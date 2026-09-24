"""编排器智能体定义 —— 通过 A2A 把请求路由到各专业智能体。"""

from __future__ import annotations

import json
import logging
from typing import Annotated

import httpx
from agent_framework import Agent, tool
from pydantic import Field

from orchestrator.prompts import get_system_prompt
from shared.agent_factory import create_chat_client
from shared.config import settings
from shared.context import (
    current_steps,
    current_stream_queue,
    current_user_email,
    current_user_role,
)
from shared.context_providers import ECommerceContextProvider
from shared.factory import parse_agent_registry
from shared.http_resilience import ResilientAsyncTransport
from shared.middleware import build_specialist_middleware
from shared.oauth.service_client import build_a2a_headers
from shared.telemetry import a2a_call_span

logger = logging.getLogger(__name__)

# 经过校验，而不仅仅是解码：shared.factory.parse_agent_registry 会在导入时
# 拒绝空白或缺少 scheme 的端点，而不是让它在第一个需要它的请求上暴露为
# 一个无法路由的智能体。
AGENT_REGISTRY: dict[str, str] = parse_agent_registry(settings.AGENT_REGISTRY)

# 一个共享的传输层（因而是它的按主机断路器）在下面构造的每个
# httpx.AsyncClient 之间复用 —— 断路器只有在跨调用记住失败时才有意义，
# 而每次调用都新建一个 ResilientAsyncTransport() 会每次重置那份记忆。
# 在多个短生命周期的 AsyncClient 实例之间共享是安全的：关闭一个 client 会
# 关闭其传输层的连接池，但连接池会在下一个请求时惰性重开而不是抛错 ——
# 在依赖这一点之前已直接对照 httpx 的当前行为验证过。
_A2A_TRANSPORT = ResilientAsyncTransport()


