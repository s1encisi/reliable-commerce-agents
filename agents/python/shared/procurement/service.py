"""专业服务采购入口；仅可信协调服务可调用，模型不能伪造取证结果。"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shared.config import settings
from shared.context import current_user_email
from shared.db import get_pool
from shared.procurement.models import Plan, ProcurementSpec, current_procurement_binding
from shared.procurement.planning import CAPABILITY_SERVICES


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: UUID
    plan_revision: int = Field(ge=1)
    step_id: str = Field(min_length=1, max_length=40)
    capability: str
    inputs: dict
    binding: dict = Field(default_factory=dict)


async def execute_bound(task: dict, capability: str, inputs: dict) -> dict:
    from shared.procurement.domain import DomainError, execute_capability

    spec = ProcurementSpec.model_validate(task["requirements"]).model_dump(mode="json")
    if task["execution_mode"] == "offline":
        data = await execute_capability(get_pool(), capability, spec, inputs, task["owner_email"])
        return {"data": data, "source": "deterministic_tools", "response": "离线工具查询；没有调用模型。"}
    from agent_framework import Agent, tool
    from agent_framework._middleware import FunctionMiddleware, MiddlewareTermination

    from shared.factory import get_compatible_client
    from shared.procurement.model_io import budget_cause, model_context, require_model_authorization
    from shared.prompt_loader import _load_shared_file

    require_model_authorization()
    captured = []
    captured_errors = []
    capture_lock = asyncio.Lock()

    @tool(name=capability, description="执行本次已绑定的采购只读能力；商品及需求由服务端确定，不接受覆盖参数。")
    async def bound_tool() -> dict:
        async with capture_lock:
            if captured_errors:
                raise captured_errors[0]
            if not captured:
                try:
                    captured.append(await execute_capability(get_pool(), capability, spec, inputs, task["owner_email"]))
                except DomainError as exc:
                    captured_errors.append(exc)
                    raise
            return captured[0]

    class ReturnEvidence(FunctionMiddleware):
        async def process(self, context, call_next):
            try:
                await call_next()
            except Exception:
                if not captured_errors:
                    raise
            if captured_errors:
                # 已知业务失败直接交回修复器，不能被SDK转成普通文本后丢失SKU与错误类别。
                error = captured_errors[0]
                raise MiddlewareTermination(
                    result={"error_code": error.code, "affected_product_ids": error.product_ids}
                )
            if captured:
                # 最终事实由协调器验收，无需再付费让模型复述同一工具结果。
                # 使用SDK正式终止机制，保留真实工具回执与本轮用量。
                raise MiddlewareTermination(result=context.result)

    with model_context(task, "execution"):
        client = get_compatible_client(settings.PROCUREMENT_EXECUTOR_PROVIDER)
        client.function_invocation_configuration.update(
            max_iterations=1, max_function_calls=1, terminate_on_unknown_calls=True
        )
        specialist = Agent(
            client=client,
            name=CAPABILITY_SERVICES[capability],
            tools=[bound_tool],
            middleware=[ReturnEvidence()],
            instructions=_load_shared_file("procurement-specialist.yaml")["instructions"],
        )
        try:
            response = await specialist.run(
                json.dumps(
                    {"capability": capability, "confirmed_requirements": spec, "bound_inputs": inputs},
                    ensure_ascii=False,
                ),
                options={"max_tokens": 2048},
            )
        except Exception as exc:
            error = budget_cause(exc)
            if error is not None:
                raise error from exc
            raise

    if captured_errors:
        raise captured_errors[0]
    if not captured:
        raise HTTPException(422, "专业模型未实际调用取证工具，回答不能作为完成证据")
    return {"data": captured[0], "source": "model_tool_execution", "response": (response.text or "")[:4000]}


async def handle_request(request: Request, agent_name: str):
    from shared.campaign_budget import BudgetError
    from shared.procurement import state
    from shared.procurement.domain import DomainError

    if not getattr(request.state, "is_agent_request", False):
        raise HTTPException(403, "采购专业入口仅接受经认证的协调服务请求")
    if not settings.PROCUREMENT_ENABLED:
        raise HTTPException(403, "采购运行时未启用")
    if settings.MCP_ENABLED:
        raise HTTPException(409, "当前采购能力使用原生工具配置，尚未验证MCP等价能力")
    try:
        body = ExecutionRequest.model_validate(await request.json())
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, "采购专业请求格式非法") from exc
    if CAPABILITY_SERVICES.get(body.capability) != agent_name:
        raise HTTPException(403, "该能力不属于当前专业服务")
    email = current_user_email.get("")
    task = await state.get_task(get_pool(), body.task_id, email)
    if task is None:
        raise HTTPException(404, "采购任务不存在")
    if task["plan_revision"] != body.plan_revision or task["status"] not in {"running", "pausing"}:
        raise HTTPException(409, "采购计划已失效或当前没有运行")
    plan = Plan.model_validate(task["plan"])
    saved = next((step for step in plan.steps if step.id == body.step_id), None)
    if saved is None or saved.capability != body.capability:
        raise HTTPException(409, "专业请求与数据库计划不一致")
    if body.binding.get("generation") != task["procurement_generation"]:
        raise HTTPException(409, "旧执行代次已失效")
    # 重新从DB读取实际父步骤，避免客户端给专业Agent注入其他SKU。
    from shared.procurement.dispatch import step_inputs
    from shared.procurement.models import StepOutcome

    dependencies = {
        row["step_id"]: StepOutcome.model_validate(row["outcome"])
        for row in task["steps"]
        if row["step_id"] in saved.depends_on and row.get("outcome")
    }
    expected_inputs = step_inputs(task, saved, dependencies)
    if body.inputs != expected_inputs:
        raise HTTPException(409, "专业输入与已保存上游证据不一致")
    token = current_procurement_binding.set(
        {
            **body.binding,
            "task_id": str(body.task_id),
            "owner_email": email,
            "spec_revision": task["spec_revision"],
        }
    )
    try:
        return await execute_bound(task, body.capability, expected_inputs)
    except BudgetError as exc:
        raise HTTPException(429, str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(
            422, {"code": exc.code, "message": str(exc), "affected_product_ids": exc.product_ids}
        ) from exc
    finally:
        current_procurement_binding.reset(token)
