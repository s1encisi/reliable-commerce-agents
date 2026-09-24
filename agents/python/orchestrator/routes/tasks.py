"""用户隔离的复杂查询任务与记忆确认入口。"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orchestrator.modes.decision_router import dispatch_readonly
from orchestrator.routes.legacy import require_auth
from shared.db import get_pool
from shared.paid_transport import current_root_run
from shared.task_planner import generate_plan
from shared.task_state import (
    claim_planning,
    claim_step,
    create_task,
    finish_step,
    load_task,
    planning_failed,
    save_plan,
)

router = APIRouter()


class NewTask(BaseModel):
    goal: str = Field(min_length=1, max_length=6000)
    constraints: list[str] = Field(default_factory=list, max_length=30)


class Revision(BaseModel):
    revision: int = Field(ge=0)


@router.post("/api/tasks")
async def new_task(body: NewTask, user: dict = Depends(require_auth)) -> dict:
    return await create_task(body.goal, body.constraints)


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: UUID, user: dict = Depends(require_auth)) -> dict:
    task = await load_task(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return task


@router.post("/api/tasks/{task_id}/plan")
async def plan_task(task_id: UUID, body: Revision, user: dict = Depends(require_auth)) -> dict:
    task = await get_task(task_id, user)
    claimed = await claim_planning(task_id, body.revision, replan=task["status"] == "needs_replan")
    if claimed is None:
        raise HTTPException(409, "版本冲突、规划进行中或已达到规划次数上限")
    try:
        plan = await generate_plan(claimed)
        if not await save_plan(task_id, claimed["revision"], plan):
            raise ValueError("规划保存版本冲突")
    except Exception as exc:
        await planning_failed(task_id, claimed["revision"])
        raise HTTPException(503, "规划未完成，未执行任何业务步骤") from exc
    return await get_task(task_id, user)


@router.post("/api/tasks/{task_id}/next")
async def execute_next(task_id: UUID, body: Revision, user: dict = Depends(require_auth)) -> dict:
    claimed = await claim_step(task_id, body.revision)
    if claimed is None:
        raise HTTPException(409, "任务已完成、执行中或版本不匹配")
    task, step = claimed
    token = current_root_run.set(str(task_id))
    try:
        history = [{"role": "user", "content": "约束：" + "；".join(task["constraints"])}]
        history.extend(
            {"role": "assistant", "content": task["results"][key]["result"].get("response", "")}
            for key in step.depends_on
        )
        result = await dispatch_readonly(step.service, step.instruction, history)
        await finish_step(task_id, task["revision"], step, result)
    except Exception as exc:
        # 执行中断保留 running；只读任务可经显式 recover 恢复，不影响售后写操作。
        raise HTTPException(503, "步骤结果未知，已保留执行状态供核实") from exc
    finally:
        current_root_run.reset(token)
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


@router.post("/api/tasks/{task_id}/recover")
async def recover_task(task_id: UUID, body: Revision, user: dict = Depends(require_auth)) -> dict:
    from shared.task_state import recover_readonly

    if not await recover_readonly(task_id, body.revision):
        raise HTTPException(409, "任务不能恢复或版本冲突")
    return await get_task(task_id, user)
