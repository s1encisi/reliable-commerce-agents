"""Procurement lifecycle through real MAF plus real PostgreSQL, including process death."""

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from shared.procurement import state
from shared.procurement.models import Plan, PlanStep, ProcurementSpec, StepOutcome
from shared.procurement.runner import ProcurementRunner


@pytest_asyncio.fixture
async def owner(clean_db):
    await clean_db.execute(
        "INSERT INTO users(email,password_hash,name,role) VALUES('runner@example.test','hash','Owner','customer')"
    )
    return clean_db, "runner@example.test"


def plan(*, repair=False):
    return Plan(
        source="deterministic_demo",
        steps=[
            PlanStep(id="discover", capability="search_candidates"),
            PlanStep(id="quote", capability="quote_options", depends_on=["discover"]),
            PlanStep(id="stock", capability="check_inventory", depends_on=["discover"]),
            PlanStep(id="reviews", capability="review_evidence", depends_on=["discover"]),
        ],
    )


def result(capability, **data):
    now = datetime.now(UTC)
    return StepOutcome(
        status="succeeded",
        source="offline",
        data={
            "capability": capability,
            "source": "tool:" + capability,
            "items": [{"product_id": "11111111-1111-4111-8111-111111111111"}],
            "observed_at": now.isoformat(),
            "valid_until": (now + timedelta(minutes=5)).isoformat(),
            **data,
        },
    )


async def create_queued(pool, email, *, mode="offline"):
    task = await state.create_task(pool, email, "采购耳机", execution_mode=mode)
    task = await state.confirm_spec(
        pool,
        task["id"],
        email,
        task["revision"],
        ProcurementSpec(query="耳机", quantity=2, total_budget="500"),
    )
    return await state.queue_task(pool, task["id"], email, task["revision"])


async def planner(task, repair=False, failures=None):
    return plan(repair=repair)


async def finalizer(task, outcomes):
    assert all(value.status == "succeeded" for value in outcomes.values())
    return {"status": "completed", "options": [{"checked": True}]}


def key(task, step, dependencies):
    return step.capability


async def test_parallelism_two_fresh_identity_and_safe_checkpoints(owner):
    from shared.context import current_user_email, current_user_role
    from shared.execution_policy import current_execution_policy
    from shared.task_state import _task_connection

    pool, email = owner
    task = await create_queued(pool, email)
    active = maximum = 0
    seen = []

    async def dispatch(task, step, dependencies):
        nonlocal active, maximum
        assert current_user_email.get() == email
        assert current_user_role.get() == "customer"
        assert current_execution_policy.get() == "read_only"
        assert _task_connection.get() is None
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.015)
        active -= 1
        seen.append(step.id)
        return result(step.capability)

    user_token = current_user_email.set("wrong-http-user@example.test")
    connection_token = _task_connection.set(object())
    try:
        runner = ProcurementRunner(pool, planner, dispatch, finalizer, enabled=True, reuse_key=key)
        assert await runner.run_once(task["id"])
    finally:
        current_user_email.reset(user_token)
        _task_connection.reset(connection_token)
    finished = await state.get_task(pool, task["id"], email)
    assert finished["status"] == "completed", finished["stop_reason"]
    assert set(seen) == {"discover", "quote", "stock", "reviews"}
    assert maximum == 2
    assert finished["checkpoint_layer"] == 1 and finished["latest_checkpoint_id"]
    assert finished["checkpoint_plan_hash"] == finished["plan_hash"]
    assert finished["counters"]["step_attempts"] == 4


async def test_pause_waits_for_inflight_then_resume_reuses_receipts(owner):
    pool, email = owner
    task = await create_queued(pool, email)
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def dispatch(task, step, dependencies):
        calls.append(step.id)
        if step.id == "quote":
            started.set()
            await release.wait()
        return result(step.capability)

    runner = ProcurementRunner(pool, planner, dispatch, finalizer, enabled=True, reuse_key=key, concurrency=1)
    work = asyncio.create_task(runner.run_once(task["id"]))
    await asyncio.wait_for(started.wait(), 5)
    latest = await state.get_task(pool, task["id"], email)
    paused = await state.request_pause(pool, task["id"], email, latest["revision"])
    assert paused["status"] == "pausing"
    assert not work.done()
    release.set()
    await work
    paused = await state.get_task(pool, task["id"], email)
    assert paused["status"] == "paused"
    assert calls == ["discover", "quote"]
    await state.queue_task(pool, task["id"], email, paused["revision"])
    await runner.run_once(task["id"])
    finished = await state.get_task(pool, task["id"], email)
    assert finished["status"] == "completed"
    assert calls == ["discover", "quote", "stock", "reviews"]
    assert finished["counters"]["planning_attempts"] == 1


