"""任务并发认领、跨实例恢复与伪完成防护，使用真实 PostgreSQL。"""

import asyncio

import pytest
import pytest_asyncio

from shared.context import current_user_email
from shared.task_acceptance import StepAcceptance, evidence_ids
from shared.task_state import (
    TaskPlan,
    claim_planning,
    claim_step,
    create_task,
    finish_step,
    load_task,
    save_plan,
)


@pytest_asyncio.fixture
async def owner(clean_db, monkeypatch):
    import shared.db

    monkeypatch.setattr(shared.db, "_pool", clean_db)
    await clean_db.execute(
        "INSERT INTO users(email,password_hash,name) VALUES('task-owner@example.test','hash','Owner')"
    )
    token = current_user_email.set("task-owner@example.test")
    yield clean_db
    current_user_email.reset(token)


async def test_task_claims_and_restarts_keep_completed_steps(owner):
    product = await owner.fetchval(
        "INSERT INTO products(name,description,category,price) "
        "VALUES('Headphones','test','Electronics',100) RETURNING id"
    )
    task = await create_task("比较两款耳机", ["不超过500元"])
    first, second = await asyncio.gather(claim_planning(task["id"], 0), claim_planning(task["id"], 0))
    claimed = first or second
    assert bool(first) != bool(second)
    plan = TaskPlan(
        goal=task["goal"],
        constraints=task["constraints"],
        steps=[
            {
                "id": "find",
                "service": "product-discovery",
                "instruction": "查商品",
                "acceptance": {"kind": "product_selection"},
            },
            {
                "id": "stock",
                "service": "inventory-fulfillment",
                "instruction": "查库存",
                "depends_on": ["find"],
                "acceptance": {"kind": "inventory_check", "from_steps": ["find"]},
            },
        ],
    )
    assert await save_plan(task["id"], claimed["revision"], plan)
    saved = await load_task(task["id"])
    a, b = await asyncio.gather(claim_step(task["id"], saved["revision"]), claim_step(task["id"], saved["revision"]))
    assert bool(a) != bool(b)
    running, step = a or b
    assert await finish_step(
        task["id"],
        running["revision"],
        step,
        {
            "response": "找到商品",
            "steps": [
                {
                    "tool_name": "search_products",
                    "status": "success",
                    "result_confirmed": True,
                    "provenance": {"source": "tool:search_products", "row_ids": [str(product)]},
                }
            ],
        },
    )
    resumed = await load_task(task["id"])
    assert "find" in resumed["results"]
    _, next_step = await claim_step(task["id"], resumed["revision"])
    assert next_step.id == "stock"
    token = current_user_email.set("another@example.test")
    try:
        assert await load_task(task["id"]) is None
    finally:
        current_user_email.reset(token)


def test_plan_rejects_cycles_and_unregistered_services():
    with pytest.raises(ValueError):
        TaskPlan(goal="x", constraints=[], steps=[{"id": "x", "service": "shell", "instruction": "run"}])
    with pytest.raises(ValueError):
        TaskPlan(
            goal="x",
            constraints=[],
            steps=[{"id": "x", "service": "product-discovery", "instruction": "run", "depends_on": ["x"]}],
        )
    assert not evidence_ids({"response": "我已经完成了", "steps": []}, StepAcceptance(kind="product_selection"))
    assert not evidence_ids({"grounding": {"verified": 1, "unverified": 1}}, StepAcceptance(kind="product_selection"))
