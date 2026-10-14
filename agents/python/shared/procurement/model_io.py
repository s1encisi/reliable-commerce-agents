"""采购模型调用接线：显式费用许可、同一任务身份与结构化输出。"""

from __future__ import annotations

import json
from contextlib import contextmanager

from agent_framework import Agent

from shared.config import settings
from shared.factory import get_compatible_client
from shared.paid_transport import current_call_purpose, current_root_run
from shared.prompt_loader import _load_shared_file


def require_model_authorization() -> None:
    from shared.campaign_budget import BudgetError

    if not settings.PROCUREMENT_ENABLED or not settings.PROCUREMENT_MODEL_AUTHORIZED:
        raise BudgetError("采购真实模型尚未授权；请先完成提供方、有效价格与活动预算配置")


def budget_cause(exc: Exception):
    """SDK可能包装传输异常；保留预算错误语义，不把费用拒绝当成模型质量失败。"""
    from shared.campaign_budget import BudgetError

    pending = [exc]
    seen = set()
    while pending and len(seen) < 16:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, BudgetError):
            return current
        pending.extend(
            value
            for value in (getattr(current, "__cause__", None), getattr(current, "__context__", None))
            if value is not None
        )
        pending.extend(getattr(current, "exceptions", ()))
    return None


@contextmanager
def model_context(task: dict, purpose: str):
    from shared.procurement.models import current_procurement_binding

    previous = current_procurement_binding.get() or {}
    binding = {
        **previous,
        "task_id": str(task["id"]),
        "owner_email": task["owner_email"],
        "spec_revision": task.get("spec_revision", 0),
    }
    token = current_procurement_binding.set(binding)
    run_token = current_root_run.set(str(task["id"]))
    purpose_token = current_call_purpose.set(purpose)
    try:
        yield
    finally:
        current_call_purpose.reset(purpose_token)
        current_root_run.reset(run_token)
        current_procurement_binding.reset(token)


async def structured_response(task: dict, prompt_name: str, payload: dict, result_type):
    require_model_authorization()
    serialized = json.dumps({**payload, "schema": result_type.model_json_schema()}, ensure_ascii=False, default=str)
    if len(serialized.encode()) > settings.CONTEXT_MAX_BYTES:
        raise ValueError("采购关键需求与计划资料超过上下文预算；请缩短或拆分，未静默丢弃条件")
    with model_context(task, "planning"):
        agent = Agent(
            client=get_compatible_client(settings.PROCUREMENT_PLANNER_PROVIDER),
            name="procurement-planner" if "planner" in prompt_name else "procurement-parser",
            tools=[],
            instructions=_load_shared_file(prompt_name)["instructions"],
        )
        try:
            response = await agent.run(
                serialized,
                options={"max_tokens": 4096},
            )
        except Exception as exc:
            error = budget_cause(exc)
            if error is not None:
                raise error from exc
            raise

    text = (response.text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return result_type.model_validate_json(text)


async def parse_task(task: dict):
    from shared.procurement.parsing import ParseProposal, parse_offline

    if task["execution_mode"] == "offline":
        return parse_offline(task["goal"])
    result = await structured_response(task, "procurement-parser.yaml", {"goal": task["goal"]}, ParseProposal)
    result.source = "model"
    return result
