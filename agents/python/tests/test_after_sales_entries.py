"""通过真实入口和隔离 PostgreSQL 验证退货政策回归。"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from shared.config import settings
from shared.context import current_user_email, current_user_role

NOW = datetime(2026, 9, 15, 12, tzinfo=UTC)
EMAIL = "after-sales@example.test"


@pytest_asyncio.fixture
async def returns_db(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> asyncpg.Pool:
    monkeypatch.setattr("shared.db._pool", clean_db)
    monkeypatch.setattr(settings, "HITL_ENABLED", False)
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    monkeypatch.setattr("shared.after_sales.service.utc_now", lambda: NOW)
    monkeypatch.setattr("workflows.return_replace.utc_now", lambda: NOW)
    email_token = current_user_email.set(EMAIL)
    role_token = current_user_role.set("customer")
    await clean_db.execute("INSERT INTO users (email, password_hash, name) VALUES ($1, 'test', 'After Sales')", EMAIL)
    try:
        yield clean_db
    finally:
        current_user_email.reset(email_token)
        current_user_role.reset(role_token)


async def seed_order(
    pool: asyncpg.Pool,
    *,
    delivered_at: datetime | None = None,
    status: str = "delivered",
    total: Decimal = Decimal("49.99"),
) -> UUID:
    order_id = uuid4()
    await pool.execute(
        """INSERT INTO orders (id, user_id, status, total, shipping_address, created_at)
           VALUES ($1, (SELECT id FROM users WHERE email = $2), $3, $4, '{}', $5)""",
        order_id,
        EMAIL,
        status,
        total,
        NOW - timedelta(days=90),
    )
    if delivered_at is not None:
        await pool.execute(
            "INSERT INTO order_status_history (order_id, status, timestamp) VALUES ($1, 'delivered', $2)",
            order_id,
            delivered_at,
        )
    return order_id


async def assert_unmodified(pool: asyncpg.Pool, order_id: UUID) -> None:
    assert await pool.fetchval("SELECT status FROM orders WHERE id = $1", order_id) == "delivered"
    assert await pool.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order_id) == 0


@pytest.mark.asyncio
async def test_missing_delivery_is_not_eligible(returns_db: asyncpg.Pool) -> None:
    from shared.tools.return_tools import check_return_eligibility

    order_id = await seed_order(returns_db)
    result = await check_return_eligibility.func(order_id=str(order_id))
    assert result["eligible"] is False
    assert result["outcome"] == "NEEDS_REVIEW"
    await assert_unmodified(returns_db, order_id)


async def http_post(path: str, body: dict[str, Any], *, email: str = EMAIL, role: str = "customer") -> Response:
    from orchestrator.routes import router
    from shared.db import get_pool
    from shared.jwt_utils import create_access_token

    app = FastAPI()
    app.include_router(router)
    user_id = await get_pool().fetchval("SELECT id FROM users WHERE email = $1", email)
    token = create_access_token(email, role, str(user_id or uuid4()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(path, json=body, headers={"Authorization": f"Bearer {token}"})


async def create_approval(order_id: UUID, *, reason: str = "Wrong size", method: str = "store_credit") -> str:
    from shared.hitl import _create_hitl_request

    return str(
        await _create_hitl_request(
            EMAIL,
            None,
            "after-sales-test",
            "initiate_return",
            {"order_id": str(order_id), "reason": reason, "refund_method": method},
        )
    )


async def approve(request_id: str) -> dict[str, Any]:
    response = await http_post(
        f"/api/admin/hitl/requests/{request_id}/approve", {}, email="admin@example.test", role="admin"
    )
    assert response.status_code == 200, response.text
    return response.json()["execution_result"]


@pytest.mark.parametrize("entry", ["tool", "rest", "approval", "workflow"])
@pytest.mark.asyncio
async def test_legal_return_has_identical_business_fields(returns_db: asyncpg.Pool, entry: str) -> None:
    from shared.tools.return_tools import check_return_eligibility, initiate_return
    from workflows.return_replace import ReturnAndReplaceWorkflow, WorkflowState

    order_id = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    reason = "Wrong size"
    if entry == "tool":
        result = await initiate_return.func(order_id=str(order_id), reason=reason, refund_method="store_credit")
        assert result["success"] is True
    elif entry == "rest":
        response = await http_post(
            f"/api/orders/{order_id}/return", {"reason": reason, "refund_method": "store_credit"}
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["success"] is True
    elif entry == "approval":
        result = await approve(await create_approval(order_id))
        assert result["success"] is True
    else:
        workflow = ReturnAndReplaceWorkflow(
            {"check_return_eligibility": check_return_eligibility.func, "initiate_return": initiate_return.func}
        )
        state = await workflow.execute(WorkflowState(EMAIL, str(order_id), reason=reason))
        assert state.return_id and not state.errors
        result = {"return_id": state.return_id}
    row = await returns_db.fetchrow("SELECT * FROM returns WHERE order_id = $1", order_id)
    assert str(row["id"]) == result["return_id"]
    assert row["status"] == "requested"
    assert row["reason"] == reason
    assert row["refund_method"] == "store_credit"
    assert row["refund_amount"] == Decimal("49.99")
    assert row["return_label_url"].startswith("/api/returns/")
    assert await returns_db.fetchval("SELECT status FROM orders WHERE id = $1", order_id) == "returned"
    assert (
        await returns_db.fetchval(
            "SELECT count(*) FROM order_status_history WHERE order_id = $1 AND status = 'returned'",
            order_id,
        )
        == 1
    )


@pytest.mark.parametrize("days,outcome", [(30, "SUCCEEDED"), (31, "REJECTED")])
@pytest.mark.asyncio
async def test_submit_enforces_exact_boundary(returns_db: asyncpg.Pool, days: int, outcome: str) -> None:
    from shared.tools.return_tools import initiate_return

    order_id = await seed_order(returns_db, delivered_at=NOW - timedelta(days=days))
    result = await initiate_return.func(order_id=str(order_id), reason="Wrong size")
    assert result["outcome"] == outcome
    if outcome == "REJECTED":
        await assert_unmodified(returns_db, order_id)


@pytest.mark.parametrize("reason,method", [(" ", "store_credit"), ("x" * 256, "store_credit"), ("broken", "bitcoin")])
@pytest.mark.asyncio
async def test_invalid_parameters_fail_before_business_writes(
    returns_db: asyncpg.Pool,
    reason: str,
    method: str,
) -> None:
    from shared.tools.return_tools import initiate_return

    order_id = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    result = await initiate_return.func(order_id=str(order_id), reason=reason, refund_method=method)
    assert result["error_code"] == "VALIDATION_ERROR"
    response = await http_post(f"/api/orders/{order_id}/return", {"reason": reason, "refund_method": method})
    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    assert isinstance(response.json()["detail"], str)
    await assert_unmodified(returns_db, order_id)


@pytest.mark.asyncio
async def test_foreign_and_unknown_order_responses_are_indistinguishable(returns_db: asyncpg.Pool) -> None:
    order_id = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    foreign = await http_post(f"/api/orders/{order_id}/return", {"reason": "wrong"}, email="other@example.test")
    missing = await http_post(f"/api/orders/{uuid4()}/return", {"reason": "wrong"}, email="other@example.test")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    await assert_unmodified(returns_db, order_id)


@pytest.mark.asyncio
async def test_missing_evidence_can_be_corrected_without_cached_failure(returns_db: asyncpg.Pool) -> None:
    from shared.tools.return_tools import initiate_return

    order_id = await seed_order(returns_db)
    first = await initiate_return.func(order_id=str(order_id), reason="wrong")
    assert first["outcome"] == "NEEDS_REVIEW"
    await returns_db.execute(
        "INSERT INTO order_status_history (order_id, status, timestamp) VALUES ($1, 'delivered', $2)",
        order_id,
        NOW - timedelta(days=5),
    )
    second = await initiate_return.func(order_id=str(order_id), reason="wrong")
    assert second["success"] is True
    assert await initiate_return.func(order_id=str(order_id), reason="wrong") == second


@pytest.mark.parametrize("hitl,total", [(True, Decimal("49.99")), (False, Decimal("501.00"))])
@pytest.mark.asyncio
async def test_tool_and_rest_both_queue_when_approval_required(
    returns_db: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
    hitl: bool,
    total: Decimal,
) -> None:
    from shared.tools.return_tools import initiate_return

    monkeypatch.setattr(settings, "HITL_ENABLED", hitl)
    order_id = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5), total=total)
    tool_result = await initiate_return.func(order_id=str(order_id), reason="wrong")
    response = await http_post(f"/api/orders/{order_id}/return", {"reason": "wrong"})
    assert response.status_code == 409  # 界面不能在此时宣称退货已创建。
    assert "approval" in response.json()["detail"]
    assert tool_result["outcome"] == response.json()["outcome"] == "AWAITING_APPROVAL"
    await assert_unmodified(returns_db, order_id)
    result = await approve(tool_result["request_id"])
    assert result["success"] is True


@pytest.mark.parametrize("mutation", ["expires", "missing_delivery", "amount", "reason", "version", "ttl"])
@pytest.mark.asyncio
async def test_approval_rechecks_changed_evidence_and_binding(
    returns_db: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    order_id = await seed_order(returns_db, delivered_at=NOW - timedelta(days=29))
    request_id = await create_approval(order_id)
    if mutation == "expires":
        monkeypatch.setattr("shared.after_sales.service.utc_now", lambda: NOW + timedelta(days=2))
    elif mutation == "missing_delivery":
        await returns_db.execute("DELETE FROM order_status_history WHERE order_id = $1", order_id)
    elif mutation == "amount":
        await returns_db.execute("UPDATE orders SET total = total + 1 WHERE id = $1", order_id)
    else:
        raw = await returns_db.fetchval("SELECT tool_input FROM tool_approval_requests WHERE id = $1", request_id)
        payload = json.loads(raw)
        if mutation == "reason":
            payload["reason"] = "Changed after approval preparation"
        elif mutation == "version":
            payload["_return_approval"]["policy_version"] = "old-policy"
        else:
            payload["_return_approval"]["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
        await returns_db.execute(
            "UPDATE tool_approval_requests SET tool_input = $2::jsonb WHERE id = $1",
            request_id,
            json.dumps(payload),
        )
    result = await approve(request_id)
    assert result["success"] is False
    # 审批是授权决定，不是业务写入成功的证据。
    assert (
        await returns_db.fetchval("SELECT status FROM tool_approval_requests WHERE id = $1", request_id) == "approved"
    )
    await assert_unmodified(returns_db, order_id)


@pytest.mark.asyncio
async def test_pending_or_denied_approval_cannot_execute(returns_db: asyncpg.Pool) -> None:
    from shared.hitl import execute_approved_action, get_hitl_request

    order_id = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    request_id = await create_approval(order_id)
    req = await get_hitl_request(request_id)
    result = await execute_approved_action("initiate_return", req["tool_input"], EMAIL, request_id)
    assert result["success"] is False
    response = await http_post(
        f"/api/admin/hitl/requests/{request_id}/deny", {}, email="admin@example.test", role="admin"
    )
    assert response.status_code == 200
    result = await execute_approved_action("initiate_return", req["tool_input"], EMAIL, request_id)
    assert result["success"] is False
    await assert_unmodified(returns_db, order_id)


@pytest.mark.asyncio
async def test_concurrent_different_intents_create_only_one_return(returns_db: asyncpg.Pool) -> None:
    from shared.tools.return_tools import initiate_return

    order_id = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    results = await asyncio.gather(
        *(initiate_return.func(order_id=str(order_id), reason=f"Reason {n}") for n in range(5))
    )
    assert sum(result.get("success") is True for result in results) == 1
    assert await returns_db.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order_id) == 1


@pytest.mark.asyncio
async def test_failure_mid_transaction_rolls_back_all_business_writes(returns_db: asyncpg.Pool) -> None:
    from shared.tools.return_tools import initiate_return

    order_id = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    await returns_db.execute("""
        CREATE FUNCTION reject_test_return_event() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.status = 'returned' THEN RAISE EXCEPTION 'injected return event failure'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER reject_test_return_event BEFORE INSERT ON order_status_history
        FOR EACH ROW EXECUTE FUNCTION reject_test_return_event();
    """)
    try:
        with pytest.raises(asyncpg.RaiseError, match="injected return event failure"):
            await initiate_return.func(order_id=str(order_id), reason="wrong")
        await assert_unmodified(returns_db, order_id)
    finally:
        await returns_db.execute(
            "DROP TRIGGER reject_test_return_event ON order_status_history; DROP FUNCTION reject_test_return_event()"
        )


@pytest.mark.parametrize("approved,expire", [(True, False), (False, False), (True, True)])
@pytest.mark.asyncio
async def test_real_workflow_pauses_before_writes_and_revalidates_on_resume(
    returns_db: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
    approved: bool,
    expire: bool,
) -> None:
    from orchestrator.modes.base import RunContext
    from orchestrator.modes.workflow_mode import ReturnReplaceMode

    monkeypatch.setattr(settings, "HITL_ENABLED", True)
    order_id = await seed_order(returns_db, delivered_at=NOW - timedelta(days=29))
    message = f"Return order {order_id}: the item is damaged"
    events = [e async for e in ReturnReplaceMode().run(message, RunContext(history=[]))]
    pause = events[-1].payload
    assert pause["pending_approval"] is True
    assert "initiate_return" not in pause["agents_involved"]
    await assert_unmodified(returns_db, order_id)
    if expire:
        monkeypatch.setattr("shared.after_sales.service.utc_now", lambda: NOW + timedelta(days=2))
    # 从数据库检查点重建工作流，模拟新的 HTTP 请求。
    events = [
        e
        async for e in ReturnReplaceMode().resume(
            checkpoint_id=pause["latest_checkpoint_id"],
            request_id=pause["request_id"],
            approved=approved,
        )
    ]
    final = events[-1].payload
    if approved and not expire:
        assert "finalize" in final["agents_involved"]
        row = await returns_db.fetchrow("SELECT reason, refund_method FROM returns WHERE order_id = $1", order_id)
        assert row["reason"] == message  # 审批确认的原因在进程重建后仍保持一致。
        assert row["refund_method"] == "store_credit"
    else:
        assert "finalize" not in final["agents_involved"]
        await assert_unmodified(returns_db, order_id)


@pytest.mark.asyncio
async def test_real_http_workflow_concurrent_resume_claims_only_once(
    returns_db: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "HITL_ENABLED", True)
    order_id = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    chat = await http_post(
        "/api/chat", {"message": f"Return order {order_id} because it is damaged", "mode": "workflow:return-replace"}
    )
    assert chat.status_code == 200, chat.text
    run_id = await returns_db.fetchval("SELECT workflow_run_id FROM hitl_requests WHERE user_email = $1", EMAIL)
    await assert_unmodified(returns_db, order_id)
    responses = await asyncio.gather(
        *(http_post(f"/api/orchestration/{run_id}/resume", {"approved": True}) for _ in range(2))
    )
    assert sorted(response.status_code for response in responses) == [200, 404]
    assert await returns_db.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order_id) == 1


@pytest.mark.asyncio
async def test_ambiguous_order_needs_input_without_choosing_recent(returns_db: asyncpg.Pool) -> None:
    from orchestrator.modes.base import RunContext
    from orchestrator.modes.workflow_mode import ReturnReplaceMode

    first = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    second = await seed_order(returns_db, delivered_at=NOW - timedelta(days=3))
    events = [e async for e in ReturnReplaceMode().run("Return my order", RunContext(history=[]))]
    assert "provide the order ID" in events[-1].payload["text"]
    assert events[-1].payload["agents_involved"] == []
    events = [e async for e in ReturnReplaceMode().run(f"Return {first} or {second}", RunContext(history=[]))]
    assert "choose one order ID" in events[-1].payload["text"]
    await assert_unmodified(returns_db, first)
    await assert_unmodified(returns_db, second)


@pytest.mark.asyncio
async def test_waiting_for_order_lock_does_not_freeze_eligibility_clock(
    returns_db: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shared.after_sales import operations, service
    from shared.tool_inputs import InitiateReturnInput

    order_id = await seed_order(returns_db, delivered_at=NOW - timedelta(days=30))
    intent = InitiateReturnInput(order_id=order_id, reason="wrong")
    # 先预留操作再锁父记录，因为新操作的外键需要键共享锁。
    await operations.reserve(intent, operations.operation_id_for(intent))
    entering = asyncio.Event()
    original = service._load_snapshot

    async def observe_snapshot(conn: asyncpg.Connection, uid: UUID, email: str, *, lock: bool) -> Any:
        if lock:
            entering.set()
        return await original(conn, uid, email, lock=lock)

    monkeypatch.setattr(service, "_load_snapshot", observe_snapshot)
    async with returns_db.acquire() as conn:
        async with conn.transaction():
            await conn.fetchval("SELECT id FROM orders WHERE id = $1 FOR UPDATE", order_id)
            task = asyncio.create_task(service.request_return(str(order_id), "wrong"))
            await asyncio.wait_for(entering.wait(), timeout=5)
            monkeypatch.setattr(service, "utc_now", lambda: NOW + timedelta(seconds=1))
    result = await asyncio.wait_for(task, timeout=5)
    assert result["error_code"] == "RETURN_WINDOW_EXPIRED"
    await assert_unmodified(returns_db, order_id)


@pytest.mark.asyncio
async def test_direct_tool_cannot_skip_expiry_check(returns_db: asyncpg.Pool) -> None:
    from shared.tools.return_tools import initiate_return

    order_id = await seed_order(returns_db, delivered_at=NOW - timedelta(days=60))
    result = await initiate_return.func(order_id=str(order_id), reason="Wrong size")
    assert result.get("success") is False
    await assert_unmodified(returns_db, order_id)


@pytest.mark.asyncio
async def test_approved_path_cannot_bypass_missing_delivery(returns_db: asyncpg.Pool) -> None:
    from shared.hitl import execute_approved_action

    order_id = await seed_order(returns_db)
    result = await execute_approved_action(
        tool_name="initiate_return",
        tool_input={"order_id": str(order_id), "reason": "Wrong size"},
        user_email=EMAIL,
    )
    assert result["success"] is False
    await assert_unmodified(returns_db, order_id)
