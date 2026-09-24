"""真实 PostgreSQL 上的幂等协议与敏感写入测试。

覆盖预留、成功重放、新请求冲突、过期接管、异常释放；退货与退款
顺序重试；审批记录写入失败时拒绝执行；退款参数和重复退货防护；
审批先认领后执行；结算重复提交不重复下单或扣库存。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from shared.context import current_user_email, current_user_role
from shared.idempotency import idempotent

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db_pool(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> asyncpg.Pool:
    import shared.db as shared_db

    # 配置测试可能重载模块，
    # 应替换退货服务实际持有的配置单例。
    from shared.after_sales.service import settings

    monkeypatch.setattr(shared_db, "_pool", clean_db)
    monkeypatch.setattr(settings, "HITL_ENABLED", False)
    current_user_email.set(None)
    current_user_role.set(None)
    return clean_db


async def _seed_user(pool: asyncpg.Pool, *, email: str = "buyer@example.com", role: str = "customer") -> uuid.UUID:
    user_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO users (id, email, password_hash, name, role) VALUES ($1, $2, 'hash', 'Test User', $3)",
        user_id,
        email,
        role,
    )
    return user_id


async def _seed_order(
    pool: asyncpg.Pool, user_id: uuid.UUID, *, status: str = "delivered", total: float = 99.99
) -> uuid.UUID:
    order_id = uuid.uuid4()
    await pool.execute(
        """INSERT INTO orders (id, user_id, status, total, shipping_address, created_at)
           VALUES ($1, $2, $3, $4, '{}'::jsonb, NOW() - INTERVAL '10 days')""",
        order_id,
        user_id,
        status,
        total,
    )
    if status == "delivered":
        await pool.execute(
            """INSERT INTO order_status_history (order_id, status, timestamp)
               VALUES ($1, 'delivered', NOW() - INTERVAL '5 days')""",
            order_id,
        )
    return order_id


async def _seed_return(
    pool: asyncpg.Pool,
    order_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    status: str = "requested",
    refund_amount: float = 99.99,
) -> uuid.UUID:
    return_id = uuid.uuid4()
    await pool.execute(
        """INSERT INTO returns (id, order_id, user_id, reason, status, refund_method, refund_amount)
           VALUES ($1, $2, $3, 'not as described', $4, 'original_payment', $5)""",
        return_id,
        order_id,
        user_id,
        status,
        refund_amount,
    )
    return return_id


async def _seed_product(pool: asyncpg.Pool, *, price: float = 25.0, is_active: bool = True) -> uuid.UUID:
    product_id = uuid.uuid4()
    await pool.execute(
        """INSERT INTO products (id, name, description, category, price, is_active)
           VALUES ($1, 'Test Widget', 'A widget', 'Electronics', $2, $3)""",
        product_id,
        price,
        is_active,
    )
    return product_id


# ─────────────────────── 1. Core protocol ────────────────────────────────


@idempotent("test_scope")
async def _echo(value: str) -> dict:
    _echo.calls = getattr(_echo, "calls", 0) + 1
    return {"echoed": value, "call_number": _echo.calls}


@idempotent("test_scope_raises")
async def _maybe_raise(should_raise: bool) -> dict:
    _maybe_raise.calls = getattr(_maybe_raise, "calls", 0) + 1
    if should_raise:
        raise ValueError("boom")
    return {"ok": True}


async def test_first_call_reserves_and_executes(db_pool: asyncpg.Pool) -> None:
    current_user_email.set("alice@example.com")
    _echo.calls = 0
    result = await _echo("hello")
    assert result == {"echoed": "hello", "call_number": 1}
    row = await db_pool.fetchrow("SELECT status, result FROM idempotency_keys WHERE scope = 'test_scope'")
    assert row["status"] == "completed"
    assert json.loads(row["result"]) == {"echoed": "hello", "call_number": 1}


async def test_identical_retry_replays_cached_result_without_reexecuting(db_pool: asyncpg.Pool) -> None:
    current_user_email.set("alice@example.com")
    _echo.calls = 0
    first = await _echo("hello")
    second = await _echo("hello")
    assert second == first
    assert _echo.calls == 1, "the second call must replay the cache, not re-invoke the wrapped function"


async def test_different_args_are_not_deduped(db_pool: asyncpg.Pool) -> None:
    current_user_email.set("alice@example.com")
    _echo.calls = 0
    await _echo("hello")
    result = await _echo("goodbye")
    assert result == {"echoed": "goodbye", "call_number": 2}
    assert _echo.calls == 2


async def test_different_identity_is_not_deduped(db_pool: asyncpg.Pool) -> None:
    _echo.calls = 0
    current_user_email.set("alice@example.com")
    await _echo("hello")
    current_user_email.set("bob@example.com")
    result = await _echo("hello")
    assert result == {"echoed": "hello", "call_number": 2}
    assert _echo.calls == 2, "same args from a different user must not be deduped against each other"


async def test_fresh_in_progress_conflict_is_refused(db_pool: asyncpg.Pool) -> None:
    current_user_email.set("alice@example.com")
    # 手动插入相同键的新 in_progress 记录，
    # 模拟仍在执行的并发重复请求。
    from shared.idempotency import _canonical_key

    key = _canonical_key("test_scope", "alice@example.com", {"value": "stuck"})
    await db_pool.execute(
        "INSERT INTO idempotency_keys (key, scope, status) VALUES ($1, 'test_scope', 'in_progress')",
        key,
    )
    result = await _echo("stuck")
    assert "already being processed" in result["error"]


async def test_stale_in_progress_is_reclaimed_not_permanently_blocked(db_pool: asyncpg.Pool) -> None:
    current_user_email.set("alice@example.com")
    from shared.idempotency import _canonical_key

    key = _canonical_key("test_scope", "alice@example.com", {"value": "abandoned"})
    stale_ts = datetime.now(UTC) - timedelta(seconds=120)
    await db_pool.execute(
        "INSERT INTO idempotency_keys (key, scope, status, created_at) VALUES ($1, 'test_scope', 'in_progress', $2)",
        key,
        stale_ts,
    )
    _echo.calls = 0
    result = await _echo("abandoned")
    assert result == {"echoed": "abandoned", "call_number": 1}, (
        "a reservation older than the staleness window must be treated as an abandoned "
        "attempt (crashed process) and taken over, not a permanent lock"
    )


async def test_exception_releases_the_reservation_for_a_real_retry(db_pool: asyncpg.Pool) -> None:
    current_user_email.set("alice@example.com")
    _maybe_raise.calls = 0
    with pytest.raises(ValueError, match="boom"):
        await _maybe_raise(should_raise=True)

    # 真实失败后的重试必须能够重新执行，
    # 不能被遗留预留永久阻止。
    result = await _maybe_raise(should_raise=False)
    assert result == {"ok": True}
    assert _maybe_raise.calls == 2


async def test_no_identity_skips_idempotency_entirely_without_touching_the_pool() -> None:
    # 有意不创建数据库池：无身份时，
    # 幂等装饰器不能调用 get_pool。
    # 否则未认证请求会在角色守卫之前，
    # 因连接池未初始化而崩溃，
    # 破坏无需数据库的拒绝路径。
    current_user_email.set(None)
    _echo.calls = 0
    result = await _echo("anon")
    assert result == {"echoed": "anon", "call_number": 1}


# ─────────────────────── 2. Real tool double-submit dedup ────────────────


async def test_initiate_return_retry_replays_the_original_return_instead_of_erroring(db_pool: asyncpg.Pool) -> None:
    from shared.tools.return_tools import initiate_return

    user_id = await _seed_user(db_pool, email="retry@example.com")
    order_id = await _seed_order(db_pool, user_id, status="delivered", total=50.0)
    current_user_email.set("retry@example.com")

    first = await initiate_return(order_id=str(order_id), reason="wrong size")
    assert "return_id" in first

    second = await initiate_return(order_id=str(order_id), reason="wrong size")
    assert second == first, "an identical retry must replay the original success, not hit 'already initiated'"

    count = await db_pool.fetchval("SELECT COUNT(*) FROM returns WHERE order_id = $1", order_id)
    assert count == 1, "only one returns row may exist no matter how many times the identical call retries"


async def test_process_refund_retry_replays_the_original_refund_instead_of_erroring(db_pool: asyncpg.Pool) -> None:
    from shared.tools.return_tools import process_refund

    user_id = await _seed_user(db_pool, email="refund-retry@example.com")
    order_id = await _seed_order(db_pool, user_id, status="returned", total=75.0)
    return_id = await _seed_return(db_pool, order_id, user_id, status="requested", refund_amount=75.0)
    current_user_email.set("refund-retry@example.com")
    current_user_role.set("customer")

    first = await process_refund(return_id=str(return_id))
    assert first["status"] == "refunded"

    second = await process_refund(return_id=str(return_id))
    assert second == first, "an identical retry must replay the original refund, not hit 'already refunded'"

    row = await db_pool.fetchrow("SELECT status FROM returns WHERE id = $1", return_id)
    assert row["status"] == "refunded"


# ─────────────────────── 3. hitl.py fail-closed ───────────────────────────


async def test_hitl_fails_closed_when_approval_record_write_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_framework._middleware import FunctionInvocationContext

    from shared.config import settings
    from shared.hitl import HITLFunctionMiddleware

    monkeypatch.setattr(settings, "HITL_ENABLED", True)

    async def _boom(**_kwargs: Any) -> uuid.UUID:
        raise RuntimeError("transient DB blip")

    monkeypatch.setattr("shared.hitl._create_hitl_request", _boom)

    called_next = False

    async def _call_next() -> None:
        nonlocal called_next
        called_next = True

    class _Fn:
        name = "process_refund"

    ctx = FunctionInvocationContext.__new__(FunctionInvocationContext)
    ctx.function = _Fn()
    ctx.arguments = {"return_id": "abc"}
    ctx.result = None

    middleware = HITLFunctionMiddleware()
    await middleware.process(ctx, _call_next)

    assert called_next is False, "the gated tool must NOT execute when the approval record couldn't be written"
    assert ctx.result["status"] == "error"
    assert "No changes have been made" in ctx.result["message"]


# ─────────────────────── 4. execute_approved_action bug fixes ────────────


async def test_execute_approved_action_process_refund_operates_on_the_real_return(db_pool: asyncpg.Pool) -> None:
    """退款参数名回归测试。

    中间件捕获的实际参数只有 return_id；不能读取空 order_id 后
    更新 orders，必须按退货标识更新正确记录。
    """
    from shared.hitl import execute_approved_action

    user_id = await _seed_user(db_pool, email="admin-approve@example.com")
    order_id = await _seed_order(db_pool, user_id, status="returned", total=42.0)
    return_id = await _seed_return(db_pool, order_id, user_id, status="requested", refund_amount=42.0)

    result = await execute_approved_action(
        tool_name="process_refund",
        tool_input={"return_id": str(return_id)},
        user_email="admin-approve@example.com",
    )

    assert result["success"] is True
    assert result["refunded_amount"] == 42.0
    row = await db_pool.fetchrow("SELECT status FROM returns WHERE id = $1", return_id)
    assert row["status"] == "refunded"


async def test_execute_approved_action_process_refund_is_guarded_against_double_execution(
    db_pool: asyncpg.Pool,
) -> None:
    from shared.hitl import execute_approved_action

    user_id = await _seed_user(db_pool, email="admin-double@example.com")
    order_id = await _seed_order(db_pool, user_id, status="returned", total=15.0)
    return_id = await _seed_return(db_pool, order_id, user_id, status="requested", refund_amount=15.0)

    first = await execute_approved_action(
        tool_name="process_refund",
        tool_input={"return_id": str(return_id)},
        user_email="admin-double@example.com",
    )
    assert first["success"] is True

    # 绕过幂等包装再次执行相同参数，
    # 模拟同一退货对应不同审批记录，
    # 仍不能报告第二次成功。
    # UPDATE 的状态条件提供
    # 这一层纵深保护。
    from shared.idempotency import _canonical_key

    key = _canonical_key(
        "hitl_execute",
        "admin-double@example.com",
        {
            "tool_name": "process_refund",
            "tool_input": {"return_id": str(return_id)},
            "user_email": "admin-double@example.com",
        },
    )
    await db_pool.execute("DELETE FROM idempotency_keys WHERE key = $1", key)

    second = await execute_approved_action(
        tool_name="process_refund",
        tool_input={"return_id": str(return_id)},
        user_email="admin-double@example.com",
    )
    assert second["success"] is False


async def test_execute_approved_action_initiate_return_does_not_create_a_duplicate_row(db_pool: asyncpg.Pool) -> None:
    from shared.hitl import _create_hitl_request, claim_hitl_request, execute_approved_action
    from shared.idempotency import _canonical_key

    email = "admin-initiate@example.com"
    user_id = await _seed_user(db_pool, email=email)
    order_id = await _seed_order(db_pool, user_id, status="delivered", total=30.0)
    request_id = str(
        await _create_hitl_request(
            email, None, "test", "initiate_return", {"order_id": str(order_id), "reason": "damaged"}
        )
    )
    req = await claim_hitl_request(request_id)
    args = {
        "tool_name": "initiate_return",
        "tool_input": req["tool_input"],
        "user_email": email,
        "approval_id": request_id,
    }
    first = await execute_approved_action(**args)
    assert first["success"] is True
    key = _canonical_key("hitl_execute:returns-v1", email, args)
    await db_pool.execute("DELETE FROM idempotency_keys WHERE key = $1", key)
    second = await execute_approved_action(**args)
    assert second == first, "the durable operation result survives removal of the legacy cache"
    assert await db_pool.fetchval("SELECT COUNT(*) FROM returns WHERE order_id = $1", order_id) == 1


# ─────────────────────── 5. claim_hitl_request TOCTOU fix ─────────────────


async def test_claim_hitl_request_is_atomic_only_one_caller_wins(db_pool: asyncpg.Pool) -> None:
    from shared.hitl import _create_hitl_request, claim_hitl_request

    request_id = await _create_hitl_request(
        user_email="race@example.com",
        session_id=None,
        agent_name="order-management",
        tool_name="process_refund",
        tool_input={"return_id": "whatever"},
    )

    first = await claim_hitl_request(str(request_id))
    second = await claim_hitl_request(str(request_id))

    assert first is not None
    assert first["tool_name"] == "process_refund"
    assert second is None, "a request that's already been claimed must not be claimable again"

    row = await db_pool.fetchrow("SELECT status FROM tool_approval_requests WHERE id = $1", request_id)
    assert row["status"] == "processing"


async def test_resolve_hitl_request_accepts_a_processing_row(db_pool: asyncpg.Pool) -> None:
    from shared.hitl import _create_hitl_request, claim_hitl_request, resolve_hitl_request

    request_id = await _create_hitl_request(
        user_email="resolve@example.com",
        session_id=None,
        agent_name="order-management",
        tool_name="cancel_order",
        tool_input={"order_id": "whatever"},
    )
    await claim_hitl_request(str(request_id))

    updated = await resolve_hitl_request(
        request_id=str(request_id),
        decision="approved",
        admin_email="admin@example.com",
        execution_result={"success": True},
    )
    assert updated is True
    row = await db_pool.fetchrow("SELECT status FROM tool_approval_requests WHERE id = $1", request_id)
    assert row["status"] == "executed"


# ─────────────────────── 6. Checkout double-submit dedup ─────────────────


async def _seed_cart_with_one_item(pool: asyncpg.Pool, user_id: uuid.UUID, product_id: uuid.UUID) -> None:
    cart_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO carts (id, user_id) VALUES ($1, $2)",
        cart_id,
        user_id,
    )
    await pool.execute(
        "INSERT INTO cart_items (cart_id, product_id, quantity) VALUES ($1, $2, $3)",
        cart_id,
        product_id,
        2,
    )
    warehouse_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO warehouses (id, name, location, region) VALUES ($1, 'Test WH', 'Testville', 'east')",
        warehouse_id,
    )
    await pool.execute(
        "INSERT INTO warehouse_inventory (warehouse_id, product_id, quantity) VALUES ($1, $2, 10)",
        warehouse_id,
        product_id,
    )


async def test_checkout_double_submit_places_one_order_and_decrements_inventory_once(
    db_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.routes import require_auth, router

    user_id = await _seed_user(db_pool, email="checkout@example.com")
    product_id = await _seed_product(db_pool, price=25.0)
    await _seed_cart_with_one_item(db_pool, user_id, product_id)

    async def _fake_auth():
        return {"sub": "checkout@example.com", "role": "customer", "user_id": str(user_id)}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = _fake_auth

    body = {
        "shipping_address": {
            "name": "A",
            "street": "1 Main St",
            "city": "X",
            "state": "Y",
            "zip": "00000",
            "country": "US",
        },
        "billing_same_as_shipping": True,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        first_resp = await http.post("/api/checkout", json=body, headers={"Authorization": "Bearer fake"})
        assert first_resp.status_code == 200
        first = first_resp.json()

        # 模拟超时后原样重发请求。
        # 首次结算已经清空购物车，
        # 但幂等键来自原请求身份和参数，
        # 不依赖当前购物车状态。
        # 应重放成功，不能因购物车为空报错，
        # 更不能再次下单。
        second_resp = await http.post("/api/checkout", json=body, headers={"Authorization": "Bearer fake"})
        assert second_resp.status_code == 200
        second = second_resp.json()

    assert second == first, "a retried checkout with identical args must replay the original order"

    order_count = await db_pool.fetchval("SELECT COUNT(*) FROM orders WHERE user_id = $1", user_id)
    assert order_count == 1, "exactly one order must exist no matter how many times the identical request retries"

    remaining_stock = await db_pool.fetchval(
        "SELECT quantity FROM warehouse_inventory WHERE product_id = $1", product_id
    )
    assert remaining_stock == 8, "inventory must be decremented exactly once (10 - 2), not twice"


async def test_checkout_with_different_body_is_not_deduped(db_pool: asyncpg.Pool) -> None:
    from orchestrator.routes import require_auth, router

    user_id = await _seed_user(db_pool, email="checkout-distinct@example.com")
    product_id = await _seed_product(db_pool, price=10.0)
    await _seed_cart_with_one_item(db_pool, user_id, product_id)

    async def _fake_auth():
        return {"sub": "checkout-distinct@example.com", "role": "customer", "user_id": str(user_id)}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = _fake_auth

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        first_resp = await http.post(
            "/api/checkout",
            json={
                "shipping_address": {
                    "name": "A",
                    "street": "1 Main St",
                    "city": "X",
                    "state": "Y",
                    "zip": "00000",
                    "country": "US",
                },
                "billing_same_as_shipping": True,
            },
            headers={"Authorization": "Bearer fake"},
        )
        assert first_resp.status_code == 200

        # 不同订单参数对应新的业务尝试。
        # 即使购物车为空而返回 400，
        # 也不能错误重放首次成功。
        second_resp = await http.post(
            "/api/checkout",
            json={
                "shipping_address": {
                    "name": "B",
                    "street": "2 Other St",
                    "city": "X",
                    "state": "Y",
                    "zip": "00000",
                    "country": "US",
                },
                "billing_same_as_shipping": True,
            },
            headers={"Authorization": "Bearer fake"},
        )
        assert second_resp.status_code == 400
        assert "empty" in second_resp.json()["detail"].lower()
