"""版本化任务状态：先原子认领，再调用外部服务；已完成步骤不重复执行。"""

from __future__ import annotations

import json
import time
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.context import current_user_email
from shared.db import get_pool

SERVICES = {"product-discovery", "order-management", "pricing-promotions", "review-sentiment", "inventory-fulfillment"}


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    service: Literal[
        "product-discovery", "order-management", "pricing-promotions", "review-sentiment", "inventory-fulfillment"
    ]
    instruction: str = Field(min_length=1, max_length=2000)
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    done_when: Literal["successful_tool_result", "verified_claim"] = "successful_tool_result"


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str
    constraints: list[str]
    steps: list[PlanStep] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_dependencies(self) -> TaskPlan:
        seen = set()
        for step in self.steps:
            if step.id in seen or step.service not in SERVICES or not set(step.depends_on) <= seen:
                raise ValueError("步骤标识、服务或依赖关系不合法")
            seen.add(step.id)
        return self


def unpack(row) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    for key in ("constraints", "plan", "results", "receipt_history"):
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    return result


async def create_task(goal: str, constraints: list[str]) -> dict:
    pool = get_pool()
    task_id = uuid4()
    row = await pool.fetchrow(
        "INSERT INTO agent_tasks(id,user_id,goal,constraints) SELECT $1,id,$2,$3::jsonb FROM users "
        "WHERE email=$4 RETURNING *",
        task_id,
        goal,
        json.dumps(constraints),
        current_user_email.get(""),
    )
    if row is None:
        raise PermissionError("用户不存在")
    return unpack(row)


async def load_task(task_id: UUID) -> dict | None:
    return unpack(
        await get_pool().fetchrow(
            "SELECT t.* FROM agent_tasks t JOIN users u ON u.id=t.user_id WHERE t.id=$1 AND u.email=$2",
            task_id,
            current_user_email.get(""),
        )
    )


async def claim_planning(task_id: UUID, revision: int, *, replan: bool = False) -> dict | None:
    allowed = ["needs_replan"] if replan else ["pending"]
    return unpack(
        await get_pool().fetchrow(
            "UPDATE agent_tasks t SET status='planning',planning_attempts=planning_attempts+1, "
            "revision=revision+1,updated_at=NOW() "
            "FROM users u WHERE t.user_id=u.id AND u.email=$1 AND t.id=$2 AND t.revision=$3 "
            "AND t.status=ANY($4::text[]) AND t.planning_attempts<2 RETURNING t.*",
            current_user_email.get(""),
            task_id,
            revision,
            allowed,
        )
    )


async def save_plan(task_id: UUID, revision: int, plan: TaskPlan) -> bool:
    task = await load_task(task_id)
    if task is None or plan.goal != task["goal"] or plan.constraints != task["constraints"]:
        raise ValueError("规划不能改写目标或硬约束")
    # 修订计划也不能抹去已验证完成的步骤。
    done = set(task["results"])
    if not done <= {step.id for step in plan.steps}:
        raise ValueError("重规划遗漏已完成步骤")
    if task.get("plan"):
        old_steps = {s["id"]: s for s in task["plan"]["steps"]}
        new_steps = {s.id: s.model_dump() for s in plan.steps}
        if any(old_steps.get(key) != new_steps.get(key) for key in done):
            raise ValueError("重规划不能改写已完成步骤的含义")
    tag = await get_pool().execute(
        "UPDATE agent_tasks t SET plan=$1::jsonb,status='ready',revision=revision+1,updated_at=NOW() FROM users u "
        "WHERE t.user_id=u.id AND u.email=$2 AND t.id=$3 AND t.revision=$4 AND t.status='planning'",
        plan.model_dump_json(),
        current_user_email.get(""),
        task_id,
        revision,
    )
    return tag == "UPDATE 1"


