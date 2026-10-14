"""Real PostgreSQL tests for procurement CAS, leases, fencing and retained evidence."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from shared.procurement import state
from shared.procurement.models import Plan, PlanStep, ProcurementSpec, StepOutcome, current_procurement_binding


@pytest_asyncio.fixture
async def owner(clean_db):
    await clean_db.execute(
        "INSERT INTO users(email,password_hash,name,role) VALUES('runtime@example.test','hash','Owner','customer')"
    )
    return clean_db, "runtime@example.test"


def spec(**updates):
    return ProcurementSpec(query="耳机", quantity=2, total_budget="500.00", **updates)


async def queued(pool, email, *, execution_mode="offline"):
    task = await state.create_task(pool, email, "采购耳机", execution_mode=execution_mode)
    task = await state.confirm_spec(pool, task["id"], email, task["revision"], spec())
    return await state.queue_task(pool, task["id"], email, task["revision"])


def simple_plan():
    return Plan(
        source="deterministic_demo",
        steps=[
            PlanStep(id="find", capability="search_candidates"),
            PlanStep(id="stock", capability="check_inventory", depends_on=["find"]),
        ],
    )


def outcome(product=None):
    now = datetime.now(UTC)
    return StepOutcome(
        status="succeeded",
        data={
            "source": "tool:search_candidates",
            "items": [{"product_id": str(product or uuid4())}],
            "observed_at": now.isoformat(),
            "valid_until": (now + timedelta(seconds=300)).isoformat(),
        },
    )


async def test_control_cas_owner_and_legacy_task_isolation(owner):
    pool, email = owner
    task = await state.create_task(pool, email, "原目标", draft_spec={"query": "耳机"})
    assert task["task_type"] == "procurement" and task["execution_mode"] == "offline"
    assert await state.get_task(pool, task["id"], "other@example.test") is None
    changed = await state.update_draft(pool, task["id"], email, 0, {"query": "键盘"}, goal="新目标")
    assert changed["goal"] == "新目标"
    assert changed["receipt_history"][-1]["goal"] == "原目标"
    with pytest.raises(state.TaskConflict):
        await state.confirm_spec(pool, task["id"], email, 0, spec())
    old = await pool.fetchval(
        "INSERT INTO agent_tasks(id,user_id,goal,status) SELECT $1,id,'legacy','queued' FROM users WHERE email=$2 "
        "RETURNING id",
        uuid4(),
        email,
    )
    assert await state.claim_task(pool, "worker", task_id=old) is None


async def test_lease_claim_is_atomic_and_old_completion_is_archived(owner):
    pool, email = owner
    task = await queued(pool, email)
    claims = await asyncio.gather(
        state.claim_task(pool, "a", task_id=task["id"]),
        state.claim_task(pool, "b", task_id=task["id"]),
    )
    first = next(value for value in claims if value)
    assert sum(value is not None for value in claims) == 1
    await state.begin_planning(pool, first)
    planned = await state.save_plan(pool, first, simple_plan())
    attempt, _ = await state.begin_step(pool, first, planned["plan_revision"], "find", "key")
    await pool.execute(
        "UPDATE agent_tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=$1",
        first.task_id,
    )
    second = await state.claim_task(pool, "replacement", task_id=task["id"])
    assert second and second.generation > first.generation
    assert await state.heartbeat(pool, first) is False
    assert await state.finish_step(pool, first, planned["plan_revision"], "find", attempt, outcome()) is None
    archived = await pool.fetchrow("SELECT accepted,status,late_outcome FROM procurement_attempts WHERE id=$1", attempt)
    assert archived["accepted"] is False and archived["status"] == "unknown" and archived["late_outcome"]
    assert (await state.get_task(pool, task["id"], email))["steps"][0]["status"] == "unknown"


async def test_receipt_reuse_crosses_step_ids_and_spec_revision(owner):
    pool, email = owner
    task = await queued(pool, email)
    lease = await state.claim_task(pool, "one", task_id=task["id"])
    await state.begin_planning(pool, lease)
    planned = await state.save_plan(pool, lease, simple_plan())
    aid, _ = await state.begin_step(pool, lease, planned["plan_revision"], "find", "stable-input")
    saved = await state.finish_step(pool, lease, planned["plan_revision"], "find", aid, outcome())
    again = await state.finish_step(pool, lease, planned["plan_revision"], "find", aid, outcome())
    assert saved.artifacts == again.artifacts
    assert await pool.fetchval("SELECT COUNT(*) FROM procurement_artifacts WHERE task_id=$1", lease.task_id) == 1
    await state.finish_task(pool, lease, "paused")
    paused = await state.get_task(pool, task["id"], email)
    updated = await state.confirm_spec(
        pool,
        task["id"],
        email,
        paused["revision"],
        ProcurementSpec(query="耳机", quantity=2, total_budget="400"),
    )
    assert updated["previous_plan"] and updated["reusable_outcomes"]
    await state.queue_task(pool, task["id"], email, updated["revision"])
    lease2 = await state.claim_task(pool, "two", task_id=task["id"])
    await state.begin_planning(pool, lease2)
    newplan = Plan(source="deterministic_demo", steps=[PlanStep(id="other", capability="search_candidates")])
    current = await state.save_plan(pool, lease2, newplan)
    cached = await state.find_reusable(pool, lease2, "search_candidates", "stable-input")
    reused = await state.mark_reused(pool, lease2, current["plan_revision"], "other", "stable-input", cached)
    assert reused.artifacts == saved.artifacts
    data = await state.get_task(pool, task["id"], email)
    assert data["steps"][0]["reused_from"]["attempt_id"] == str(aid)
    assert data["counters"]["step_attempts"] == 1
    events = await state.list_events(pool, task["id"], email)
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))


async def test_model_calls_are_atomic_and_never_reset_by_new_spec(owner):
    pool, email = owner
    task = await state.create_task(pool, email, "model", execution_mode="model")
    token = current_procurement_binding.set(
        {
            "task_id": task["id"],
            "owner_email": email,
            "spec_revision": task["spec_revision"],
        }
    )
    try:
        values = await asyncio.gather(
            *(state.reserve_model_call(pool, task["id"], email, "parse") for _ in range(35)),
            return_exceptions=True,
        )
    finally:
        current_procurement_binding.reset(token)
    assert sorted(x for x in values if isinstance(x, int)) == list(range(1, 25))
    assert sum(isinstance(x, state.BudgetExceeded) for x in values) == 11
    task = await state.confirm_spec(pool, task["id"], email, task["revision"], spec())
    assert task["counters"]["model_calls"] == 24
    with pytest.raises(state.BudgetExceeded):
        await state.queue_task(pool, task["id"], email, task["revision"])


async def test_005_is_repeatable_and_preserves_query_rows(owner):
    pool, email = owner
    task = await state.create_task(pool, email, "preserve")
    root = Path(__file__).resolve().parents[3]
    sql = (root / "docker/postgres/migrations/005_procurement_runtime.sql").read_text()
    await pool.execute(sql)
    await pool.execute(sql)
    assert (await state.get_task(pool, task["id"], email))["goal"] == "preserve"
    init = (root / "docker/postgres/init.sql").read_text()
    assert sql in init


async def test_atomic_model_gate_rejects_stale_spec_and_lease_without_consuming_calls(owner):
    pool, email = owner
    task = await state.create_task(pool, email, "模型采购", execution_mode="model")
    binding = {"task_id": task["id"], "owner_email": email, "spec_revision": 0}
    token = current_procurement_binding.set(binding)
    try:
        assert await state.reserve_model_call(pool, task["id"], email, "parse") == 1
        confirmed = await state.confirm_spec(pool, task["id"], email, task["revision"], spec())
        with pytest.raises(state.BudgetExceeded):
            await state.reserve_model_call(pool, task["id"], email, "parse")
    finally:
        current_procurement_binding.reset(token)
    await state.queue_task(pool, task["id"], email, confirmed["revision"])
    lease = await state.claim_task(pool, "first", task_id=task["id"])
    token = current_procurement_binding.set(
        {
            **binding,
            "spec_revision": lease.spec_revision,
            "lease_token": str(lease.token),
            "generation": lease.generation,
        }
    )
    try:
        assert await state.reserve_model_call(pool, task["id"], email, "planning") == 2
        await pool.execute(
            "UPDATE agent_tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=$1",
            lease.task_id,
        )
        successor = await state.claim_task(pool, "successor", task_id=task["id"])
        assert successor.generation > lease.generation
        with pytest.raises(state.BudgetExceeded):
            await state.reserve_model_call(pool, task["id"], email, "planning")
    finally:
        current_procurement_binding.reset(token)
    assert (await state.get_task(pool, task["id"], email))["counters"]["model_calls"] == 2


async def test_pause_is_checked_inside_step_and_planning_claim_transactions(owner):
    pool, email = owner
    task = await queued(pool, email)
    lease = await state.claim_task(pool, "worker", task_id=task["id"])
    await state.begin_planning(pool, lease)
    planned = await state.save_plan(pool, lease, simple_plan())
    paused = await state.request_pause(pool, task["id"], email, planned["revision"])
    assert paused["status"] == "pausing"
    with pytest.raises(state.TaskConflict, match="PAUSE_REQUESTED"):
        await state.begin_step(pool, lease, planned["plan_revision"], "find", "not-sent")
    with pytest.raises(state.TaskConflict, match="PAUSE_REQUESTED"):
        await state.begin_planning(pool, lease, repair=True)
    final = await state.get_task(pool, task["id"], email)
    assert final["counters"]["step_attempts"] == 0 and final["counters"]["planning_attempts"] == 1
