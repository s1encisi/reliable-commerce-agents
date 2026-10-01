"""任务语义验收与恢复，使用 testcontainers 的真实隔离 PostgreSQL。"""

import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from fastapi import HTTPException

from shared.context import current_user_email
from shared.task_acceptance import StepAcceptance, TaskRequirements, verify_step
from shared.task_state import (
    PlanStep,
    TaskPlan,
    claim_planning,
    claim_step,
    confirm_constraints,
    create_task,
    edit_task,
    finish_step,
    load_task,
    recover_readonly,
    save_plan,
    task_operation_lock,
)


@pytest_asyncio.fixture
async def task_owner(clean_db, monkeypatch):
    import shared.db

    monkeypatch.setattr(shared.db, "_pool", clean_db)
    uid = await clean_db.fetchval(
        "INSERT INTO users(email,password_hash,name) VALUES('contracts@example.test','hash','Owner') RETURNING id"
    )
    ids = [uuid4(), uuid4()]
    for index, pid in enumerate(ids):
        await clean_db.execute(
            "INSERT INTO products(id,name,description,category,price,rating) VALUES($1,$2,'test','Electronics',$3,4.5)",
            pid,
            f"Candidate {index}",
            (index + 1) * 100,
        )
    token = current_user_email.set("contracts@example.test")
    yield clean_db, uid, ids
    current_user_email.reset(token)


def tool_result(tool, ids):
    return {
        "response": "已完成",
        "steps": [
            {
                "tool_name": tool,
                "status": "success",
                "result_confirmed": True,
                "provenance": {"source": f"tool:{tool}", "row_ids": [str(value) for value in ids]},
            }
        ],
    }


def task_data(requirements=None, constraints=None):
    return {"requirements": requirements or {}, "constraints": constraints or [], "results": {}}


def step_for(kind="product_selection", ids=None, **kwargs):
    service = (
        "inventory-fulfillment"
        if kind == "inventory_check"
        else ("order-management" if kind == "order_lookup" else "product-discovery")
    )
    return PlanStep(
        id="check",
        service=service,
        instruction="核实目标对象",
        acceptance=StepAcceptance(kind=kind, required_ids=ids or [], **kwargs),
    )


async def planned(task, step):
    claimed = await claim_planning(task["id"], task["revision"])
    plan = TaskPlan(goal=task["goal"], constraints=task["constraints"], steps=[step])
    assert await save_plan(task["id"], claimed["revision"], plan)
    return await load_task(task["id"])


async def test_unrelated_success_and_claim_counts_do_not_finish_step(task_owner):
    pool, _, ids = task_owner
    result = tool_result("recall_memories", ids)
    result["grounding"] = {"verified": 20, "unverified": 0}
    report = await verify_step(pool, task_data(), step_for(), result)
    assert report["passed"] is False
    assert report["errors"] == ["NO_RELEVANT_TOOL_EVIDENCE"]


async def test_required_objects_and_comparison_cardinality(task_owner):
    pool, _, ids = task_owner
    step = step_for("product_comparison", ids)
    report = await verify_step(pool, task_data(), step, tool_result("get_product_details", ids[:1]))
    assert not report["passed"]
    assert "REQUIRED_ENTITIES_MISSING" in report["errors"]
    one = await verify_step(pool, task_data(), step_for("product_comparison"), tool_result("compare_products", ids[:1]))
    assert "INSUFFICIENT_ENTITIES" in one["errors"]


async def test_multiple_details_are_valid_comparison_path(task_owner):
    pool, _, ids = task_owner
    result = tool_result("get_product_details", ids[:1])
    result["steps"] += tool_result("get_product_details", ids[1:])["steps"]
    report = await verify_step(pool, task_data({"max_price": 250}), step_for("product_comparison", ids), result)
    assert report["passed"]
    assert {row["id"] for row in report["evidence"]} == {str(value) for value in ids}
    assert [row["price"] for row in sorted(report["evidence"], key=lambda x: x["price"])] == [100, 200]


