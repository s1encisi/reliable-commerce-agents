"""版本化只读任务：显式验收、会话锁协调和乐观版本栅栏。"""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.context import current_user_email
from shared.db import get_pool
from shared.task_acceptance import DEMO_TASK_GOAL, StepAcceptance, TaskRequirements, verify_step
from shared.task_acceptance import SERVICES as ACCEPTANCE_SERVICES

SERVICES = {"product-discovery", "order-management", "pricing-promotions", "review-sentiment", "inventory-fulfillment"}
_task_connection: ContextVar = ContextVar("task_connection", default=None)
_active_task_locks: dict[object, int] = {}


def _db():
    return _task_connection.get() or get_pool()


@asynccontextmanager
async def _connection():
    connection = _task_connection.get()
    if connection is not None:
        yield connection
    else:
        async with get_pool().acquire() as connection:
            yield connection


@asynccontextmanager
async def task_operation_lock(task_id: UUID):
    """持会话锁而不持长事务；始终为工具及普通查询留一条池连接。"""
    pool = get_pool()
    active = _active_task_locks.get(pool, 0)
    if active >= pool.get_max_size() - 1:
        yield False
        return
    # 同一事件循环中检查与增加之间没有 await；繁忙时拒绝而非持连接排队。
    _active_task_locks[pool] = active + 1
    key = int.from_bytes(hashlib.sha256(f"agent-task:{task_id}".encode()).digest()[:8], signed=True)
    try:
        async with pool.acquire() as connection:
            acquired = await connection.fetchval("SELECT pg_try_advisory_lock($1)", key)
            token = _task_connection.set(connection) if acquired else None
            try:
                yield bool(acquired)
            finally:
                if token is not None:
                    _task_connection.reset(token)
                    if not connection.is_closed():
                        await connection.execute("SELECT pg_advisory_unlock($1)", key)
    finally:
        remaining = _active_task_locks[pool] - 1
        if remaining:
            _active_task_locks[pool] = remaining
        else:
            _active_task_locks.pop(pool)


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    service: Literal[
        "product-discovery", "order-management", "pricing-promotions", "review-sentiment", "inventory-fulfillment"
    ]
    instruction: str = Field(min_length=1, max_length=2000)
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    done_when: Literal["successful_tool_result", "verified_claim"] = "successful_tool_result"
    # 允许加载旧计划供审阅，但新计划保存和旧计划执行都必须具有验收契约。
    acceptance: StepAcceptance | None = None


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str
    constraints: list[str]
    source: Literal["model", "deterministic_demo"] = "model"
    steps: list[PlanStep] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_dependencies(self) -> TaskPlan:
        seen = set()
        for step in self.steps:
            if step.id in seen or step.service not in SERVICES or not set(step.depends_on) <= seen:
                raise ValueError("步骤标识、服务或依赖关系不合法")
            if step.acceptance is not None and (
                not set(step.acceptance.from_steps) <= set(step.depends_on)
                or step.service not in ACCEPTANCE_SERVICES[step.acceptance.kind]
            ):
                raise ValueError("验收实体来源或专业服务不匹配")
            seen.add(step.id)
        return self


def review_items(task: dict) -> list[str]:
    """模型选择的验收种类不能证明它覆盖了用户的完整自由文本目标。"""
    items = list(task["constraints"])
    if (task.get("plan") or {}).get("source") != "deterministic_demo" or task["goal"] != DEMO_TASK_GOAL:
        items.insert(0, "任务目标：" + task["goal"])
    return items


def unpack(row) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    for key in ("constraints", "requirements", "plan", "results", "receipt_history", "constraint_confirmation"):
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    result["review_items"] = review_items(result)
    return result


async def create_task(goal: str, constraints: list[str], requirements: TaskRequirements | None = None) -> dict:
    row = await _db().fetchrow(
        "INSERT INTO agent_tasks(id,user_id,goal,constraints,requirements) "
        "SELECT $1,id,$2,$3::jsonb,$4::jsonb FROM users WHERE email=$5 RETURNING *",
        uuid4(),
        goal,
        json.dumps(constraints),
        (requirements or TaskRequirements()).model_dump_json(),
        current_user_email.get(""),
    )
    if row is None:
        raise PermissionError("用户不存在")
    return unpack(row)


async def load_task(task_id: UUID) -> dict | None:
    return unpack(
        await _db().fetchrow(
            "SELECT t.* FROM agent_tasks t JOIN users u ON u.id=t.user_id WHERE t.id=$1 AND u.email=$2",
            task_id,
            current_user_email.get(""),
        )
    )


