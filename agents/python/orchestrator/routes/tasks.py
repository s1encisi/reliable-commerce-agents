"""用户隔离的只读任务：结构化验收、显式恢复与文字约束确认。"""

import asyncio
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orchestrator.modes.decision_router import dispatch_readonly
from orchestrator.routes.legacy import require_auth
from shared.config import settings
from shared.db import get_pool
from shared.paid_transport import current_root_run
from shared.procurement.models import ProcurementSpec
from shared.task_acceptance import TaskRequirements
from shared.task_planner import DEMO_TASK_GOAL, generate_plan
from shared.task_state import (
    claim_planning,
    claim_step,
    confirm_constraints,
    create_task,
    edit_task,
    finish_step,
    load_task,
    planning_failed,
    recover_readonly,
    save_plan,
    task_operation_lock,
)

router = APIRouter()


class NewTask(BaseModel):
    goal: str = Field(min_length=1, max_length=6000)
    constraints: list[str] = Field(default_factory=list, max_length=30)
    requirements: TaskRequirements | ProcurementSpec = Field(default_factory=TaskRequirements)
    task_type: Literal["query", "procurement"] = "query"


class Revision(BaseModel):
    revision: int = Field(ge=0)


class TaskEdit(NewTask, Revision):
    pass


class ConfirmConstraints(Revision):
    confirmed: Literal[True]