@pytest.mark.parametrize(
    ("requirements", "error"),
    [
        ({"max_price": 150}, "PRICE_LIMIT_EXCEEDED"),
        ({"min_rating": 4.8}, "RATING_BELOW_MINIMUM"),
        ({"category": "Books"}, "CATEGORY_MISMATCH"),
        ({"in_stock": True}, "STOCK_REQUIREMENT_NOT_MET"),
    ],
)
async def test_database_facts_enforce_user_requirements(task_owner, requirements, error):
    pool, _, ids = task_owner
    result = tool_result("compare_products", ids)
    result["response"] = "两件均只要1元、五星、图书且有库存"
    report = await verify_step(pool, task_data(requirements), step_for("product_comparison", ids), result)
    assert not report["passed"]
    assert error in report["errors"]


async def test_explicit_selection_can_ignore_unselected_search_candidates(task_owner):
    pool, _, ids = task_owner
    report = await verify_step(
        pool,
        task_data({"max_price": 150, "product_ids": [str(ids[0])]}),
        step_for(),
        tool_result("search_products", ids),
    )
    assert report["passed"]
    assert [row["id"] for row in report["evidence"]] == [str(ids[0])]


async def test_inventory_requires_inventory_tool_and_dependency_entities(task_owner):
    pool, _, ids = task_owner
    task = task_data()
    task["results"]["find"] = {
        "valid_until": time.time() + 300,
        "verification": {"passed": True, "evidence": [{"id": str(ids[0])}]},
    }
    step = step_for("inventory_check", from_steps=["find"])
    step.depends_on = ["find"]
    report = await verify_step(pool, task, step, tool_result("get_product_details", ids))
    assert not report["passed"]
    wrong = await verify_step(pool, task, step, tool_result("check_stock", ids[1:]))
    assert "REQUIRED_ENTITIES_MISSING" in wrong["errors"]
    good = await verify_step(pool, task, step, tool_result("get_warehouse_availability", ids[:1]))
    assert good["passed"]
    task["results"]["find"]["valid_until"] = 0
    expired = await verify_step(pool, task, step, tool_result("check_stock", ids[:1]))
    assert "DEPENDENCY_EXPIRED" in expired["errors"]


async def test_order_evidence_is_rechecked_under_current_owner(task_owner):
    pool, _, _ = task_owner
    other = await pool.fetchval(
        "INSERT INTO users(email,password_hash,name) VALUES('other@example.test','hash','Other') RETURNING id"
    )
    oid = await pool.fetchval(
        "INSERT INTO orders(user_id,total,shipping_address) VALUES($1,100,'{}') RETURNING id", other
    )
    report = await verify_step(
        pool, task_data(), step_for("order_lookup", [oid]), tool_result("get_order_details", [oid])
    )
    assert not report["passed"]
    assert "ENTITY_NOT_FOUND_OR_NOT_AUTHORIZED" in report["errors"]
    assert report["evidence"] == []


async def test_denied_write_cannot_use_earlier_query_evidence(task_owner):
    pool, _, ids = task_owner
    result = tool_result("search_products", ids)
    result["requires_original_route"] = True
    assert not (await verify_step(pool, task_data(), step_for(), result))["passed"]


async def test_text_constraints_require_user_confirmation_and_edit_invalidates(task_owner):
    _, _, ids = task_owner
    task = await create_task("选商品", ["适合通勤"], TaskRequirements(max_price=250))
    ready = await planned(task, step_for(ids=ids))
    running, step = await claim_step(task["id"], ready["revision"])
    assert await finish_step(task["id"], running["revision"], step, tool_result("search_products", ids))
    review = await load_task(task["id"])
    assert review["status"] == "needs_review"
    assert not await confirm_constraints(task["id"], review["revision"] - 1)
    other_token = current_user_email.set("other@example.test")
    try:
        assert not await confirm_constraints(task["id"], review["revision"])
    finally:
        current_user_email.reset(other_token)
    assert await confirm_constraints(task["id"], review["revision"])
    complete = await load_task(task["id"])
    assert complete["status"] == "completed"
    assert complete["constraint_confirmation"]["source_kind"] == "user_confirmed"
    assert await edit_task(task["id"], complete["revision"], "换预算", [], TaskRequirements(max_price=150))
    edited = await load_task(task["id"])
    assert edited["plan"] is None and edited["results"] == {}
    assert edited["constraint_confirmation"] is None
    assert edited["planning_attempts"] == 1
    assert edited["receipt_history"][-1]["constraint_confirmation"]["source_kind"] == "user_confirmed"


