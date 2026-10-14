"""PostgreSQL procurement state: control-plane CAS, expiring leases and fenced step receipts."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from shared.procurement.models import Lease, Plan, ProcurementSpec, StepOutcome, plan_hash


class TaskNotFound(LookupError):  # noqa: N818 - public runtime API
    pass


class TaskConflict(ValueError):  # noqa: N818 - public runtime API
    pass


class LeaseLost(RuntimeError):  # noqa: N818 - public runtime API
    pass


class BudgetExceeded(RuntimeError):  # noqa: N818 - public runtime API
    pass


_JSON_FIELDS = {
    "constraints",
    "requirements",
    "draft_spec",
    "plan",
    "previous_plan",
    "results",
    "receipt_history",
    "options",
    "counters",
    "definition",
    "outcome",
    "reused_from",
    "data",
    "payload",
}


def _uid(value) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _default(value):
    return value.isoformat() if isinstance(value, datetime) else str(value)


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=_default)


def unpack(row) -> dict | None:
    if row is None:
        return None
    value = dict(row)
    for name in _JSON_FIELDS & value.keys():
        if isinstance(value[name], str):
            value[name] = json.loads(value[name])
    return value


def public(value):
    return json.loads(dumps(value))


async def _owned(conn, task_id, email, *, lock=False):
    row = await conn.fetchrow(
        "SELECT t.*,u.email AS owner_email,u.role AS owner_role,u.is_active AS owner_active "
        "FROM agent_tasks t JOIN users u ON u.id=t.user_id "
        "WHERE t.id=$1 AND u.email=$2 AND t.task_type='procurement'" + (" FOR UPDATE OF t" if lock else ""),
        _uid(task_id),
        email,
    )
    if row is None:
        raise TaskNotFound("采购任务不存在")
    return unpack(row)


async def _fenced(conn, lease: Lease, *, lock=True):
    task = await _owned(conn, lease.task_id, lease.owner_email, lock=lock)
    now = await conn.fetchval("SELECT clock_timestamp()")
    if (
        task["lease_token"] != lease.token
        or task["procurement_generation"] != lease.generation
        or task["spec_revision"] != lease.spec_revision
        or not task["lease_expires_at"]
        or task["lease_expires_at"] <= now
        or not task["owner_active"]
    ):
        raise LeaseLost("采购执行租约或需求版本已失效")
    return task


async def _event(conn, task_id, event: str, payload: dict | None = None):
    row = await conn.fetchrow(
        "UPDATE agent_tasks SET state_seq=state_seq+1,updated_at=clock_timestamp() WHERE id=$1 "
        "RETURNING id,revision,spec_revision,plan_revision,status,state_seq",
        _uid(task_id),
    )
    value = {**dict(row), **(payload or {})}
    if payload and "step_id" in payload and "status" in payload:
        value["step_status"] = payload["status"]
    value["status"] = row["status"]
    value["task_id"] = str(value.pop("id"))
    seq = value.pop("state_seq")
    await conn.execute(
        "INSERT INTO procurement_events(task_id,seq,event,payload) VALUES($1,$2,$3,$4::jsonb)",
        _uid(task_id),
        seq,
        event,
        dumps(value),
    )


async def create_task(
    pool, owner_email: str, goal: str, *, draft_spec=None, status="draft", execution_mode="offline"
) -> dict:
    if execution_mode not in {"offline", "model"} or status not in {"draft", "needs_input"}:
        raise ValueError("非法采购执行模式或初始状态")
    task_id = uuid4()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "INSERT INTO agent_tasks(id,user_id,goal,task_type,execution_mode,draft_spec,status,counters) "
            "SELECT $1,id,$2,'procurement',$3,$4::jsonb,$5,$6::jsonb FROM users "
            "WHERE email=$7 AND is_active=TRUE RETURNING id",
            task_id,
            goal,
            execution_mode,
            dumps(draft_spec),
            status,
            dumps({"model_calls": 0, "repairs_used": 0, "step_attempts": 0, "reused_steps": 0}),
            owner_email,
        )
        if row is None:
            raise TaskNotFound("用户不存在或已停用")
        await _event(conn, task_id, "task.created")
    return await get_task(pool, task_id, owner_email)


async def get_task(pool, task_id, owner_email) -> dict | None:
    async with pool.acquire() as conn:
        try:
            task = await _owned(conn, task_id, owner_email)
        except TaskNotFound:
            return None
        steps = await conn.fetch(
            "SELECT * FROM procurement_steps WHERE task_id=$1 AND plan_revision=$2 ORDER BY step_id",
            _uid(task_id),
            task["plan_revision"],
        )
        artifacts = await conn.fetch(
            "SELECT * FROM procurement_artifacts WHERE task_id=$1 ORDER BY observed_at,artifact_id",
            _uid(task_id),
        )
        reusable = await conn.fetch(
            "SELECT step_id,plan_revision,attempt_id,input_hash,outcome,valid_until,reused_from "
            "FROM procurement_steps WHERE task_id=$1 AND status='succeeded' AND reuse_invalidated=FALSE "
            "AND valid_until>clock_timestamp() ORDER BY updated_at DESC",
            _uid(task_id),
        )
    task["steps"] = [{**unpack(row), "id": row["step_id"]} for row in steps]
    task["artifacts"] = [unpack(row) for row in artifacts]
    task["reusable_outcomes"] = [unpack(row) for row in reusable]
    task["counters"] = {
        "model_calls": 0,
        "repairs_used": 0,
        "step_attempts": 0,
        "reused_steps": 0,
        **task["counters"],
        "planning_attempts": task["planning_attempts"],
    }
    for name in ("lease_token", "lease_owner", "lease_expires_at", "heartbeat_at"):
        task.pop(name, None)
    return public(task)


def _check_revision(task, revision, allowed):
    if task["revision"] != revision or task["status"] not in allowed:
        raise TaskConflict("任务版本冲突或当前状态不允许此操作")


async def update_draft(
    pool, task_id, owner_email, revision, draft_spec, *, goal=None, status="draft", stop_reason=None
) -> dict:
    if status not in {"draft", "needs_input"}:
        raise ValueError("非法草稿状态")
    async with pool.acquire() as conn, conn.transaction():
        task = await _owned(conn, task_id, owner_email, lock=True)
        _check_revision(task, revision, {"draft", "needs_input", "paused"})
        history = task["receipt_history"] + [
            {
                "kind": "draft_edited",
                "goal": task["goal"],
                "requirements": task["requirements"],
                "draft_spec": task["draft_spec"],
                "revision": revision,
            }
        ]
        await conn.execute(
            "UPDATE agent_tasks SET draft_spec=$2::jsonb,status=$3,stop_reason=$4,options='[]'::jsonb,"
            "goal=COALESCE($5,goal),receipt_history=$6::jsonb,revision=revision+1 WHERE id=$1",
            _uid(task_id),
            dumps(draft_spec),
            status,
            stop_reason,
            goal,
            dumps(history),
        )
        await _event(conn, task_id, "spec.draft_updated")
    return await get_task(pool, task_id, owner_email)


async def confirm_spec(pool, task_id, owner_email, revision, spec: ProcurementSpec) -> dict:
    spec = ProcurementSpec.model_validate(spec)
    async with pool.acquire() as conn, conn.transaction():
        task = await _owned(conn, task_id, owner_email, lock=True)
        _check_revision(task, revision, {"draft", "needs_input", "paused"})
        history = task["receipt_history"]
        if task["spec_revision"]:
            history.append(
                {
                    "kind": "spec_revised",
                    "spec_revision": task["spec_revision"],
                    "requirements": task["requirements"],
                    "plan": task["plan"],
                    "results": task["results"],
                }
            )
        counters = {**task["counters"], "repairs_used": 0}
        await conn.execute(
            "UPDATE agent_tasks SET requirements=$2::jsonb,draft_spec=$2::jsonb,spec_revision=spec_revision+1,"
            "previous_plan=COALESCE(plan,previous_plan),plan=NULL,plan_hash=NULL,results='{}'::jsonb,"
            "options='[]'::jsonb,receipt_history=$3::jsonb,status='pending',revision=revision+1,"
            "planning_attempts=0,counters=$4::jsonb,stop_reason=NULL,procurement_generation=procurement_generation+1,"
            "lease_token=NULL,lease_owner=NULL,lease_expires_at=NULL,run_deadline=NULL,"
            "latest_checkpoint_id=NULL,checkpoint_plan_hash=NULL,checkpoint_layer=-1 WHERE id=$1",
            _uid(task_id),
            spec.model_dump_json(),
            dumps(history),
            dumps(counters),
        )
        await _event(conn, task_id, "spec.confirmed")
    return await get_task(pool, task_id, owner_email)


async def queue_task(pool, task_id, owner_email, revision) -> dict:
    async with pool.acquire() as conn, conn.transaction():
        task = await _owned(conn, task_id, owner_email, lock=True)
        _check_revision(task, revision, {"pending", "paused", "failed"})
        if task["spec_revision"] < 1:
            raise TaskConflict("请先确认采购需求")
        if task["stop_reason"] in {"REPAIR_LIMIT", "INVALID_REPAIR_PLAN", "INVALID_PLAN"}:
            raise TaskConflict("需确认新需求，不能通过反复启动恢复修复额度")
        if task["plan"] is None and task["planning_attempts"] >= 2:
            raise TaskConflict("当前需求的两次规划额度已耗尽")
        if task["execution_mode"] == "model" and task["counters"].get("model_calls", 0) >= 24:
            raise BudgetExceeded("本任务模型调用额度已耗尽")
        await conn.execute(
            "UPDATE agent_tasks SET status='queued',revision=revision+1,stop_reason=NULL,run_deadline=NULL WHERE id=$1",
            _uid(task_id),
        )
        await _event(conn, task_id, "task.queued")
    return await get_task(pool, task_id, owner_email)


async def request_pause(pool, task_id, owner_email, revision) -> dict:
    async with pool.acquire() as conn, conn.transaction():
        task = await _owned(conn, task_id, owner_email, lock=True)
        _check_revision(
            task,
            revision,
            {
                "pending",
                "queued",
                "planning",
                "running",
                "pausing",
                "completed",
                "needs_review",
                "failed",
                "budget_exhausted",
            },
        )
        status = "pausing" if task["lease_token"] is not None else "paused"
        await conn.execute(
            "UPDATE agent_tasks SET status=$2,revision=revision+1 WHERE id=$1",
            _uid(task_id),
            status,
        )
        await _event(conn, task_id, "task.pause_requested")
    return await get_task(pool, task_id, owner_email)


async def list_events(pool, task_id, owner_email, after_seq=0, limit=100):
    async with pool.acquire() as conn:
        await _owned(conn, task_id, owner_email)
        rows = await conn.fetch(
            "SELECT seq,event,payload,created_at FROM procurement_events WHERE task_id=$1 AND seq>$2 "
            "ORDER BY seq LIMIT $3",
            _uid(task_id),
            max(0, after_seq),
            min(max(limit, 1), 500),
        )
    return public([unpack(row) for row in rows])


async def reserve_model_call(pool, task_id, owner_email, purpose, max_calls=24) -> int:
    async with pool.acquire() as conn, conn.transaction():
        task = await _owned(conn, task_id, owner_email, lock=True)
        if not task["owner_active"] or task["execution_mode"] != "model":
            raise BudgetExceeded("任务不允许调用付费模型")
        from shared.procurement.models import current_procurement_binding

        binding = current_procurement_binding.get()
        if (
            not binding
            or str(binding.get("task_id")) != str(task_id)
            or binding.get("owner_email") != owner_email
            or binding.get("spec_revision") != task["spec_revision"]
        ):
            raise BudgetExceeded("模型调用缺少匹配的任务、身份或需求版本")
        if binding.get("lease_token") is None:
            if task["status"] not in {"draft", "needs_input"}:
                raise BudgetExceeded("无租约的模型调用仅允许解析当前草稿")
        else:
            now = await conn.fetchval("SELECT clock_timestamp()")
            if (
                str(task["lease_token"]) != str(binding["lease_token"])
                or task["procurement_generation"] != binding.get("generation")
                or not task["lease_expires_at"]
                or task["lease_expires_at"] <= now
                or task["status"] not in {"planning", "running", "pausing"}
                or (task["run_deadline"] is not None and task["run_deadline"] <= now)
            ):
                raise BudgetExceeded("旧租约、已停止任务或截止时间后的模型调用已失效")
        counters = task["counters"]
        count = counters.get("model_calls", 0)
        if count >= min(max_calls, 24):
            raise BudgetExceeded("本任务模型调用额度已耗尽")
        counters["model_calls"] = count + 1
        await conn.execute("UPDATE agent_tasks SET counters=$2::jsonb WHERE id=$1", _uid(task_id), dumps(counters))
        await _event(conn, task_id, "model.call_reserved", {"purpose": str(purpose)[:40], "model_calls": count + 1})
        return count + 1


async def claim_task(pool, worker_id, *, task_id=None, lease_seconds=30, run_timeout_seconds=180) -> Lease | None:
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT t.*,u.email AS owner_email FROM agent_tasks t JOIN users u ON u.id=t.user_id "
            "WHERE t.task_type='procurement' AND u.is_active=TRUE "
            "AND t.status IN ('queued','planning','running','pausing') "
            "AND (t.status='queued' OR t.lease_expires_at<=clock_timestamp()) "
            "AND ($1::uuid IS NULL OR t.id=$1) ORDER BY t.created_at LIMIT 1 FOR UPDATE OF t SKIP LOCKED",
            _uid(task_id) if task_id else None,
        )
        if row is None:
            return None
        task = unpack(row)
        token = uuid4()
        generation = task["procurement_generation"] + 1
        deadline = task["run_deadline"]
        if task["status"] == "queued" or deadline is None:
            deadline = await conn.fetchval(
                "SELECT clock_timestamp()+$1::double precision * interval '1 second'",
                run_timeout_seconds,
            )
        await conn.execute(
            "UPDATE procurement_steps SET status='unknown',updated_at=clock_timestamp() "
            "WHERE task_id=$1 AND status='running'",
            task["id"],
        )
        await conn.execute(
            "UPDATE procurement_attempts SET status='unknown',finished_at=clock_timestamp(),accepted=FALSE,"
            "outcome=$2::jsonb WHERE task_id=$1 AND finished_at IS NULL",
            task["id"],
            dumps({"status": "unknown", "error_code": "LEASE_EXPIRED"}),
        )
        await conn.execute(
            "UPDATE agent_tasks SET lease_token=$2,lease_owner=$3,procurement_generation=$4,"
            "lease_expires_at=clock_timestamp()+$5::double precision*interval '1 second',"
            "heartbeat_at=clock_timestamp(),run_deadline=$6,"
            "status=CASE WHEN status='pausing' THEN 'pausing' ELSE 'running' END WHERE id=$1",
            task["id"],
            token,
            worker_id,
            generation,
            lease_seconds,
            deadline,
        )
        await _event(conn, task["id"], "run.claimed", {"generation": generation})
        return Lease(task["id"], token, generation, task["spec_revision"], task["owner_email"])


async def heartbeat(pool, lease: Lease, lease_seconds=30) -> bool:
    tag = await pool.execute(
        "UPDATE agent_tasks SET heartbeat_at=clock_timestamp(),"
        "lease_expires_at=clock_timestamp()+$5::double precision*interval '1 second' "
        "WHERE id=$1 AND lease_token=$2 AND procurement_generation=$3 AND spec_revision=$4 "
        "AND lease_expires_at>clock_timestamp()",
        lease.task_id,
        lease.token,
        lease.generation,
        lease.spec_revision,
        lease_seconds,
    )
    return tag == "UPDATE 1"


async def lease_task(pool, lease: Lease) -> dict:
    async with pool.acquire() as conn:
        await _fenced(conn, lease, lock=False)
    task = await get_task(pool, lease.task_id, lease.owner_email)
    return task


async def begin_planning(pool, lease: Lease, *, repair=False) -> dict:
    async with pool.acquire() as conn, conn.transaction():
        task = await _fenced(conn, lease)
        if task["status"] == "pausing":
            raise TaskConflict("PAUSE_REQUESTED")
        if task["planning_attempts"] >= 2 or (repair and task["counters"].get("repairs_used", 0) >= 1):
            raise TaskConflict("PLANNING_LIMIT")
        counters = task["counters"]
        if repair:
            counters["repairs_used"] = counters.get("repairs_used", 0) + 1
        await conn.execute(
            "UPDATE agent_tasks SET planning_attempts=planning_attempts+1,counters=$2::jsonb,"
            "status=CASE WHEN status='pausing' THEN status ELSE 'planning' END WHERE id=$1",
            lease.task_id,
            dumps(counters),
        )
        await _event(conn, lease.task_id, "plan.requested", {"repair": repair})
    return await lease_task(pool, lease)


async def save_plan(pool, lease: Lease, plan: Plan) -> dict:
    async with pool.acquire() as conn, conn.transaction():
        task = await _fenced(conn, lease)
        revision = task["plan_revision"] + 1
        digest = plan_hash(plan, ProcurementSpec.model_validate(task["requirements"]))
        await conn.execute(
            "UPDATE agent_tasks SET previous_plan=COALESCE(plan,previous_plan),plan=$2::jsonb,plan_revision=$3,"
            "plan_hash=$4,results='{}'::jsonb,latest_checkpoint_id=NULL,checkpoint_plan_hash=NULL,checkpoint_layer=-1,"
            "status=CASE WHEN status='pausing' THEN status ELSE 'running' END WHERE id=$1",
            lease.task_id,
            plan.model_dump_json(),
            revision,
            digest,
        )
        for step in plan.steps:
            await conn.execute(
                "INSERT INTO procurement_steps(task_id,spec_revision,plan_revision,step_id,capability,definition) "
                "VALUES($1,$2,$3,$4,$5,$6::jsonb)",
                lease.task_id,
                lease.spec_revision,
                revision,
                step.id,
                step.capability,
                step.model_dump_json(),
            )
        await _event(conn, lease.task_id, "plan.accepted", {"plan_hash": digest, "source": plan.source})
    return await lease_task(pool, lease)


async def find_reusable(pool, lease: Lease, capability: str, input_hash: str):
    async with pool.acquire() as conn:
        await _fenced(conn, lease, lock=False)
        row = await conn.fetchrow(
            "SELECT * FROM procurement_steps WHERE task_id=$1 AND capability=$2 AND input_hash=$3 "
            "AND status='succeeded' AND reuse_invalidated=FALSE AND valid_until>clock_timestamp() "
            "ORDER BY updated_at DESC LIMIT 1",
            lease.task_id,
            capability,
            input_hash,
        )
    return public(unpack(row)) if row else None


async def mark_reused(pool, lease: Lease, plan_revision: int, step_id: str, input_hash: str, cached: dict):
    async with pool.acquire() as conn, conn.transaction():
        task = await _fenced(conn, lease)
        if task["plan_revision"] != plan_revision:
            raise LeaseLost("计划已变化")
        origin = cached.get("reused_from") or {
            "plan_revision": cached["plan_revision"],
            "step_id": cached["step_id"],
            "attempt_id": cached["attempt_id"],
        }
        await conn.execute(
            "UPDATE procurement_steps SET status='succeeded',input_hash=$4,outcome=$5::jsonb,"
            "valid_until=$6,reused_from=$7::jsonb,updated_at=clock_timestamp() "
            "WHERE task_id=$1 AND plan_revision=$2 AND step_id=$3 AND status<>'running'",
            lease.task_id,
            plan_revision,
            step_id,
            input_hash,
            dumps(cached["outcome"]),
            datetime.fromisoformat(cached["valid_until"]),
            dumps(origin),
        )
        counters = task["counters"]
        counters["reused_steps"] = counters.get("reused_steps", 0) + 1
        results = task["results"]
        results[step_id] = cached["outcome"]
        await conn.execute(
            "UPDATE agent_tasks SET counters=$2::jsonb,results=$3::jsonb WHERE id=$1",
            lease.task_id,
            dumps(counters),
            dumps(results),
        )
        await _event(
            conn, lease.task_id, "step.reused", {"step_id": step_id, "status": "succeeded", "reused_from": origin}
        )
    return StepOutcome.model_validate(cached["outcome"])


async def begin_step(pool, lease: Lease, plan_revision: int, step_id: str, input_hash: str) -> tuple[UUID, int]:
    async with pool.acquire() as conn, conn.transaction():
        task = await _fenced(conn, lease)
        if task["status"] == "pausing":
            raise TaskConflict("PAUSE_REQUESTED")
        if task["plan_revision"] != plan_revision:
            raise LeaseLost("计划已变化")
        row = await conn.fetchrow(
            "SELECT * FROM procurement_steps WHERE task_id=$1 AND plan_revision=$2 AND step_id=$3 FOR UPDATE",
            lease.task_id,
            plan_revision,
            step_id,
        )
        if row is None or row["status"] == "running":
            raise TaskConflict("步骤已被认领")
        if row["attempt"] >= 3:
            raise TaskConflict("STEP_ATTEMPT_LIMIT")
        attempt_id = uuid4()
        number = row["attempt"] + 1
        await conn.execute(
            "INSERT INTO procurement_attempts(id,task_id,spec_revision,plan_revision,step_id,"
            "attempt,generation,lease_token)"
            " VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
            attempt_id,
            lease.task_id,
            lease.spec_revision,
            plan_revision,
            step_id,
            number,
            lease.generation,
            lease.token,
        )
        await conn.execute(
            "UPDATE procurement_steps SET status='running',attempt=$4,attempt_id=$5,input_hash=$6,"
            "outcome=NULL,reused_from=NULL,updated_at=clock_timestamp() "
            "WHERE task_id=$1 AND plan_revision=$2 AND step_id=$3",
            lease.task_id,
            plan_revision,
            step_id,
            number,
            attempt_id,
            input_hash,
        )
        counters = task["counters"]
        counters["step_attempts"] = counters.get("step_attempts", 0) + 1
        await conn.execute("UPDATE agent_tasks SET counters=$2::jsonb WHERE id=$1", lease.task_id, dumps(counters))
        await _event(conn, lease.task_id, "step.started", {"step_id": step_id, "attempt": number, "status": "running"})
        return attempt_id, number


def _time(value, fallback):
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else fallback
    if isinstance(value, str):
        try:
            result = datetime.fromisoformat(value)
            return result if result.tzinfo is not None else fallback
        except ValueError:
            pass
    return fallback


async def finish_step(
    pool, lease: Lease, plan_revision: int, step_id: str, attempt_id: UUID, outcome: StepOutcome
) -> StepOutcome | None:
    async with pool.acquire() as conn, conn.transaction():
        task = await _owned(conn, lease.task_id, lease.owner_email, lock=True)
        now = await conn.fetchval("SELECT clock_timestamp()")
        row = await conn.fetchrow(
            "SELECT * FROM procurement_steps WHERE task_id=$1 AND plan_revision=$2 AND step_id=$3 FOR UPDATE",
            lease.task_id,
            plan_revision,
            step_id,
        )
        prior = await conn.fetchrow("SELECT * FROM procurement_attempts WHERE id=$1", attempt_id)
        if prior is not None and prior["finished_at"] is not None:
            if prior["accepted"]:
                return StepOutcome.model_validate(unpack(prior)["outcome"])
            await conn.execute(
                "UPDATE procurement_attempts SET late_outcome=$2::jsonb WHERE id=$1",
                attempt_id,
                outcome.model_dump_json(),
            )
            return None
        accepted = bool(
            task["owner_active"]
            and row
            and row["attempt_id"] == attempt_id
            and task["lease_token"] == lease.token
            and task["procurement_generation"] == lease.generation
            and task["spec_revision"] == lease.spec_revision
            and task["plan_revision"] == plan_revision
            and task["lease_expires_at"]
            and task["lease_expires_at"] > now
        )
        data = outcome.model_dump(mode="json")
        observed = _time(outcome.data.get("observed_at"), now)
        valid = _time(outcome.data.get("valid_until"), now + timedelta(seconds=300))
        if accepted and outcome.status == "succeeded":
            artifacts = outcome.artifacts or [{"data": outcome.data}]
            data["artifacts"] = []
            for artifact in artifacts:
                artifact_id = uuid4()
                item = {**artifact, "artifact_id": str(artifact_id)}
                data["artifacts"].append(item)
                await conn.execute(
                    "INSERT INTO procurement_artifacts(artifact_id,task_id,attempt_id,plan_revision,step_id,"
                    "capability,source,observed_at,valid_until,data) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)",
                    artifact_id,
                    lease.task_id,
                    attempt_id,
                    plan_revision,
                    step_id,
                    row["capability"],
                    artifact.get("source", outcome.data.get("source", outcome.source)),
                    observed,
                    valid,
                    dumps(artifact.get("data", artifact)),
                )
        await conn.execute(
            "UPDATE procurement_attempts SET status=$2,finished_at=clock_timestamp(),accepted=$3,outcome=$4::jsonb "
            "WHERE id=$1 AND finished_at IS NULL",
            attempt_id,
            outcome.status,
            accepted,
            dumps(data),
        )
        if not accepted:
            return None
        await conn.execute(
            "UPDATE procurement_steps SET status=$4,outcome=$5::jsonb,valid_until=$6,reuse_invalidated=FALSE,"
            "updated_at=clock_timestamp() WHERE task_id=$1 AND plan_revision=$2 AND step_id=$3",
            lease.task_id,
            plan_revision,
            step_id,
            outcome.status,
            dumps(data),
            valid,
        )
        results = task["results"]
        results[step_id] = data
        await conn.execute("UPDATE agent_tasks SET results=$2::jsonb WHERE id=$1", lease.task_id, dumps(results))
        await _event(
            conn,
            lease.task_id,
            "step.finished",
            {
                "step_id": step_id,
                "status": outcome.status,
                "attempt": row["attempt"],
                "source": outcome.source,
                "error_code": outcome.error_code,
            },
        )
        return StepOutcome.model_validate(data)


async def save_checkpoint(pool, lease: Lease, digest: str, checkpoint_id: str, layer: int):
    async with pool.acquire() as conn, conn.transaction():
        task = await _fenced(conn, lease)
        if task["plan_hash"] != digest:
            raise LeaseLost("检查点业务计划不匹配")
        await conn.execute(
            "UPDATE agent_tasks SET latest_checkpoint_id=$2,checkpoint_plan_hash=$3,checkpoint_layer=$4 WHERE id=$1",
            lease.task_id,
            checkpoint_id,
            digest,
            layer,
        )
        await _event(conn, lease.task_id, "run.checkpoint", {"checkpoint_id": checkpoint_id, "layer": layer})


async def record_failures(pool, lease: Lease, failures: list[dict]):
    async with pool.acquire() as conn, conn.transaction():
        task = await _fenced(conn, lease)
        history = task["receipt_history"]
        history.append(
            {
                "kind": "repair_requested",
                "plan_revision": task["plan_revision"],
                "plan": task["plan"],
                "failures": failures,
                "results": task["results"],
            }
        )
        rows = await conn.fetch(
            "SELECT plan_revision,step_id,capability,outcome FROM procurement_steps "
            "WHERE task_id=$1 AND status='succeeded' AND reuse_invalidated=FALSE",
            lease.task_id,
        )
        for row in rows:
            outcome = unpack(row)["outcome"]
            items = outcome.get("data", {}).get("items", [])
            entities = {str(x.get("product_id", x.get("id", ""))) for x in items if isinstance(x, dict)}
            for failure in failures:
                capabilities = set(failure.get("capabilities") or [])
                affected = {str(x) for x in failure.get("affected_product_ids") or []}
                if failure.get("product_id"):
                    affected.add(str(failure["product_id"]))
                if (not capabilities or row["capability"] in capabilities) and (not affected or entities & affected):
                    await conn.execute(
                        "UPDATE procurement_steps SET reuse_invalidated=TRUE WHERE task_id=$1 "
                        "AND plan_revision=$2 AND step_id=$3",
                        lease.task_id,
                        row["plan_revision"],
                        row["step_id"],
                    )
                    break
        await conn.execute(
            "UPDATE agent_tasks SET previous_plan=plan,receipt_history=$2::jsonb WHERE id=$1",
            lease.task_id,
            dumps(history),
        )
        await _event(conn, lease.task_id, "plan.repair_required", {"failures": failures})


async def finish_task(pool, lease: Lease, status: str, *, options=None, stop_reason=None, verification=None):
    if status not in {"paused", "needs_review", "completed", "failed", "budget_exhausted"}:
        raise ValueError("非法采购终态")
    async with pool.acquire() as conn, conn.transaction():
        task = await _fenced(conn, lease)
        if status == "completed" and (
            task["plan"] is None
            or await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM procurement_steps WHERE task_id=$1 AND plan_revision=$2 "
                "AND status<>'succeeded')",
                lease.task_id,
                task["plan_revision"],
            )
        ):
            raise TaskConflict("UNFINISHED_STEPS")
        await conn.execute(
            "UPDATE procurement_attempts SET status='unknown',finished_at=clock_timestamp(),accepted=FALSE,"
            "outcome=$2::jsonb WHERE task_id=$1 AND finished_at IS NULL",
            lease.task_id,
            dumps({"status": "unknown", "error_code": stop_reason or "RUN_STOPPED"}),
        )
        await conn.execute(
            "UPDATE procurement_steps SET status='unknown' WHERE task_id=$1 AND status='running'",
            lease.task_id,
        )
        results = task["results"]
        history = task["receipt_history"]
        previous = results.pop("_final_verification", None)
        if previous is not None:
            history.append({"kind": "final_verification_retired", "verification": previous})
        if status == "completed" and verification is not None:
            if not isinstance(verification, dict) or verification.get("passed") is False:
                raise TaskConflict("FINAL_VERIFICATION_FAILED")
            now = await conn.fetchval("SELECT clock_timestamp()")
            receipt = dict(verification)
            receipt.setdefault("verified_at", now.isoformat())
            receipt.update(
                recorded_at=now.isoformat(),
                spec_revision=lease.spec_revision,
                plan_revision=task["plan_revision"],
                plan_hash=task["plan_hash"],
            )
            results["_final_verification"] = receipt
        await conn.execute(
            "UPDATE agent_tasks SET status=$2,options=$3::jsonb,stop_reason=$4,results=$5::jsonb,"
            "receipt_history=$6::jsonb,lease_token=NULL,lease_owner=NULL,lease_expires_at=NULL,"
            "heartbeat_at=NULL WHERE id=$1",
            lease.task_id,
            status,
            dumps(options or []),
            stop_reason,
            dumps(results),
            dumps(history),
        )
        await _event(conn, lease.task_id, "task." + status, {"stop_reason": stop_reason})


async def clear_stale_checkpoint(pool, lease: Lease):
    async with pool.acquire() as conn, conn.transaction():
        await _fenced(conn, lease)
        await conn.execute(
            "UPDATE agent_tasks SET latest_checkpoint_id=NULL,checkpoint_plan_hash=NULL,"
            "checkpoint_layer=-1 WHERE id=$1",
            lease.task_id,
        )
        await _event(conn, lease.task_id, "run.checkpoint_rejected")


async def record_unstarted_outcome(pool, lease: Lease, plan_revision: int, step_id: str, outcome: StepOutcome):
    """确定性输入/依赖错误不冒充工具调用，仍留下可解释的步骤结果。"""
    async with pool.acquire() as conn, conn.transaction():
        task = await _fenced(conn, lease)
        if task["plan_revision"] != plan_revision:
            raise LeaseLost("计划已变化")
        await conn.execute(
            "UPDATE procurement_steps SET status=$4,outcome=$5::jsonb,updated_at=clock_timestamp() "
            "WHERE task_id=$1 AND plan_revision=$2 AND step_id=$3",
            lease.task_id,
            plan_revision,
            step_id,
            outcome.status,
            outcome.model_dump_json(),
        )
        results = task["results"]
        results[step_id] = outcome.model_dump(mode="json")
        await conn.execute("UPDATE agent_tasks SET results=$2::jsonb WHERE id=$1", lease.task_id, dumps(results))
        await _event(
            conn,
            lease.task_id,
            "step.blocked",
            {
                "step_id": step_id,
                "status": outcome.status,
                "attempt": 0,
                "error_code": outcome.error_code,
            },
        )
    return outcome


async def invalidate_expired_receipts(pool, lease: Lease) -> bool:
    """重放到更早阶段，但不废弃依赖节点本身仍有效的语义缓存。"""
    async with pool.acquire() as conn, conn.transaction():
        task = await _fenced(conn, lease)
        if task["plan"] is None:
            return False
        rows = await conn.fetch(
            "SELECT * FROM procurement_steps WHERE task_id=$1 AND plan_revision=$2 ORDER BY step_id FOR UPDATE",
            lease.task_id,
            task["plan_revision"],
        )
        now = await conn.fetchval("SELECT clock_timestamp()")
        expired = {
            row["step_id"]
            for row in rows
            if row["status"] == "succeeded"
            and (row["reuse_invalidated"] or row["valid_until"] is None or row["valid_until"] <= now)
        }
        if not expired:
            return False
        affected = set(expired)
        for layer in Plan.model_validate(task["plan"]).layers():
            for step in layer:
                if set(step.depends_on) & affected:
                    affected.add(step.id)
        results = task["results"]
        retired = {key: results.pop(key) for key in affected if key in results}
        results.pop("_final_verification", None)
        history = task["receipt_history"] + [
            {
                "kind": "evidence_expired",
                "plan_revision": task["plan_revision"],
                "expired_steps": sorted(expired),
                "replayed_steps": sorted(affected),
                "receipts": retired,
                "invalidated_at": now.isoformat(),
            }
        ]
        for row in rows:
            if row["step_id"] in expired:
                await conn.execute(
                    "UPDATE procurement_steps SET status='pending',outcome=NULL,reuse_invalidated=TRUE,"
                    "updated_at=clock_timestamp() WHERE task_id=$1 AND plan_revision=$2 AND step_id=$3",
                    lease.task_id,
                    task["plan_revision"],
                    row["step_id"],
                )
            elif row["step_id"] in affected and row["status"] != "succeeded":
                await conn.execute(
                    "UPDATE procurement_steps SET status='pending',outcome=NULL,updated_at=clock_timestamp() "
                    "WHERE task_id=$1 AND plan_revision=$2 AND step_id=$3",
                    lease.task_id,
                    task["plan_revision"],
                    row["step_id"],
                )
        await conn.execute(
            "UPDATE agent_tasks SET results=$2::jsonb,receipt_history=$3::jsonb,"
            "latest_checkpoint_id=NULL,checkpoint_plan_hash=NULL,checkpoint_layer=-1 WHERE id=$1",
            lease.task_id,
            dumps(results),
            dumps(history),
        )
        await _event(
            conn,
            lease.task_id,
            "run.evidence_expired",
            {
                "expired_steps": sorted(expired),
                "replayed_steps": sorted(affected),
            },
        )
        return True