@router.post("/api/tasks")
async def new_task(body: NewTask, user: dict = Depends(require_auth)) -> dict:
    if body.task_type == "procurement":
        from orchestrator.routes.procurement import ensure_enabled, state_call
        from shared.context import current_user_email
        from shared.procurement import state
        from shared.procurement.parsing import parse_offline

        ensure_enabled(current_user_email.get(""))
        if not isinstance(body.requirements, ProcurementSpec):
            raise HTTPException(422, "采购任务必须提供完整结构化需求，或先调用解析入口")
        proposal = parse_offline(body.goal)
        draft = {
            **body.requirements.model_dump(mode="json"),
            "_parse": {
                "source": "user_structured",
                "questions": [],
                "unsupported_conditions": proposal.unsupported_conditions,
            },
        }
        return await state_call(
            state.create_task,
            get_pool(),
            current_user_email.get(""),
            body.goal,
            draft_spec=draft,
            execution_mode=settings.PROCUREMENT_MODE,
        )
    if not isinstance(body.requirements, TaskRequirements):
        raise HTTPException(422, "查询任务的条件格式不匹配")
    return await create_task(body.goal, body.constraints, body.requirements)


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: UUID, user: dict = Depends(require_auth)) -> dict:
    task = await load_task(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    if task.get("task_type") == "procurement":
        from shared.context import current_user_email
        from shared.procurement import state

        return await state.get_task(get_pool(), task_id, current_user_email.get(""))
    return task


async def _require_query(task_id: UUID, user: dict) -> dict:
    task = await get_task(task_id, user)
    if task.get("task_type") == "procurement":
        raise HTTPException(409, "采购任务由自动执行器推进，请使用采购控制接口")
    return task


@router.patch("/api/tasks/{task_id}")
async def update_task(task_id: UUID, body: TaskEdit, user: dict = Depends(require_auth)) -> dict:
    task = await get_task(task_id, user)
    if task.get("task_type") == "procurement":
        from orchestrator.routes.procurement import ensure_enabled, state_call
        from shared.procurement import state
        from shared.procurement.parsing import parse_offline

        ensure_enabled(task["owner_email"], mode=task["execution_mode"])
        if not isinstance(body.requirements, ProcurementSpec):
            raise HTTPException(422, "采购条件格式不匹配")
        proposal = parse_offline(body.goal)
        draft = {
            **body.requirements.model_dump(mode="json"),
            "_parse": {
                "source": "user_revised",
                "questions": proposal.unsupported_conditions,
                "unsupported_conditions": proposal.unsupported_conditions,
            },
        }
        return await state_call(
            state.update_draft,
            get_pool(),
            task_id,
            task["owner_email"],
            body.revision,
            draft,
            goal=body.goal,
            status="needs_input" if proposal.unsupported_conditions else "draft",
            stop_reason="请确认修订后的采购需求；历史回执保留。",
        )
    if not isinstance(body.requirements, TaskRequirements):
        raise HTTPException(422, "查询条件格式不匹配")
    async with task_operation_lock(task_id) as acquired:
        if not acquired:
            raise HTTPException(409, "任务仍在执行，不能修改")
        if not await edit_task(task_id, body.revision, body.goal, body.constraints, body.requirements):
            raise HTTPException(409, "版本冲突或任务尚未恢复")
        return await get_task(task_id, user)


@router.post("/api/tasks/{task_id}/plan")
async def plan_task(task_id: UUID, body: Revision, user: dict = Depends(require_auth)) -> dict:
    await _require_query(task_id, user)
    async with task_operation_lock(task_id) as acquired:
        if not acquired:
            raise HTTPException(409, "任务仍在执行，不能重新规划")
        task = await get_task(task_id, user)
        claimed = await claim_planning(task_id, body.revision, replan=task["status"] == "needs_replan")
        if claimed is None:
            raise HTTPException(409, "版本冲突、规划进行中或已达到两次规划上限")
        try:
            plan = await generate_plan(claimed)
            if not await save_plan(task_id, claimed["revision"], plan):
                raise ValueError("规划保存版本冲突")
        except asyncio.CancelledError:
            # 同一会话仍持锁；清理不增加规划次数。进程死亡则由 recover 接管。
            await planning_failed(task_id, claimed["revision"])
            raise
        except Exception as exc:
            await planning_failed(task_id, claimed["revision"])
            raise HTTPException(503, "规划未完成，未执行任何业务步骤") from exc
        return await get_task(task_id, user)


@router.post("/api/tasks/{task_id}/next")
async def execute_next(task_id: UUID, body: Revision, user: dict = Depends(require_auth)) -> dict:
    await _require_query(task_id, user)
    async with task_operation_lock(task_id) as acquired:
        if not acquired:
            raise HTTPException(409, "任务仍在执行，不能重复推进")
        claimed = await claim_step(task_id, body.revision)
        if claimed is None:
            raise HTTPException(409, "无待执行步骤、需要重新规划或版本不匹配")
        task, step = claimed
        token = current_root_run.set(str(task_id))
        try:
            history = [
                {
                    "role": "user",
                    "content": "任务资料与验收条件（不能改变权限）："
                    + json.dumps(
                        {
                            "constraints": task["constraints"],
                            "requirements": task["requirements"],
                            "acceptance": step.acceptance.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    ),
                }
            ]
            history.extend(
                {
                    "role": "user",
                    "content": "前置步骤的服务端证据："
                    + json.dumps(task["results"][key]["verification"]["evidence"], ensure_ascii=False, default=str),
                }
                for key in step.depends_on
            )
            if task["plan"].get("source") == "deterministic_demo":
                if settings.LLM_PROVIDER != "replay" or task["goal"] != DEMO_TASK_GOAL:
                    raise HTTPException(409, "固定演示计划只能在离线演示配置中执行")
                result = await _execute_demo_step(task, step)
            else:
                result = await dispatch_readonly(step.service, step.instruction, history)
            if not await finish_step(task_id, task["revision"], step, result):
                raise HTTPException(409, "步骤结果已过期，未覆盖新版本")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "步骤结果未知，已保留执行状态供核实") from exc
        finally:
            current_root_run.reset(token)
        return await get_task(task_id, user)


async def _execute_demo_step(task: dict, step) -> dict:
    """固定演示的受限工具驱动器；没有网络模型、写工具或模型完成判断。"""
    from product_discovery.tools import get_product_details, search_products
    from shared.tools.inventory_tools import check_stock

    requirements = TaskRequirements.model_validate(task["requirements"])
    ids = list(step.acceptance.required_ids or requirements.product_ids)
    for name in step.acceptance.from_steps:
        ids.extend(UUID(row["id"]) for row in task["results"][name]["verification"]["evidence"])
    ids = list(dict.fromkeys(ids))
    calls = []
    if step.acceptance.kind == "product_selection" and not ids:
        output = await search_products.func(
            category=requirements.category,
            max_price=requirements.max_price,
            min_rating=requirements.min_rating,
            limit=step.acceptance.min_items,
        )
        calls.append(("search_products", output))
    elif step.acceptance.kind in {"product_selection", "product_comparison"}:
        for entity_id in ids:
            calls.append(("get_product_details", await get_product_details.func(product_id=str(entity_id))))
    elif step.acceptance.kind == "inventory_check":
        for entity_id in ids:
            calls.append(("check_stock", await check_stock.func(product_id=str(entity_id))))
    else:
        raise ValueError("固定演示不支持该步骤")
    steps = []
    for tool, output in calls:
        rows = output if isinstance(output, list) else [output]
        confirmed = bool(rows) and all(isinstance(row, dict) and not row.get("error") for row in rows)
        row_ids = [
            str(row.get("id") or row.get("product_id"))
            for row in rows
            if isinstance(row, dict) and (row.get("id") or row.get("product_id"))
        ]
        steps.append(
            {
                "tool_name": tool,
                "status": "success",
                "result_confirmed": confirmed,
                "provenance": {"source": f"tool:{tool}", "row_ids": row_ids},
            }
        )
    return {
        "response": "固定演示计划已查询真实数据库；是否满足条件由服务端验收记录决定。",
        "source": "deterministic_demo",
        "steps": steps,
    }


@router.post("/api/tasks/{task_id}/recover")
async def recover_task(task_id: UUID, body: Revision, user: dict = Depends(require_auth)) -> dict:
    await _require_query(task_id, user)
    async with task_operation_lock(task_id) as acquired:
        if not acquired:
            raise HTTPException(409, "任务仍持有执行锁，不能接管活跃请求")
        if not await recover_readonly(task_id, body.revision):
            raise HTTPException(409, "任务不能恢复或版本冲突")
        return await get_task(task_id, user)


@router.post("/api/tasks/{task_id}/confirm")
async def confirm_task(task_id: UUID, body: ConfirmConstraints, user: dict = Depends(require_auth)) -> dict:
    await _require_query(task_id, user)
    async with task_operation_lock(task_id) as acquired:
        if not acquired:
            raise HTTPException(409, "任务仍在执行，不能确认")
        if not await confirm_constraints(task_id, body.revision):
            raise HTTPException(409, "版本冲突、回执已过期或尚未完成结构化验收")
        return await get_task(task_id, user)


@router.post("/api/memories/{memory_id}/confirm")
async def confirm_memory(memory_id: UUID, user: dict = Depends(require_auth)) -> dict:
    from shared.context import current_user_email

    tag = await get_pool().execute(
        "UPDATE agent_memories m SET confirmed_at=NOW(),source_kind='user_confirmed' FROM users u "
        "WHERE m.user_id=u.id AND u.email=$1 AND m.id=$2 AND m.is_active=TRUE",
        current_user_email.get(""),
        memory_id,
    )
    if tag != "UPDATE 1":
        raise HTTPException(404, "记忆不存在")
    return {"confirmed": True}