@tool(
    name="call_specialist_agent",
    description=(
        "Route a request to a specialist agent via A2A protocol. "
        "Available agents: product-discovery, order-management, "
        "pricing-promotions, review-sentiment, inventory-fulfillment"
    ),
)
async def call_specialist_agent(
    agent_name: Annotated[str, Field(description="Name of the specialist agent to call")],
    message: Annotated[str, Field(description="The message/request to send to the specialist agent")],
) -> str:
    """调用一个专业智能体并返回其响应。"""
    url = AGENT_REGISTRY.get(agent_name)
    if not url:
        available = ", ".join(AGENT_REGISTRY.keys()) if AGENT_REGISTRY else "none configured"
        return f"Unknown agent: {agent_name}. Available agents: {available}"

    logger.info("a2a.call source=orchestrator target=%s user=%s", agent_name, current_user_email.get())

    # 审计修复 #14：不再在每次 A2A 调用时转发一份被截断的对话历史副本。
    # 会话 id 通过请求头传递；专业智能体一侧在需要先前上下文时，会经由
    # shared.agent_host 从 Postgres 重新水合。去掉这个载荷让我们摆脱了
    # 那个 10 条消息 / 500 字符的窗口 —— 它此前会在长对话中静默地丢失上下文。

    stream_queue = current_stream_queue.get()
    headers = await build_a2a_headers()
    from shared.execution_policy import current_execution_policy
    from shared.paid_transport import current_root_run, current_run_deadline

    if current_root_run.get():
        headers["X-Root-Run-Id"] = current_root_run.get()
    headers["X-Execution-Policy"] = current_execution_policy.get()
    if current_run_deadline.get() is not None:
        headers["X-Run-Deadline"] = str(current_run_deadline.get())
    from shared.after_sales.operations import current_operation_id

    if current_operation_id.get():
        headers["X-Return-Operation-Id"] = current_operation_id.get()
    request_body = {"message": message}

    with a2a_call_span("orchestrator", agent_name, url):
        # ── 流式路径 ───────────────────────────────────────────────────────
        # 当 SSE 上下文处于活动状态（stream_queue 已设置）时，连接到专业
        # 智能体的 /message:stream 端点，并在响应分片到达时立即转发给浏览器
        # —— 从而消除专业智能体的 LLM 生成响应期间那段静默空白。
        if stream_queue is not None:
            try:
                chunks: list[str] = []
                current_event: str = "data"
                async with httpx.AsyncClient(timeout=60, transport=_A2A_TRANSPORT) as client:
                    async with client.stream(
                        "POST",
                        f"{url}/message:stream",
                        json=request_body,
                        headers=headers,
                    ) as resp:
                        resp.raise_for_status()
                        from shared.sse import iter_sse

                        async for current_event, payload in iter_sse(resp.aiter_lines()):
                            if current_event == "step":
                                # 把专业智能体的工具调用步骤合并进本次请求共享的
                                # current_steps，并立即转发给浏览器，而不是留给
                                # 流结束后的排空阶段去上报。
                                #
                                # 专业智能体在每个工具返回时就发出这些步骤，
                                # 因此此刻正是该步骤最新鲜的时候 —— 一直留到
                                # 运行结束再发，会让整条时间线在答案写完
                                # 之后一次性出现，而那恰恰是它最没用的时候。
                                try:
                                    step_data = json.loads(payload)
                                    bucket = current_steps.get()
                                    if bucket is not None:
                                        bucket.append(step_data)
                                    # `_live` 标记该步骤已经发送过。
                                    # chat.py 的排空逻辑会把它弹出，因此它既不会
                                    # 重复发出，也不会作为传输细节进入持久化元数据。
                                    step_data["_live"] = True
                                    await stream_queue.put(("frame", "step", step_data))
                                except (json.JSONDecodeError, ValueError):
                                    pass
                                current_event = "data"
                                continue
                            if payload == "[DONE]":
                                break
                            if payload.startswith("[ERROR"):
                                logger.error("a2a.stream_error target=%s payload=%s", agent_name, payload)
                                continue
                            chunks.append(payload)
                            await stream_queue.put(("delta", agent_name, payload))
                return "".join(chunks) or f"The {agent_name} agent returned an empty response."
            except (httpx.TimeoutException, httpx.HTTPStatusError, Exception) as exc:
                logger.warning("a2a.stream_fallback target=%s reason=%s", agent_name, type(exc).__name__)
                # 只有明确的端点不存在才可安全切换；超时或断流可能已执行工具。
                if not (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {404, 405}):
                    return "专业智能体连接中断，执行结果待核实；系统没有重新发送业务请求。"
                # 404/405 表示此流式端点没有处理该业务请求。

        # ── 阻塞式路径（非流式或流式回退） ────────────────────────────────
        try:
            async with httpx.AsyncClient(timeout=30, transport=_A2A_TRANSPORT) as client:
                resp = await client.post(
                    f"{url}/message:send",
                    json=request_body,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                bucket = current_steps.get()
                specialist_steps = data.get("steps") or []
                if bucket is not None and specialist_steps:
                    bucket.extend(specialist_steps)
                return data.get("response", resp.text)
        except httpx.TimeoutException:
            logger.error("a2a.timeout target=%s", agent_name)
            return f"The {agent_name} agent took too long to respond. Please try again."
        except httpx.HTTPStatusError as e:
            logger.error("a2a.error target=%s status=%s", agent_name, e.response.status_code)
            return f"The {agent_name} agent returned an error (status {e.response.status_code}). Please try again."
        except Exception:
            logger.exception("a2a.failure target=%s", agent_name)
            return f"Failed to reach the {agent_name} agent. Please try again later."


# 导出工具列表，供 routes.py 直接使用（绕过 MAF Responses API）
ORCHESTRATOR_TOOLS = [call_specialist_agent]


def create_orchestrator_agent() -> Agent:
    """创建客服编排器 ChatAgent。"""
    return Agent(
        client=create_chat_client(),
        name="orchestrator",
        description="Customer support orchestrator that routes requests to specialist agents.",
        instructions=get_system_prompt(current_user_role.get() or "customer"),
        tools=ORCHESTRATOR_TOOLS,
        context_providers=[ECommerceContextProvider()],
        middleware=build_specialist_middleware(),
        # 目前没有挂任何 HistoryProvider，所以这一项目前是空操作 ——
        # 但 agent-framework-orchestrations>=1.0.1 要求每个 HandoffBuilder
        # 参与者都带上它（见 orchestrator/handoff.py），而且它也是日后把
        # shared/session.py 的 HistoryProvider 接到本智能体上的前提条件。
        # 无条件设置（而不是只在处理权交接路径上设置），以保持两个调用点一致。
        require_per_service_call_history_persistence=True,
    )