async def claim_planning(task_id: UUID, revision: int, *, replan: bool = False) -> dict | None:
    allowed = ["needs_replan"] if replan else ["pending"]
    return unpack(
        await _db().fetchrow(
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
    if any(step.acceptance is None for step in plan.steps):
        raise ValueError("每个步骤必须提供受控验收契约")
    requirements = TaskRequirements.model_validate(task.get("requirements") or {})
    kinds = {step.acceptance.kind for step in plan.steps}
    if requirements.compare and "product_comparison" not in kinds:
        raise ValueError("比较任务必须包含对象对比验收")
    if requirements.in_stock is not None and "inventory_check" not in kinds:
        raise ValueError("库存要求必须包含库存核实步骤")
    if (
        requirements.product_ids
        or requirements.max_price is not None
        or requirements.min_rating is not None
        or requirements.category is not None
        or requirements.min_items > 1
    ) and not kinds & {"product_selection", "product_comparison", "inventory_check"}:
        raise ValueError("商品约束缺少对应验收步骤")
    done = set(task["results"])
    if not done <= {step.id for step in plan.steps}:
        raise ValueError("重规划遗漏已完成步骤")
    if task.get("plan"):
        old_steps = {s["id"]: s for s in task["plan"]["steps"]}
        new_steps = {s.id: s.model_dump(mode="json") for s in plan.steps}
        if any(old_steps.get(key) != new_steps.get(key) for key in done):
            raise ValueError("重规划不能改写已完成步骤的含义")
    tag = await _db().execute(
        "UPDATE agent_tasks t SET plan=$1::jsonb,status='ready',revision=revision+1,updated_at=NOW() FROM users u "
        "WHERE t.user_id=u.id AND u.email=$2 AND t.id=$3 AND t.revision=$4 AND t.status='planning'",
        plan.model_dump_json(),
        current_user_email.get(""),
        task_id,
        revision,
    )
    return tag == "UPDATE 1"


def _fresh_results(task: dict, plan: TaskPlan) -> tuple[dict, dict]:
    usable = {
        key
        for key, receipt in task["results"].items()
        if receipt.get("valid_until", 0) > time.time() and receipt.get("verification", {}).get("passed")
    }
    for step in plan.steps:
        if not set(step.depends_on) <= usable:
            usable.discard(step.id)
    fresh = {key: receipt for key, receipt in task["results"].items() if key in usable}
    stale = {key: receipt for key, receipt in task["results"].items() if key not in usable}
    return fresh, stale


async def claim_step(task_id: UUID, revision: int) -> tuple[dict, PlanStep] | None:
    async with _connection() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT t.* FROM agent_tasks t JOIN users u ON u.id=t.user_id WHERE t.id=$1 AND u.email=$2 FOR UPDATE OF t",
            task_id,
            current_user_email.get(""),
        )
        task = unpack(row)
        if not task or task["revision"] != revision or task["status"] not in {"ready", "completed", "needs_review"}:
            return None
        plan = TaskPlan.model_validate(task["plan"])
        if any(step.acceptance is None for step in plan.steps):
            await conn.execute(
                "UPDATE agent_tasks SET status='needs_replan',revision=revision+1,updated_at=NOW() WHERE id=$1",
                task_id,
            )
            return None
        fresh, stale = _fresh_results(task, plan)
        if stale:
            history = task.get("receipt_history", []) + [{"invalidated_at": time.time(), "receipts": stale}]
            task["results"] = fresh
            await conn.execute(
                "UPDATE agent_tasks SET results=$2::jsonb,receipt_history=$3::jsonb,"
                "constraint_confirmation=NULL WHERE id=$1",
                task_id,
                json.dumps(fresh, default=str),
                json.dumps(history, default=str),
            )
        ready = [s for s in plan.steps if s.id not in task["results"] and set(s.depends_on) <= set(task["results"])]
        if not ready:
            return None
        step = ready[0]
        await conn.execute(
            "UPDATE agent_tasks SET status='running',active_step=$2,revision=revision+1,updated_at=NOW() WHERE id=$1",
            task_id,
            step.id,
        )
        task["revision"] += 1
        return task, step


async def finish_step(task_id: UUID, revision: int, step: PlanStep, result: dict) -> bool:
    async with _connection() as conn, conn.transaction():
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
        # 以数据库保存的步骤为准，不能让调用方替换验收条件。
        plan = TaskPlan.model_validate(task["plan"])
        saved_step = next(s for s in plan.steps if s.id == step.id)
        fresh, stale = _fresh_results(task, plan)
        history = task.get("receipt_history", [])
        if stale:
            history.append({"invalidated_at": time.time(), "receipts": stale})
            task["results"] = fresh
        verification = await verify_step(conn, task, saved_step, result)
        results = task["results"]
        receipt = {
            "receipt_id": str(uuid4()),
            "verified_at": time.time(),
            "valid_until": time.time() + 300,
            "result": result,
            "verification": verification,
        }
        status = "needs_replan"
        if verification["passed"]:
            results[step.id] = receipt
            if len(results) == len(task["plan"]["steps"]):
                status = "needs_review" if review_items(task) else "completed"
            else:
                status = "ready"
        else:
            history.append({"step_id": step.id, "rejected_at": time.time(), "receipt": receipt})
        await conn.execute(
            "UPDATE agent_tasks SET results=$2::jsonb,status=$3,active_step=NULL,revision=revision+1,"
            "updated_at=NOW(),receipt_history=$4::jsonb,constraint_confirmation=NULL WHERE id=$1",
            task_id,
            json.dumps(results, default=str),
            status,
            json.dumps(history, default=str),
        )
        return True


async def planning_failed(task_id: UUID, revision: int) -> None:
    await _db().execute(
        "UPDATE agent_tasks t SET status='needs_replan',revision=revision+1,updated_at=NOW() FROM users u "
        "WHERE t.user_id=u.id AND u.email=$1 AND t.id=$2 AND t.revision=$3 AND t.status='planning'",
        current_user_email.get(""),
        task_id,
        revision,
    )


async def recover_readonly(task_id: UUID, revision: int) -> bool:
    """调用方必须持有 task_operation_lock；仅恢复失去会话锁的中断任务。"""
    tag = await _db().execute(
        "UPDATE agent_tasks t SET status=CASE WHEN t.status='planning' THEN 'needs_replan' ELSE 'ready' END,"
        "active_step=NULL,revision=revision+1,updated_at=NOW() "
        "FROM users u WHERE t.user_id=u.id AND u.email=$1 AND t.id=$2 AND t.revision=$3 "
        "AND (t.status='planning' OR (t.status='running' AND t.plan IS NOT NULL "
        "AND NOT (t.results ? t.active_step)))",
        current_user_email.get(""),
        task_id,
        revision,
    )
    return tag == "UPDATE 1"


async def edit_task(
    task_id: UUID, revision: int, goal: str, constraints: list[str], requirements: TaskRequirements
) -> bool:
    async with _connection() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT t.* FROM agent_tasks t JOIN users u ON u.id=t.user_id "
            "WHERE t.id=$1 AND u.email=$2 AND t.revision=$3 FOR UPDATE OF t",
            task_id,
            current_user_email.get(""),
            revision,
        )
        task = unpack(row)
        if not task or task["status"] in {"planning", "running"}:
            return False
        history = task.get("receipt_history", []) + [
            {
                "invalidated_at": time.time(),
                "reason": "task_edited",
                "revision": revision,
                "goal": task["goal"],
                "constraints": task["constraints"],
                "requirements": task["requirements"],
                "plan": task["plan"],
                "receipts": task["results"],
                "constraint_confirmation": task["constraint_confirmation"],
            }
        ]
        await conn.execute(
            "UPDATE agent_tasks SET goal=$2,constraints=$3::jsonb,requirements=$4::jsonb,plan=NULL,"
            "results='{}'::jsonb,receipt_history=$5::jsonb,constraint_confirmation=NULL,"
            "status=CASE WHEN planning_attempts=0 THEN 'pending' ELSE 'needs_replan' END,"
            "revision=revision+1,updated_at=NOW() WHERE id=$1",
            task_id,
            goal,
            json.dumps(constraints),
            requirements.model_dump_json(),
            json.dumps(history, default=str),
        )
        return True


