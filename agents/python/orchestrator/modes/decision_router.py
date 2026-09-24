"""可关闭的 Jev 路由；短路径只能查询，所有写操作仍由既有业务流程负责。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import asdict
from uuid import uuid4

import httpx

from orchestrator.events import OrchestrationEvent, adapt_step
from orchestrator.modes.base import ModeCapabilities, RunContext
from orchestrator.modes.tool_router import ToolRouterMode
from shared.config import settings
from shared.context_pipeline import ContextOverflowError, persistent_context
from shared.factory import parse_agent_registry
from shared.jev.async_client import Decision, decide_route
from shared.oauth.service_client import build_a2a_headers
from shared.paid_transport import current_root_run, current_run_deadline


async def dispatch_readonly(route: str, message: str, history: list[dict[str, str]]) -> dict:
    registry = parse_agent_registry(settings.AGENT_REGISTRY)
    if route not in registry:
        raise ValueError("目标服务已不再可用")
    headers = await build_a2a_headers()
    headers.update({"X-Execution-Policy": "read_only", "X-Root-Run-Id": current_root_run.get()})
    if current_run_deadline.get() is not None:
        headers["X-Run-Deadline"] = str(current_run_deadline.get())
    # 不叠加外层重试；收到结果前断开时不猜测另一条请求是否安全。
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
        response = await client.post(
            f"{registry[route]}/message:send",
            headers=headers,
            json={"message": message, "history": history, "context_supplied": True},
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("response"), str):
            raise ValueError("专业服务返回非法结果")
        return data


class DecisionRouterMode:
    name = "decision-router"
    label = "受控决策路由"
    description = "Jev 仅选择专业服务；代码限制只读工具，未知或失败时保留明确状态。"
    capabilities = ModeCapabilities(streams=True, supports_hitl=True)

    def graph_mermaid(self) -> str | None:
        return None

    async def run(self, message: str, ctx: RunContext) -> AsyncIterator[OrchestrationEvent]:
        mode = settings.DECISION_ROUTING_MODE
        if mode not in {"off", "shadow", "active"}:
            raise ValueError("DECISION_ROUTING_MODE 必须为 off、shadow 或 active")
        token = current_root_run.set(current_root_run.get() or str(uuid4()))
        task: asyncio.Task | None = None
        info: dict = {"mode": mode, "context_enabled": settings.DECISION_CONTEXT_ENABLED}
        history = ctx.history
        try:
            context = (
                await persistent_context(message, history, ctx.conversation_id, max_bytes=settings.CONTEXT_MAX_BYTES)
                if settings.DECISION_CONTEXT_ENABLED
                else None
            )
            if context:
                history = context.messages()
            state = {"latest_message": message, "relevant_history": history}
            decision: Decision | None = None
            if mode != "off":
                task = asyncio.create_task(decide_route(state, set(parse_agent_registry(settings.AGENT_REGISTRY))))
                if mode == "active":
                    try:
                        decision = await task
                        info.update(asdict(decision))
                    except Exception as exc:
                        info["fallback_reason"] = type(exc).__name__
            if (
                decision
                and decision.route != "defer"
                and decision.probabilities[decision.route] >= settings.DECISION_MIN_PROBABILITY
            ):
                try:
                    result = await dispatch_readonly(decision.route, message, history)
                except Exception as exc:
                    # 业务请求已经发出：不能再调用原编排器来重复执行同一意图。
                    info["execution_error"] = type(exc).__name__
                    yield OrchestrationEvent(
                        kind="run_completed",
                        payload={
                            "text": "专业服务结果未确认，系统没有重新发送请求。",
                            "agents_involved": [decision.route],
                            "decision": info,
                            "outcome": "UNKNOWN",
                        },
                    )
                    return
                if result.get("requires_original_route"):
                    info["fallback_reason"] = "requires_original_route"
                    info["policy_denials"] = result.get("policy_denials", [])
                else:
                    steps = result.get("steps", [])
                    for step in steps:
                        yield adapt_step(step)
                    yield OrchestrationEvent(
                        kind="run_completed",
                        payload={
                            "text": result["response"],
                            "agents_involved": [decision.route],
                            "steps": steps,
                            "grounding": result.get("grounding"),
                            "usage": result.get("usage"),
                            "decision": info,
                            "outcome": result.get("outcome"),
                        },
                    )
                    return
            if decision and "fallback_reason" not in info:
                info["fallback_reason"] = "defer" if decision.route == "defer" else "below_threshold"
            fallback_ctx = RunContext(history=history, conversation_id=ctx.conversation_id)
            async for event in ToolRouterMode().run(message, fallback_ctx):
                if event.kind == "run_completed":
                    if mode == "shadow" and task is not None:
                        if task.done() and not task.cancelled():
                            try:
                                info.update(asdict(task.result()))
                            except Exception as exc:
                                info["shadow_error"] = type(exc).__name__
                        else:
                            info["shadow_error"] = "not_completed_before_primary"
                    event.payload["decision"] = info
                yield event
        except ContextOverflowError as exc:
            yield OrchestrationEvent(
                kind="run_completed",
                payload={"text": str(exc), "agents_involved": [], "decision": info, "outcome": "NEEDS_INPUT"},
            )
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            current_root_run.reset(token)