async def test_one_repair_retains_unaffected_proofs(owner):
    pool, email = owner
    task = await create_queued(pool, email)
    calls = {}
    received_failures = []

    async def planning(task, repair=False, failures=None):
        if repair:
            received_failures.extend(failures)
            assert task["previous_plan"] and task["receipt_history"]
        return plan(repair=repair)

    async def dispatch(task, step, dependencies):
        calls[step.id] = calls.get(step.id, 0) + 1
        if step.id == "stock" and calls[step.id] == 1:
            return StepOutcome(
                status="failed",
                error_code="INSUFFICIENT_STOCK",
                data={
                    "affected_product_ids": ["11111111-1111-4111-8111-111111111111"],
                },
            )
        return result(step.capability)

    runner = ProcurementRunner(pool, planning, dispatch, finalizer, enabled=True, reuse_key=key)
    await runner.run_once(task["id"])
    finished = await state.get_task(pool, task["id"], email)
    assert finished["status"] == "completed"
    assert calls == {"discover": 1, "quote": 1, "stock": 2, "reviews": 1}
    assert finished["counters"]["repairs_used"] == 1
    assert finished["counters"]["planning_attempts"] == 2
    assert received_failures[0]["code"] == "INSUFFICIENT_STOCK"


async def test_final_oracle_change_refreshes_only_affected_quote(owner):
    pool, email = owner
    task = await create_queued(pool, email)
    calls = {}
    final_calls = 0

    async def dispatch(task, step, dependencies):
        calls[step.id] = calls.get(step.id, 0) + 1
        return result(step.capability)

    async def final(task, outcomes):
        nonlocal final_calls
        final_calls += 1
        if final_calls == 1:
            return {
                "status": "needs_replan",
                "options": [],
                "failures": [
                    {
                        "code": "PRICE_CHANGED",
                        "product_id": "11111111-1111-4111-8111-111111111111",
                        "capabilities": ["quote_options"],
                    }
                ],
            }
        return await finalizer(task, outcomes)

    runner = ProcurementRunner(pool, planner, dispatch, final, enabled=True, reuse_key=key)
    await runner.run_once(task["id"])
    finished = await state.get_task(pool, task["id"], email)
    assert finished["status"] == "completed"
    assert calls["quote"] == 2 and all(calls[name] == 1 for name in ("discover", "stock", "reviews"))


async def test_repair_limit_cannot_be_reset_by_pause_start(owner):
    pool, email = owner
    task = await create_queued(pool, email)

    async def dispatch(task, step, dependencies):
        return StepOutcome(status="failed", error_code="NO_DATA")

    runner = ProcurementRunner(pool, planner, dispatch, finalizer, enabled=True, reuse_key=key)
    await runner.run_once(task["id"])
    stopped = await state.get_task(pool, task["id"], email)
    assert stopped["status"] == "needs_review" and stopped["stop_reason"] == "REPAIR_LIMIT"
    paused = await state.request_pause(pool, task["id"], email, stopped["revision"])
    with pytest.raises(state.TaskConflict):
        await state.queue_task(pool, task["id"], email, paused["revision"])


async def test_model_unauthorized_never_calls_planner(owner):
    pool, email = owner
    task = await create_queued(pool, email, mode="model")

    async def forbidden(*args, **kwargs):
        raise AssertionError("must never call model")

    runner = ProcurementRunner(pool, forbidden, forbidden, enabled=True, model_authorized=False)
    await runner.run_once(task["id"])
    stopped = await state.get_task(pool, task["id"], email)
    assert stopped["status"] == "failed" and stopped["stop_reason"] == "MODEL_NOT_AUTHORIZED"
    assert stopped["counters"]["model_calls"] == 0