async def test_free_goal_needs_review_and_failed_evidence_is_retained(task_owner):
    _, _, ids = task_owner
    task = await create_task("选商品", [], TaskRequirements(max_price=150))
    ready = await planned(task, step_for())
    running, step = await claim_step(task["id"], ready["revision"])
    assert await finish_step(task["id"], running["revision"], step, tool_result("search_products", ids))
    rejected = await load_task(task["id"])
    assert rejected["status"] == "needs_replan" and rejected["results"] == {}
    assert "PRICE_LIMIT_EXCEEDED" in rejected["receipt_history"][-1]["receipt"]["verification"]["errors"]
    task2 = await create_task("选商品", [], TaskRequirements(product_ids=ids[:1], max_price=150))
    ready2 = await planned(task2, step_for())
    running2, step2 = await claim_step(task2["id"], ready2["revision"])
    assert await finish_step(task2["id"], running2["revision"], step2, tool_result("search_products", ids))
    review = await load_task(task2["id"])
    assert review["status"] == "needs_review"
    assert review["review_items"] == ["任务目标：选商品"]


async def test_expired_receipts_cannot_be_confirmed(task_owner):
    pool, _, ids = task_owner
    task = await create_task("选商品", ["通勤"])
    ready = await planned(task, step_for())
    running, step = await claim_step(task["id"], ready["revision"])
    await finish_step(task["id"], running["revision"], step, tool_result("search_products", ids))
    review = await load_task(task["id"])
    results = review["results"]
    results["check"]["valid_until"] = 0
    await pool.execute("UPDATE agent_tasks SET results=$2::jsonb WHERE id=$1", task["id"], json.dumps(results))
    assert not await confirm_constraints(task["id"], review["revision"])
    fresh = await load_task(task["id"])
    assert fresh["status"] == "ready" and fresh["results"] == {}


async def test_planning_recovery_preserves_attempt_limit_and_fences_old_result(task_owner):
    task = await create_task("查询", [])
    claim = await claim_planning(task["id"], 0)
    async with task_operation_lock(task["id"]) as acquired:
        assert acquired
        assert await recover_readonly(task["id"], claim["revision"])
    recovered = await load_task(task["id"])
    plan = TaskPlan(goal="查询", constraints=[], steps=[step_for()])
    assert not await save_plan(task["id"], claim["revision"], plan)
    second = await claim_planning(task["id"], recovered["revision"], replan=True)
    assert second["planning_attempts"] == 2
    async with task_operation_lock(task["id"]):
        assert await recover_readonly(task["id"], second["revision"])
    recovered2 = await load_task(task["id"])
    assert await claim_planning(task["id"], recovered2["revision"], replan=True) is None


async def test_live_planning_cannot_be_recovered_and_cancellation_releases_lock(task_owner, monkeypatch):
    from orchestrator.routes import tasks

    task = await create_task("查询", [])
    started = asyncio.Event()

    async def planning(_):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(tasks, "generate_plan", planning)
    running = asyncio.create_task(tasks.plan_task(task["id"], tasks.Revision(revision=0), {}))
    await started.wait()
    current = await load_task(task["id"])
    with pytest.raises(HTTPException) as error:
        await tasks.recover_task(task["id"], tasks.Revision(revision=current["revision"]), {})
    assert error.value.status_code == 409
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    cancelled = await load_task(task["id"])
    assert cancelled["status"] == "needs_replan" and cancelled["planning_attempts"] == 1
    async with task_operation_lock(task["id"]) as acquired:
        assert acquired


