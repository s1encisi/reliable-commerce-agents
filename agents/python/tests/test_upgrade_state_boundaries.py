"""跨轮约束、用户隔离、只读中断恢复及不安全 POST 不重试。"""

import httpx
import pytest
import pytest_asyncio

from shared.context import current_user_email
from shared.context_pipeline import persistent_context
from shared.http_resilience import ResilientAsyncTransport
from shared.task_state import TaskPlan, claim_planning, claim_step, create_task, load_task, recover_readonly, save_plan


@pytest_asyncio.fixture
async def owner(clean_db, monkeypatch):
    import shared.db

    monkeypatch.setattr(shared.db, "_pool", clean_db)
    user_id = await clean_db.fetchval(
        "INSERT INTO users(email,password_hash,name) VALUES('state@example.test','hash','Owner') RETURNING id"
    )
    token = current_user_email.set("state@example.test")
    yield clean_db, user_id
    current_user_email.reset(token)


async def test_constraints_survive_window_and_are_user_scoped(owner):
    pool, uid = owner
    conversation_id = await pool.fetchval(
        "INSERT INTO conversations(user_id,title) VALUES($1,'test') RETURNING id", uid
    )
    first = await persistent_context("预算不能超过500元", [], str(conversation_id))
    later = await persistent_context(
        "还有黑色的吗", [{"role": "assistant", "content": "一般说明" * 500}] * 50, str(conversation_id)
    )
    assert later.constraints == first.constraints
    token = current_user_email.set("someone-else@example.test")
    try:
        with pytest.raises(PermissionError):
            await persistent_context("read", [], str(conversation_id))
    finally:
        current_user_email.reset(token)


async def test_interrupted_readonly_step_can_be_explicitly_recovered(owner):
    task = await create_task("查库存", ["不能写入"])
    claimed = await claim_planning(task["id"], 0)
    plan = TaskPlan(
        goal=task["goal"],
        constraints=task["constraints"],
        steps=[{"id": "stock", "service": "inventory-fulfillment", "instruction": "查库存"}],
    )
    await save_plan(task["id"], claimed["revision"], plan)
    ready = await load_task(task["id"])
    running, _ = await claim_step(task["id"], ready["revision"])
    assert not await recover_readonly(task["id"], ready["revision"])
    assert await recover_readonly(task["id"], running["revision"])
    assert (await load_task(task["id"]))["results"] == {}


async def test_unproven_post_is_not_retried_after_read_timeout(monkeypatch):
    calls = []

    async def lost_reply(self, request):
        calls.append(request)
        raise httpx.ReadTimeout("commit or side effect may have happened")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", lost_reply)
    transport = ResilientAsyncTransport()
    with pytest.raises(httpx.ReadTimeout):
        await transport.handle_async_request(httpx.Request("POST", "http://specialist/message:send", json={}))
    assert len(calls) == 1
    await transport.aclose()


async def test_verified_entity_references_survive_history_window(owner):
    from uuid import uuid4

    from shared.context_pipeline import remember_evidence

    pool, uid = owner
    cid = await pool.fetchval("INSERT INTO conversations(user_id,title) VALUES($1,'refs') RETURNING id", uid)
    await persistent_context("比较耳机", [], str(cid))
    product_id = str(uuid4())
    await remember_evidence(
        str(cid),
        [
            {
                "tool_name": "get_product_details",
                "status": "success",
                "provenance": {"source": "tool:get_product_details", "row_ids": [product_id]},
            }
        ],
    )
    next_context = await persistent_context("那款呢", [], str(cid))
    assert f"get_product_details:{product_id}" in next_context.resolved_entities
    assert next_context.evidence_refs