async def test_process_death_recovery_does_not_repeat_confirmed_step(owner, database_url):
    pool, email = owner
    task = await create_queued(pool, email)
    code = """
import asyncio,os
import asyncpg
from shared.procurement.runner import ProcurementRunner
from shared.procurement.models import Plan,PlanStep,StepOutcome
async def main():
    pool=await asyncpg.create_pool(os.environ['PROCUREMENT_TEST_DSN'])
    async def planner(task,repair=False,failures=None):
        return Plan(source='deterministic_demo',steps=[
            PlanStep(id='discover',capability='search_candidates'),
            PlanStep(id='stock',capability='check_inventory',depends_on=['discover'])])
    async def dispatch(task,step,deps):
        return StepOutcome(status='succeeded',data={'items':[{'product_id':'11111111-1111-4111-8111-111111111111'}]})
    async def hook(name,task,payload):
        if name=='after_discovery':
            os._exit(23)
    runner=ProcurementRunner(pool,planner,dispatch,enabled=True,event_hook=hook,lease_seconds=.4,heartbeat_seconds=.1,
                             reuse_key=lambda task,step,deps:step.capability)
    await runner.run_once(os.environ['PROCUREMENT_TEST_TASK'])
asyncio.run(main())
"""
    env = {
        **os.environ,
        "PROCUREMENT_TEST_DSN": database_url,
        "PROCUREMENT_TEST_TASK": task["id"],
        "APP_ENV_FILE": "/dev/null",
        "RECORD": "false",
        "LLM_PROVIDER": "replay",
        "OPENAI_API_KEY": "",
        "AZURE_OPENAI_API_KEY": "",
        "DEEPSEEK_API_KEY": "",
        "MOONSHOT_API_KEY": "",
        "TYPESAFE_API_KEY": "",
    }
    completed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", code],
        env=env,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 23, completed.stderr
    await asyncio.sleep(0.5)
    calls = []

    async def dispatch(task, step, dependencies):
        calls.append(step.id)
        return result(step.capability)

    restored = ProcurementRunner(
        pool,
        planner,
        dispatch,
        finalizer,
        enabled=True,
        reuse_key=key,
        lease_seconds=1,
        heartbeat_seconds=0.2,
    )
    await restored.run_once(task["id"])
    finished = await state.get_task(pool, task["id"], email)
    assert finished["status"] == "completed", finished["stop_reason"]
    assert calls == ["stock"]
    assert (
        await pool.fetchval(
            "SELECT COUNT(*) FROM procurement_attempts WHERE task_id=$1 AND step_id='discover'",
            state._uid(task["id"]),
        )
        == 1
    )


async def test_expired_parent_rewinds_checkpoint_but_reuses_valid_child_evidence(owner):
    pool, email = owner
    task = await create_queued(pool, email)
    started, release = asyncio.Event(), asyncio.Event()
    calls = {}

    async def dispatch(current, step, dependencies):
        calls[step.id] = calls.get(step.id, 0) + 1
        if step.id == "quote" and calls[step.id] == 1:
            started.set()
            await release.wait()
        if step.id in {"stock", "reviews"}:
            assert dependencies["discover"].data["discovery_version"] == 2
        return result(step.capability, discovery_version=calls[step.id])

    runner = ProcurementRunner(pool, planner, dispatch, finalizer, enabled=True, reuse_key=key, concurrency=1)
    running = asyncio.create_task(runner.run_once(task["id"]))
    await asyncio.wait_for(started.wait(), 5)
    current = await state.get_task(pool, task["id"], email)
    await state.request_pause(pool, task["id"], email, current["revision"])
    release.set()
    await running
    paused = await state.get_task(pool, task["id"], email)
    assert paused["status"] == "paused" and paused["checkpoint_layer"] == 0
    quote_before = next(row for row in paused["steps"] if row["id"] == "quote")["outcome"]["artifacts"]
    await pool.execute(
        "UPDATE procurement_steps SET valid_until=clock_timestamp()-interval '1 second' "
        "WHERE task_id=$1 AND step_id='discover'",
        state._uid(task["id"]),
    )
    await state.queue_task(pool, task["id"], email, paused["revision"])
    await runner.run_once(task["id"])
    completed = await state.get_task(pool, task["id"], email)
    assert completed["status"] == "completed", completed["stop_reason"]
    assert calls == {"discover": 2, "quote": 1, "stock": 1, "reviews": 1}
    assert completed["counters"]["repairs_used"] == 0
    assert completed["counters"]["planning_attempts"] == 1
    assert completed["results"]["quote"]["artifacts"] == quote_before
    invalidations = [entry for entry in completed["receipt_history"] if entry["kind"] == "evidence_expired"]
    assert invalidations[-1]["expired_steps"] == ["discover"]


async def test_final_oracle_receipt_is_durable_and_removed_on_non_success(owner):
    pool, email = owner
    task = await create_queued(pool, email)
    should_pass = True

    async def dispatch(task, step, dependencies):
        return result(step.capability)

    async def final(task, outcomes):
        return {
            "status": "completed" if should_pass else "needs_review",
            "options": [{"checked": should_pass}],
            "verification": {"passed": should_pass, "validation_scope": ["quantity", "budget", "inventory"]},
        }

    runner = ProcurementRunner(pool, planner, dispatch, final, enabled=True, reuse_key=key)
    await runner.run_once(task["id"])
    completed = await state.get_task(pool, task["id"], email)
    receipt = completed["results"]["_final_verification"]
    assert receipt["passed"] and receipt["verified_at"] and receipt["recorded_at"]
    assert receipt["validation_scope"] == ["quantity", "budget", "inventory"]
    assert receipt["plan_hash"] == completed["plan_hash"]
    paused = await state.request_pause(pool, task["id"], email, completed["revision"])
    await state.queue_task(pool, task["id"], email, paused["revision"])
    should_pass = False
    await runner.run_once(task["id"])
    stopped = await state.get_task(pool, task["id"], email)
    assert stopped["status"] == "needs_review"
    assert "_final_verification" not in stopped["results"]
    assert any(entry["kind"] == "final_verification_retired" for entry in stopped["receipt_history"])