async def test_dead_connection_releases_task_lock(task_owner, database_url):
    import hashlib

    task = await create_task("查询", [])
    claimed = await claim_planning(task["id"], 0)
    connection = await asyncpg.connect(database_url)
    key = int.from_bytes(hashlib.sha256(f"agent-task:{task['id']}".encode()).digest()[:8], signed=True)
    await connection.execute("SELECT pg_advisory_lock($1)", key)
    async with task_operation_lock(task["id"]) as acquired:
        assert not acquired
    await connection.close()
    async with task_operation_lock(task["id"]) as acquired:
        assert acquired
        assert await recover_readonly(task["id"], claimed["revision"])


async def test_task_acceptance_migration_is_repeatable_and_preserves_data(task_owner):
    pool, _, _ = task_owner
    task = await create_task("保留目标", ["保留约束"])
    root = Path(__file__).resolve().parents[3]
    sql = (root / "docker/postgres/migrations/003_task_acceptance.sql").read_text()
    await pool.execute(sql)
    await pool.execute(sql)
    assert (await load_task(task["id"]))["goal"] == "保留目标"


def test_contract_rejects_unknown_kinds_wrong_service_and_unbound_dependency():
    with pytest.raises(ValueError):
        TaskPlan(
            goal="x",
            constraints=[],
            steps=[
                {
                    "id": "x",
                    "service": "product-discovery",
                    "instruction": "x",
                    "acceptance": {"kind": "inventory_check", "from_steps": ["not-a-dependency"]},
                }
            ],
        )
    with pytest.raises(ValueError):
        TaskRequirements(max_price=float("nan"))