async def confirm_constraints(task_id: UUID, revision: int) -> bool:
    async with _connection() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT t.* FROM agent_tasks t JOIN users u ON u.id=t.user_id "
            "WHERE t.id=$1 AND u.email=$2 AND t.revision=$3 AND t.status='needs_review' FOR UPDATE OF t",
            task_id,
            current_user_email.get(""),
            revision,
        )
        task = unpack(row)
        if not task:
            return False
        plan = TaskPlan.model_validate(task["plan"])
        fresh, stale = _fresh_results(task, plan)
        if stale:
            history = task["receipt_history"] + [{"invalidated_at": time.time(), "receipts": stale}]
            await conn.execute(
                "UPDATE agent_tasks SET results=$2::jsonb,receipt_history=$3::jsonb,status='ready',"
                "constraint_confirmation=NULL,revision=revision+1,updated_at=NOW() WHERE id=$1",
                task_id,
                json.dumps(fresh, default=str),
                json.dumps(history, default=str),
            )
            return False
        if len(fresh) != len(plan.steps):
            return False
        confirmation = {
            "source_kind": "user_confirmed",
            "goal": task["goal"],
            "review_items": review_items(task),
            "constraints": task["constraints"],
            "confirmed_at": time.time(),
            "revision": revision,
        }
        await conn.execute(
            "UPDATE agent_tasks SET status='completed',constraint_confirmation=$2::jsonb,"
            "revision=revision+1,updated_at=NOW() WHERE id=$1",
            task_id,
            json.dumps(confirmation),
        )
        return True
