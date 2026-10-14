"""真实PaidTransport发送边界的采购计数；网络使用内存MockTransport、账本为临时文件。"""

from decimal import Decimal

import httpx
import pytest

from shared.campaign_budget import BudgetError, CampaignBudget, Price
from shared.paid_transport import PaidTransport, current_call_purpose, current_root_run
from shared.procurement import state
from shared.procurement.models import ProcurementSpec, current_procurement_binding

OWNER = "budget-procurement@example.test"


@pytest.fixture
async def model_task(clean_db, monkeypatch, tmp_path):
    import shared.config
    import shared.db
    import shared.procurement.model_io as model_io

    config = shared.config.Settings(
        _env_file=None,
        PROCUREMENT_ENABLED=True,
        PROCUREMENT_MODEL_AUTHORIZED=True,
        PROCUREMENT_MODE="model",
        JWT_SECRET="j" * 40,
        AGENT_SHARED_SECRET="s" * 40,
    )
    monkeypatch.setattr(shared.config, "settings", config)
    monkeypatch.setattr(model_io, "settings", config)
    monkeypatch.setattr(shared.db, "_pool", clean_db)
    await clean_db.execute("INSERT INTO users(email,password_hash,name) VALUES($1,'unused','Budget test')", OWNER)
    task = await state.create_task(clean_db, OWNER, "采购耳机", execution_mode="model")
    tokens = [
        (
            current_procurement_binding,
            current_procurement_binding.set({"task_id": task["id"], "owner_email": OWNER, "spec_revision": 0}),
        ),
        (current_root_run, current_root_run.set(task["id"])),
        (current_call_purpose, current_call_purpose.set("planning")),
    ]
    requests = []

    async def send(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3}})

    transport = PaidTransport(
        "deepseek",
        budget=CampaignBudget(tmp_path / "test-only-budget.json"),
        price=Price("CNY", Decimal("2"), Decimal("8"), "test-only", "2099-01-01"),
        inner=httpx.MockTransport(send),
    )
    try:
        async with httpx.AsyncClient(transport=transport) as client:
            yield task, clean_db, config, client, requests
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


async def request(client):
    return await client.post(
        "https://api.deepseek.com/chat/completions",
        json={"messages": [{"role": "user", "content": "test"}], "max_tokens": 20},
    )


async def test_unauthorized_model_mode_never_reaches_http(model_task):
    task, pool, config, client, requests = model_task
    config.PROCUREMENT_MODEL_AUTHORIZED = False
    with pytest.raises(BudgetError):
        await request(client)
    assert not requests
    assert (await state.get_task(pool, task["id"], OWNER))["counters"]["model_calls"] == 0


async def test_twenty_four_call_limit_spans_confirmation_and_client_retries(model_task):
    task, pool, _, client, requests = model_task
    for _ in range(24):
        assert (await request(client)).status_code == 200
    updated = await state.get_task(pool, task["id"], OWNER)
    updated = await state.confirm_spec(
        pool, task["id"], OWNER, updated["revision"], ProcurementSpec(query="耳机", quantity=3, total_budget="1500")
    )
    token = current_procurement_binding.set({"task_id": task["id"], "owner_email": OWNER, "spec_revision": 1})
    try:
        with pytest.raises(BudgetError):
            await request(client)
    finally:
        current_procurement_binding.reset(token)
    assert len(requests) == 24
    assert updated["counters"]["model_calls"] == 24


async def test_old_spec_and_forged_owner_do_not_consume_or_send(model_task):
    task, pool, _, client, requests = model_task
    updated = await state.confirm_spec(
        pool, task["id"], OWNER, task["revision"], ProcurementSpec(query="耳机", quantity=3, total_budget="1500")
    )
    with pytest.raises(BudgetError):
        await request(client)
    token = current_procurement_binding.set(
        {"task_id": task["id"], "owner_email": "stranger@example.test", "spec_revision": 1}
    )
    try:
        with pytest.raises(BudgetError):
            await request(client)
    finally:
        current_procurement_binding.reset(token)
    assert not requests
    assert updated["counters"]["model_calls"] == 0


async def test_spec_change_between_precheck_and_reserve_sends_no_http(model_task, monkeypatch):
    task, pool, _, client, requests = model_task
    original_get = state.get_task
    changed = False

    async def racing_get(pool_arg, task_id, owner):
        nonlocal changed
        snapshot = await original_get(pool_arg, task_id, owner)
        if not changed:
            changed = True
            await state.confirm_spec(
                pool_arg,
                task_id,
                owner,
                snapshot["revision"],
                ProcurementSpec(query="耳机", quantity=3, total_budget="1200"),
            )
        return snapshot

    monkeypatch.setattr(state, "get_task", racing_get)
    with pytest.raises(BudgetError):
        await request(client)
    assert not requests
    assert (await original_get(pool, task["id"], OWNER))["counters"]["model_calls"] == 0


async def test_replaced_lease_cannot_issue_next_model_http(model_task):
    task, pool, _, client, requests = model_task
    task = await state.confirm_spec(
        pool, task["id"], OWNER, task["revision"], ProcurementSpec(query="耳机", quantity=3, total_budget="1500")
    )
    await state.queue_task(pool, task["id"], OWNER, task["revision"])
    first = await state.claim_task(pool, "first", task_id=task["id"])
    await pool.execute(
        "UPDATE agent_tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=$1", first.task_id
    )
    second = await state.claim_task(pool, "second", task_id=task["id"])
    assert second.generation > first.generation
    token = current_procurement_binding.set(
        {
            "task_id": task["id"],
            "owner_email": OWNER,
            "spec_revision": 1,
            "generation": first.generation,
            "lease_token": str(first.token),
        }
    )
    try:
        with pytest.raises(BudgetError):
            await request(client)
    finally:
        current_procurement_binding.reset(token)
    assert not requests
    assert (await state.get_task(pool, task["id"], OWNER))["counters"]["model_calls"] == 0
