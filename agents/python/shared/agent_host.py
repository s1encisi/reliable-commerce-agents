"""承载专业智能体的轻量 A2A 兼容宿主。

所有请求统一经过 MAF 原生 agent.run() 或 agent.run(..., stream=True)。
Agent 自身持有工具、系统提示词和上下文提供器链；本模块负责将 A2A
请求转交给对应调用。旧的自定义 OpenAI 工具循环在确认原生执行路径
兼容生产 Azure 部署后已移除。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from shared.sse import encode_sse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ─────────────────────── MAF-native execution ───────────────────────


def _history_as_maf_messages(history: list[dict] | None, user_message: str) -> list[Any]:
    """将 A2A 历史和当前用户消息转换为 agent_framework.Message。

    会话、工具调用与上下文提供器由 agent.run 管理。
    """
    from agent_framework import Message

    messages: list[Any] = []
    if history:
        for entry in history:
            role = entry.get("role")
            content = entry.get("content")
            if role in ("user", "assistant") and content:
                messages.append(Message(role=role, contents=[content]))
    messages.append(Message(role="user", contents=[user_message]))
    return messages


def _run_options() -> dict[str, Any]:
    """为每次运行设置聊天选项，并通过 LLM_TEMPERATURE 固定采样温度。

    这样可减少相同问题的答案波动，但不保证模型输出完全确定。
    """
    from shared.config import settings

    if settings.LLM_PROVIDER.lower() == "moonshot":
        return {"max_tokens": 4096}
    return {"temperature": settings.LLM_TEMPERATURE}


async def _run_agent_native(
    agent: Any,
    user_message: str,
    history: list[dict] | None = None,
    metadata_box: dict[str, Any] | None = None,
) -> str:
    """使用 MAF 原生路径执行智能体并返回答案文本。

    提供 metadata_box 时，原地写入响应的 additional_properties（包括
    事实核验报告）及 _maf_usage 下的 usage_details。后者含输入、输出
    token 数；evals/harness.py 的 ProductionRunner 会消费这些附加信息。
    纯字符串返回值无法承载这部分元数据。
    """
    messages = _history_as_maf_messages(history, user_message)
    response = await agent.run(messages, options=_run_options())
    if metadata_box is not None:
        metadata_box.update(getattr(response, "additional_properties", None) or {})
        usage = getattr(response, "usage_details", None)
        if usage:
            metadata_box["_maf_usage"] = dict(usage)
    return response.text or ""


async def _run_agent_native_stream(
    agent: Any,
    user_message: str,
    history: list[dict] | None = None,
    metadata_box: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """流式执行，按 MAF 产生文本的顺序输出分块。

    流耗尽后填充 metadata_box。ResponseStream 的异步迭代会在完成时
    内部调用 get_final_response()，因此最终响应和事实核验报告已可用；
    这里再次调用只读取缓存结果。详见 shared/grounding/middleware.py。
    """
    from shared.config import settings

    if settings.VERIFIED_OUTPUT_ONLY:
        # 等最终响应钩子完成后再展示，不撤回已经发送的未核验卡片。
        text = await _run_agent_native(agent, user_message, history, metadata_box)
        if text:
            yield text
        return
    messages = _history_as_maf_messages(history, user_message)
    stream = agent.run(messages, stream=True, options=_run_options())
    async for update in stream:
        text = getattr(update, "text", None)
        if text:
            yield text
    if metadata_box is not None and hasattr(stream, "get_final_response"):
        final = await stream.get_final_response()
        metadata_box.update(getattr(final, "additional_properties", None) or {})
        usage = getattr(final, "usage_details", None)
        if usage:
            metadata_box["_maf_usage"] = dict(usage)


# ─────────────────────── Session rehydration ──────────────────────


# 上限与 PostgresSessionHistoryProvider.max_history 保持一致。
# 编排器与专业智能体宿主必须读取相同数量的历史消息。
# 修改这里时也要更新 shared/session.py，
# 否则不同执行路径会静默使用不同的上下文窗口。
_SESSION_HISTORY_LIMIT = 50


async def _rehydrate_history_from_session(session_id: str) -> list[dict] | None:
    """按 session_id（会话 UUID）获取最近消息，返回 role/content 字典。

    失败时记录日志并返回 None，让调用方退化为无历史执行。编排器只转发
    会话标识，本函数取代旧的最近 10 条、每条 500 字符的截断历史副本。

    查询必须校验当前用户归属：会话标识来自客户端，知道 UUID 不代表
    拥有访问权。所有失败路径都要记录日志，避免把读取失败伪装成空历史。
    """
    if not session_id:
        return None

    try:
        from shared.context import current_user_email
        from shared.db import get_pool
    except Exception:
        return None

    caller = current_user_email.get("")
    if not caller:
        # 匿名用户会正常走到这里，遗漏身份转发的调用方也可能如此。
        # 两种情况都记录日志，
        # 避免把缺少身份误解为会话本来没有历史。
        logger.info("session.rehydrate_skipped reason=no_identity session_id=%s", session_id)
        return None

    try:
        pool = get_pool()
    except Exception:
        return None

    try:
        # 直接在原表使用 ORDER BY ... ASC LIMIT $2 会取到最早的记录，
        # 而非最近的 _SESSION_HISTORY_LIMIT 条。
        # 长会话会因此遗漏
        # 当前追问最需要的上下文。应先取最近记录，
        # 再恢复为时间正序。
        rows = await pool.fetch(
            """
            SELECT role, content FROM (
                SELECT role, content, created_at
                FROM messages
                WHERE conversation_id = $1::uuid
                  AND EXISTS (
                      SELECT 1 FROM conversations c
                        JOIN users u ON u.id = c.user_id
                       WHERE c.id = $1::uuid AND u.email = $3
                  )
                ORDER BY created_at DESC
                LIMIT $2
            ) recent
            ORDER BY created_at ASC
            """,
            session_id,
            _SESSION_HISTORY_LIMIT,
            caller,
        )
    except Exception:
        logger.exception("session.rehydrate_failed session_id=%s", session_id)
        return None

    history: list[dict] = []
    for row in rows:
        role = row["role"]
        content = row["content"]
        if role in ("user", "assistant") and content:
            history.append({"role": role, "content": content})
    return history


# ─────────────────────── FastAPI host ───────────────────────


def create_agent_app(
    *,
    agent: Any,
    agent_name: str,
    port: int,
    description: str = "",
    tools: list | None = None,
    on_startup: Callable | None = None,
    on_shutdown: Callable | None = None,
) -> FastAPI:
    """创建提供 A2A 兼容端点的 FastAPI 应用。

    参数：
        agent：持有工具、指令及提供器的 MAF Agent。
        agent_name：与 YAML 配置文件对应的智能体标识。
        port：端口号。
        tools：仅兼容旧调用签名；实际执行 agent 持有的工具。
        on_startup/on_shutdown：生命周期回调。
    """
    del tools  # 详见函数说明。

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if on_startup:
            await on_startup(app)
        logger.info("%s.started port=%d", agent_name, port)
        yield
        if on_shutdown:
            await on_shutdown()

    app = FastAPI(title=agent_name, lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "agent": agent_name, "port": port}

    @app.get("/.well-known/agent-card.json")
    async def agent_card():
        return {
            "name": agent_name,
            "description": description,
            "url": f"http://{agent_name}:{port}",
            "version": "1.0",
        }

    @app.post("/message:send")
    async def message_send(request: Request):
        try:
            body = await request.json()
            message = body.get("message", "")
            if not message:
                return JSONResponse({"error": "No message provided"}, status_code=400)

            # 优先使用旧 A2A 调用方转发的历史；未提供时，
            # 通过会话标识从 PostgreSQL 恢复。
            history = body.get("history", None)
            if not history and not body.get("context_supplied", False):
                session_id = request.headers.get("x-session-id", "")
                if session_id:
                    history = await _rehydrate_history_from_session(session_id)

            from shared.agent_observability import get_steps, reset_steps
            from shared.execution_policy import current_policy_denials
            from shared.grounding.ledger import reset_grounding_ledger
            from shared.telemetry import agent_run_span

            denials: list[str] = []
            current_policy_denials.set(denials)
            reset_steps()
            reset_grounding_ledger()
            with agent_run_span(agent_name):
                metadata: dict = {}
                response_text = await _run_agent_native(agent, message, history=history, metadata_box=metadata)
            # 返回专业智能体捕获的工具步骤，
            # 供编排器合并到实时执行时间线。
            steps = get_steps()
            for step in steps:
                step["agent"] = agent_name
            return {
                "response": response_text,
                "steps": steps,
                "grounding": metadata.get("grounding"),
                "usage": metadata.get("_maf_usage"),
                "requires_original_route": bool(set(denials) - {"store_memory", "draft_seller_response"}),
                "policy_denials": denials,
            }

        except Exception as exc:
            from shared.campaign_budget import BudgetError

            cause = exc
            for _ in range(8):
                if isinstance(cause, BudgetError):
                    return JSONResponse(
                        {"error": "本次运行预算不足，未发起下一次模型调用", "outcome": "BUDGET_EXHAUSTED"},
                        status_code=429,
                    )
                cause = getattr(cause, "__cause__", None)
                if cause is None:
                    break
            logger.exception("%s.message_error", agent_name)
            return JSONResponse(
                {"error": "Agent processing failed"},
                status_code=500,
            )

    @app.post("/message:stream")
    async def message_stream(request: Request) -> StreamingResponse:
        """SSE 流式端点，契约与 /message:send 相同。

        编排器在流式上下文中连接此端点，实时向浏览器转发响应分块，
        无需等待专业智能体的完整答案。
        """
        try:
            body = await request.json()
            message = body.get("message", "")
            if not message:
                return StreamingResponse(
                    iter(["data: [ERROR: no message]\n\n"]),
                    media_type="text/event-stream",
                )
            history = body.get("history", None)
            if not history and not body.get("context_supplied", False):
                session_id = request.headers.get("x-session-id", "")
                if session_id:
                    history = await _rehydrate_history_from_session(session_id)
        except Exception:
            logger.exception("%s.message_stream_parse_error", agent_name)
            return StreamingResponse(
                iter(["data: [ERROR: bad request]\n\n"]),
                media_type="text/event-stream",
            )

        from shared.agent_observability import reset_steps
        from shared.grounding.ledger import reset_grounding_ledger
        from shared.telemetry import agent_run_span

        async def _generate():
            steps = reset_steps()
            reset_grounding_ledger()
            sent = 0

            def _drain_new_steps() -> list[str]:
                """输出自上次清空后新记录的全部步骤帧。

                StepRecorderMiddleware 在工具返回时追加 current_steps；尚未读取
                的尾部对应已经完成但尚未上报的调用。
                """
                nonlocal sent
                frames = []
                for step in steps[sent:]:
                    step["agent"] = agent_name
                    frames.append(f"event: step\ndata: {json.dumps(step, default=str)}\n\n")
                sent = len(steps)
                return frames

            with agent_run_span(agent_name):
                try:
                    async for chunk in _run_agent_native_stream(agent, message, history=history):
                        # 先发送步骤，再发送文本分块：
                        # MAF 工具循环先完成工具调用，
                        # 随后才生成描述该结果的文本。
                        # 这样浏览器会先收到步骤，再收到相关描述，
                        # 不会无故延迟一个文本分块。
                        for frame in _drain_new_steps():
                            yield frame
                        yield encode_sse(chunk)
                except Exception:
                    logger.exception("%s.message_stream_error", agent_name)
                    yield "data: [ERROR: agent processing failed]\n\n"

            # 处理最终文本分块之后才完成的步骤，
            # 例如模型未及描述结果的工具，或没有产生任何文本的运行。
            # 以前所有步骤都在此处统一输出，
            # 导致答案写完后时间线才突然出现，
            # 现在这里只补发剩余步骤。
            for frame in _drain_new_steps():
                yield frame
            yield "data: [DONE]\n\n"

        return StreamingResponse(_generate(), media_type="text/event-stream")

    return app