@pytest.mark.parametrize("code", ["POLICY_REJECTED", "STALE_PLAN", "SERVICE_UNAVAILABLE"])
async def test_policy_failures_stop_new_nodes_without_retry_or_repair(owner, code):
    pool, email = owner
    task = await create_queued(pool, email)
    calls = []

    async def dispatch(task, step, dependencies):
        calls.append(step.id)
        if step.id == "quote":
            return StepOutcome(status="failed", error_code=code, retryable=True)
        return result(step.capability)

    runner = ProcurementRunner(pool, planner, dispatch, finalizer, enabled=True, reuse_key=key, concurrency=1)
    await runner.run_once(task["id"])
    stopped = await state.get_task(pool, task["id"], email)
    assert stopped["status"] == "failed" and stopped["stop_reason"] == code
    assert calls == ["discover", "quote"]
    assert stopped["counters"]["planning_attempts"] == 1 and stopped["counters"]["repairs_used"] == 0


@pytest.mark.parametrize("recovers", [True, False])
async def test_known_temporary_failure_gets_one_step_retry_without_semantic_repair(owner, recovers):
    pool, email = owner
    task = await create_queued(pool, email)
    calls = {}

    async def dispatch(task, step, dependencies):
        calls[step.id] = calls.get(step.id, 0) + 1
        if step.id == "quote" and (calls[step.id] == 1 or not recovers):
            return StepOutcome(status="failed", error_code="TEMPORARY_DEPENDENCY", retryable=True)
        return result(step.capability)

    runner = ProcurementRunner(pool, planner, dispatch, finalizer, enabled=True, reuse_key=key)
    await runner.run_once(task["id"])
    completed = await state.get_task(pool, task["id"], email)
    assert calls["quote"] == 2
    assert completed["counters"]["planning_attempts"] == 1 and completed["counters"]["repairs_used"] == 0
    if recovers:
        assert completed["status"] == "completed"
    else:
        assert completed["status"] == "needs_review"
        assert completed["stop_reason"] == "TRANSIENT_RETRY_EXHAUSTED"


@pytest.mark.parametrize("phase", ["planner", "dispatcher"])
async def test_campaign_budget_error_has_explicit_budget_state(owner, phase):
    from shared.campaign_budget import BudgetError

    pool, email = owner
    task = await create_queued(pool, email)

    async def planning(task, repair=False, failures=None):
        if phase == "planner":
            raise BudgetError("reserved monetary budget exhausted")
        return plan()

    async def dispatch(task, step, dependencies):
        raise BudgetError("reserved monetary budget exhausted")

    runner = ProcurementRunner(pool, planning, dispatch, finalizer, enabled=True)
    await runner.run_once(task["id"])
    stopped = await state.get_task(pool, task["id"], email)
    assert stopped["status"] == "budget_exhausted"
    assert stopped["stop_reason"] == "MODEL_CALL_LIMIT"
    assert stopped["counters"]["repairs_used"] == 0


async def test_unknown_result_is_not_replanned_as_a_business_failure(owner):
    pool, email = owner
    task = await create_queued(pool, email)

    async def dispatch(task, step, dependencies):
        return StepOutcome(status="unknown", error_code="READ_TIMEOUT", retryable=True)

    runner = ProcurementRunner(pool, planner, dispatch, finalizer, enabled=True)
    await runner.run_once(task["id"])
    stopped = await state.get_task(pool, task["id"], email)
    assert stopped["status"] == "needs_review" and stopped["stop_reason"] == "UNKNOWN_OUTCOME"
    assert stopped["counters"]["step_attempts"] == 1
    assert stopped["counters"]["planning_attempts"] == 1 and stopped["counters"]["repairs_used"] == 0


async def test_serve_logs_transient_worker_exception_then_continues(monkeypatch, caplog):
    runner = ProcurementRunner(None, None, None, enabled=True)
    stop = asyncio.Event()
    calls = 0

    async def iteration():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary claim connection error")
        stop.set()
        return False

    monkeypatch.setattr(runner, "run_once", iteration)
    await asyncio.wait_for(runner.serve(stop), 3)
    assert calls == 2
    assert "procurement.worker_iteration_failed" in caplog.text