async def test_http_demo_uses_real_database_then_requires_explicit_confirmation(task_owner, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from orchestrator.routes import tasks
    from shared.task_planner import DEMO_TASK_GOAL

    monkeypatch.setattr(tasks.settings, "LLM_PROVIDER", "replay")

    async def unexpected_network(*args, **kwargs):
        raise AssertionError("Fixed demonstration must not call a model or specialist HTTP service")

    monkeypatch.setattr(tasks, "dispatch_readonly", unexpected_network)
    app = FastAPI()
    app.include_router(tasks.router)
    app.dependency_overrides[tasks.require_auth] = lambda: {"sub": "contracts@example.test", "role": "customer"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/tasks",
            json={
                "goal": DEMO_TASK_GOAL,
                "constraints": ["适合通勤"],
                "requirements": {"min_items": 2, "max_price": 250, "compare": True},
            },
        )
        assert response.status_code == 200
        task = response.json()
        response = await client.post(f"/api/tasks/{task['id']}/plan", json={"revision": task["revision"]})
        assert response.status_code == 200
        task = response.json()
        assert task["plan"]["source"] == "deterministic_demo"
        stale = await client.post(f"/api/tasks/{task['id']}/next", json={"revision": 0})
        assert stale.status_code == 409
        for _ in range(3):
            response = await client.post(f"/api/tasks/{task['id']}/next", json={"revision": task["revision"]})
            assert response.status_code == 200
            task = response.json()
        assert task["status"] == "needs_review"
        prices = [row["price"] for row in task["results"]["select"]["verification"]["evidence"]]
        assert sorted(prices) == [100, 200]
        invalid = await client.post(
            f"/api/tasks/{task['id']}/confirm", json={"revision": task["revision"], "confirmed": False}
        )
        assert invalid.status_code == 422
        response = await client.post(
            f"/api/tasks/{task['id']}/confirm", json={"revision": task["revision"], "confirmed": True}
        )
        assert response.status_code == 200
        task = response.json()
        assert task["status"] == "completed"
        response = await client.patch(
            f"/api/tasks/{task['id']}",
            json={
                "revision": task["revision"],
                "goal": DEMO_TASK_GOAL,
                "constraints": [],
                "requirements": {"min_items": 2, "max_price": 100},
            },
        )
        assert response.status_code == 200
        edited = response.json()
        assert edited["results"] == {} and edited["planning_attempts"] == 1
        assert edited["constraint_confirmation"] is None


async def test_plan_must_cover_structured_comparison_and_inventory_requirements(task_owner):
    task = await create_task("对比并查库存", [], TaskRequirements(compare=True, in_stock=True))
    claimed = await claim_planning(task["id"], 0)
    with pytest.raises(ValueError, match="比较"):
        await save_plan(
            task["id"], claimed["revision"], TaskPlan(goal=task["goal"], constraints=[], steps=[step_for()])
        )
    compare = step_for("product_comparison")
    with pytest.raises(ValueError, match="库存"):
        await save_plan(task["id"], claimed["revision"], TaskPlan(goal=task["goal"], constraints=[], steps=[compare]))


async def test_finish_uses_saved_contract_not_caller_supplied_relaxation(task_owner):
    _, _, ids = task_owner
    task = await create_task("对比指定对象", [])
    ready = await planned(task, step_for("product_comparison", ids))
    running, step = await claim_step(task["id"], ready["revision"])
    step.acceptance = StepAcceptance(kind="product_selection", min_items=1)
    await finish_step(task["id"], running["revision"], step, tool_result("search_products", ids))
    result = await load_task(task["id"])
    assert result["status"] == "needs_replan"
    assert result["receipt_history"][-1]["receipt"]["verification"]["errors"] == ["NO_RELEVANT_TOOL_EVIDENCE"]


async def test_task_locks_reserve_capacity_for_database_tools(task_owner):
    from contextlib import AsyncExitStack

    pool, _, _ = task_owner
    async with AsyncExitStack() as stack:
        for _ in range(pool.get_max_size() - 1):
            assert await stack.enter_async_context(task_operation_lock(uuid4()))
        assert not await stack.enter_async_context(task_operation_lock(uuid4()))
        assert await asyncio.wait_for(pool.fetchval("SELECT 1"), timeout=1) == 1
    async with task_operation_lock(uuid4()) as acquired:
        assert acquired


async def test_free_comparison_goal_cannot_be_proven_by_single_selection(task_owner):
    _, _, ids = task_owner
    task = await create_task("比较两款红色耳机并解释通勤适用性", [])
    ready = await planned(task, step_for())
    running, step = await claim_step(task["id"], ready["revision"])
    await finish_step(task["id"], running["revision"], step, tool_result("get_product_details", ids[:1]))
    review = await load_task(task["id"])
    assert review["status"] == "needs_review"
    assert review["results"]["check"]["verification"]["passed"] is True
    assert review["review_items"] == ["任务目标：比较两款红色耳机并解释通勤适用性"]
    assert await confirm_constraints(task["id"], review["revision"])
    confirmed = await load_task(task["id"])
    assert confirmed["constraint_confirmation"]["goal"] == task["goal"]
    assert confirmed["constraint_confirmation"]["review_items"] == review["review_items"]


async def test_exact_fixed_demo_can_complete_without_text_review(task_owner):
    from shared.task_planner import DEMO_TASK_GOAL, demo_plan

    task = await create_task(DEMO_TASK_GOAL, [], TaskRequirements(min_items=2))
    claim = await claim_planning(task["id"], 0)
    assert await save_plan(task["id"], claim["revision"], demo_plan(task))
    ready = await load_task(task["id"])
    ids = task_owner[2]
    running, step = await claim_step(task["id"], ready["revision"])
    await finish_step(task["id"], running["revision"], step, tool_result("search_products", ids))
    ready = await load_task(task["id"])
    running, step = await claim_step(task["id"], ready["revision"])
    await finish_step(task["id"], running["revision"], step, tool_result("check_stock", ids))
    completed = await load_task(task["id"])
    assert completed["status"] == "completed" and completed["review_items"] == []