async def claim_step(task_id: UUID, revision: int) -> tuple[dict, PlanStep] | None:
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT t.* FROM agent_tasks t JOIN users u ON u.id=t.user_id WHERE t.id=$1 AND u.email=$2 FOR UPDATE OF t",
            task_id,
            current_user_email.get(""),
        )
        task = unpack(row)
        if not task or task["revision"] != revision or task["status"] not in {"ready", "completed"}:
            return None
        plan = TaskPlan.model_validate(task["plan"])
        usable = {key for key, receipt in task["results"].items() if receipt.get("valid_until", 0) > time.time()}
        for step in plan.steps:
            if not set(step.depends_on) <= usable:
                usable.discard(step.id)
        stale = {key: receipt for key, receipt in task["results"].items() if key not in usable}
        if stale:
            history = task.get("receipt_history", []) + [{"invalidated_at": time.time(), "receipts": stale}]
            task["results"] = {key: value for key, value in task["results"].items() if key in usable}
            await conn.execute(
                "UPDATE agent_tasks SET results=$2::jsonb,receipt_history=$3::jsonb WHERE id=$1",
                task_id,
                json.dumps(task["results"], default=str),
                json.dumps(history, default=str),
            )
        ready = [s for s in plan.steps if s.id not in task["results"] and set(s.depends_on) <= set(task["results"])]
        if not ready:
            return None
        step = ready[0]
        await conn.execute(
            "UPDATE agent_tasks SET status='running',active_step=$2,revision=revision+1 WHERE id=$1", task_id, step.id
        )
        task["revision"] += 1
        return task, step


def successful_receipt(result: dict, condition: str) -> bool:
    if result.get("requires_original_route"):
        return False
    if condition == "verified_claim":
        report = result.get("grounding") or {}
        return report.get("verified", 0) > 0 and report.get("unverified", 1) == 0
    steps = result.get("steps") or []
    return any(s.get("status") == "success" and s.get("result_confirmed") is True for s in steps)


async def finish_step(task_id: UUID, revision: int, step: PlanStep, result: dict) -> bool:
    verified = successful_receipt(result, step.done_when)
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT t.* FROM agent_tasks t JOIN users u ON u.id=t.user_id "
            "WHERE t.id=$1 AND u.email=$2 AND t.revision=$3 AND t.status='running' AND active_step=$4 FOR UPDATE OF t",
            task_id,
            current_user_email.get(""),
            revision,
            step.id,
        )
        task = unpack(row)
        if not task:
            return False
        results = task["results"]
        status = "needs_replan"
        if verified:
            results[step.id] = {
                "receipt_id": str(uuid4()),
                "verified_at": time.time(),
                "valid_until": time.time() + 300,
                "result": result,
            }
            status = "completed" if len(results) == len(task["plan"]["steps"]) else "ready"
        await conn.execute(
            (
                "UPDATE agent_tasks SET "
                "results=$2::jsonb,status=$3,active_step=NULL,revision=revision+1,updated_at=NOW() WHERE id=$1"
            ),
            task_id,
            json.dumps(results, default=str),
            status,
        )
        return True


async def planning_failed(task_id: UUID, revision: int) -> None:
    await get_pool().execute(
        "UPDATE agent_tasks t SET status='needs_replan',revision=revision+1 FROM users u "
        "WHERE t.user_id=u.id AND u.email=$1 AND t.id=$2 AND t.revision=$3 AND t.status='planning'",
        current_user_email.get(""),
        task_id,
        revision,
    )


async def recover_readonly(task_id: UUID, revision: int) -> bool:
    """显式恢复中断的只读步骤；不重做已有回执，不适用于售后写操作。"""
    tag = await get_pool().execute(
        "UPDATE agent_tasks t SET status='ready',active_step=NULL,revision=revision+1,updated_at=NOW() "
        "FROM users u WHERE t.user_id=u.id AND u.email=$1 AND t.id=$2 AND t.revision=$3 "
        "AND t.status='running' AND t.plan IS NOT NULL AND NOT (t.results ? t.active_step)",
        current_user_email.get(""),
        task_id,
        revision,
    )
    return tag == "UPDATE 1"
