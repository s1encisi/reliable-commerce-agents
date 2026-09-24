"""共享工具测试：无身份守卫无需数据库，成功路径使用真实 PostgreSQL。

注入测试连接池，不调用真实模型。
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

import shared.db as shared_db
from shared.config import settings
from shared.context import current_user_email, current_user_role
from shared.tools.inventory_tools import check_stock
from shared.tools.loyalty_tools import (
    calculate_loyalty_discount,
    get_loyalty_benefits,
    get_loyalty_tier,
)
from shared.tools.memory_tools import recall_memories, store_memory
from shared.tools.pricing_tools import get_price_history
from shared.tools.seller_tools import get_my_products
from shared.tools.user_tools import get_purchase_history, get_user_profile

# ─────────────────────── Shared fixture ─────────────────────────────────────


@pytest_asyncio.fixture
async def db_pool(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> asyncpg.Pool:
    """把 clean_db 注入共享池供工具读取。"""
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    return clean_db


@pytest_asyncio.fixture
async def seeded_user(db_pool: asyncpg.Pool) -> dict:
    """插入青铜会员及三个等级，返回用户记录。"""
    async with db_pool.acquire() as conn:
        # 会员等级每例由 clean_db 清理。
        for name, min_spend, discount, free_ship, priority in [
            ("bronze", 0, 0, None, False),
            ("silver", 1000, 5, 75.00, False),
            ("gold", 3000, 10, 0.00, True),
        ]:
            await conn.execute(
                """INSERT INTO loyalty_tiers
                   (name, min_spend, discount_pct, free_shipping_threshold, priority_support)
                   VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING""",
                name,
                min_spend,
                discount,
                free_ship,
                priority,
            )

        row = await conn.fetchrow(
            """INSERT INTO users (email, password_hash, name, role, loyalty_tier, total_spend)
               VALUES ($1, 'hash', $2, 'customer', 'bronze', 0)
               RETURNING id, email, name, role, loyalty_tier, total_spend""",
            "test@example.com",
            "Test User",
        )
        return dict(row)


@pytest_asyncio.fixture
async def seeded_seller(db_pool: asyncpg.Pool) -> dict:
    """插入用于角色校验的商家。"""
    async with db_pool.acquire() as conn:
        # 会员等级数据。
        for name, min_spend, discount, free_ship, priority in [
            ("bronze", 0, 0, None, False),
        ]:
            await conn.execute(
                """INSERT INTO loyalty_tiers
                   (name, min_spend, discount_pct, free_shipping_threshold, priority_support)
                   VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING""",
                name,
                min_spend,
                discount,
                free_ship,
                priority,
            )
        row = await conn.fetchrow(
            """INSERT INTO users (email, password_hash, name, role, loyalty_tier, total_spend)
               VALUES ($1, 'hash', 'Seller Co', 'seller', 'bronze', 0)
               RETURNING id, email, role""",
            "seller@example.com",
        )
        return dict(row)


@pytest_asyncio.fixture
async def seeded_product(db_pool: asyncpg.Pool, seeded_seller: dict) -> dict:
    """插入属于测试商家的商品。"""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO products
               (name, description, category, brand, price, seller_id, is_active)
               VALUES ('Test Widget', 'A test product', 'Electronics', 'Acme', 49.99, $1, TRUE)
               RETURNING id, name, price""",
            seeded_seller["id"],
        )
        return dict(row)


# ─────────────────────── Guard-clause tests (no DB) ─────────────────────────


@pytest.mark.asyncio
async def test_get_user_profile_no_context_returns_error() -> None:
    current_user_email.set("")
    result = await get_user_profile()
    assert result.get("error") == "No user context available"


@pytest.mark.asyncio
async def test_get_purchase_history_no_context_returns_empty() -> None:
    current_user_email.set("")
    result = await get_purchase_history()
    assert result == []


@pytest.mark.asyncio
async def test_get_loyalty_tier_no_context_returns_error() -> None:
    current_user_email.set("")
    result = await get_loyalty_tier()
    assert result.get("error") == "No user context available"


@pytest.mark.asyncio
async def test_calculate_loyalty_discount_no_context_returns_error() -> None:
    current_user_email.set("")
    result = await calculate_loyalty_discount(cart_total=100.0)
    assert result.get("error") == "No user context available"


@pytest.mark.asyncio
async def test_store_memory_no_context_returns_error(db_pool: asyncpg.Pool) -> None:
    # 记忆工具在邮箱守卫前读取连接池，因此先设置池。
    current_user_email.set("")
    result = await store_memory(category="preference", content="test")
    assert result.get("error") == "No authenticated user"


@pytest.mark.asyncio
async def test_recall_memories_no_context_returns_error(db_pool: asyncpg.Pool) -> None:
    current_user_email.set("")
    result = await recall_memories()
    assert len(result) == 1 and result[0].get("error") == "No authenticated user"


# ─────────────────────── DB-backed happy-path tests ─────────────────────────


@pytest.mark.asyncio
async def test_get_user_profile_returns_user_data(seeded_user: dict) -> None:
    current_user_email.set(seeded_user["email"])
    result = await get_user_profile()
    assert result["email"] == seeded_user["email"]
    assert result["role"] == "customer"
    assert result["loyalty_tier"] == "bronze"
    assert "user_id" in result


@pytest.mark.asyncio
async def test_get_user_profile_unknown_user(db_pool: asyncpg.Pool) -> None:
    # 即使用户不存在，JOIN 仍需要会员等级表数据。
    current_user_email.set("nobody@example.com")
    result = await get_user_profile()
    assert "error" in result


@pytest.mark.asyncio
async def test_get_loyalty_tier_returns_tier_data(seeded_user: dict) -> None:
    current_user_email.set(seeded_user["email"])
    result = await get_loyalty_tier()
    assert result["tier"] == "bronze"
    assert "discount_pct" in result


@pytest.mark.asyncio
async def test_get_loyalty_benefits_returns_all_tiers(seeded_user: dict) -> None:
    result = await get_loyalty_benefits()
    tier_names = {r["tier"] for r in result}
    assert {"bronze", "silver", "gold"} == tier_names


@pytest.mark.asyncio
async def test_calculate_loyalty_discount_bronze_no_discount(seeded_user: dict) -> None:
    current_user_email.set(seeded_user["email"])
    result = await calculate_loyalty_discount(cart_total=100.0)
    assert result["tier"] == "bronze"
    assert result["discount_pct"] == 0
    assert result["discount_amount"] == 0.0
    assert result["discounted_total"] == 100.0


@pytest.mark.asyncio
async def test_check_stock_unknown_product_returns_empty(db_pool: asyncpg.Pool) -> None:
    import uuid as _uuid

    result = await check_stock(product_id=str(_uuid.uuid4()))
    assert result["in_stock"] is False
    assert result["total_quantity"] == 0


@pytest.mark.asyncio
async def test_check_stock_returns_warehouse_data(db_pool: asyncpg.Pool, seeded_product: dict) -> None:
    async with db_pool.acquire() as conn:
        wh = await conn.fetchrow(
            "INSERT INTO warehouses (name, location, region) VALUES ('East', 'VA', 'east') RETURNING id"
        )
        await conn.execute(
            """INSERT INTO warehouse_inventory
               (warehouse_id, product_id, quantity, reorder_threshold)
               VALUES ($1, $2, 50, 5)""",
            wh["id"],
            seeded_product["id"],
        )
    result = await check_stock(product_id=str(seeded_product["id"]))
    assert result["in_stock"] is True
    assert result["total_quantity"] == 50
    assert len(result["warehouses"]) == 1


@pytest.mark.asyncio
async def test_get_price_history_unknown_product(db_pool: asyncpg.Pool) -> None:
    import uuid as _uuid

    result = await get_price_history(product_id=str(_uuid.uuid4()))
    assert "error" in result


@pytest.mark.asyncio
async def test_store_and_recall_memory(seeded_user: dict) -> None:
    current_user_email.set(seeded_user["email"])
    store_result = await store_memory(
        category="preference",
        content="prefers wireless products",
        importance=7,
    )
    assert store_result["stored"] is True

    assert await recall_memories(category="preference") == []
    from uuid import UUID

    from orchestrator.routes.tasks import confirm_memory

    await confirm_memory(UUID(store_result["memory_id"]), user={"sub": seeded_user["email"]})
    memories = await recall_memories(category="preference")
    assert len(memories) >= 1
    assert any("wireless" in m["content"] for m in memories)


@pytest.mark.asyncio
async def test_recall_memories_importance_clamp(seeded_user: dict) -> None:
    current_user_email.set(seeded_user["email"])
    # 重要性超过 10 时应截断为 10。
    result = await store_memory(category="feedback", content="test", importance=99)
    assert result["stored"] is True
    from uuid import UUID

    from orchestrator.routes.tasks import confirm_memory

    await confirm_memory(UUID(result["memory_id"]), user={"sub": seeded_user["email"]})
    memories = await recall_memories()
    stored = next((m for m in memories if m["content"] == "test"), None)
    assert stored is not None
    assert stored["importance"] <= 10


# ─────────────────────── Role enforcement tests ──────────────────────────────


@pytest.mark.asyncio
async def test_get_my_products_denied_for_customer(seeded_user: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GUARDRAILS_ENABLED", True, raising=False)
    current_user_email.set(seeded_user["email"])
    current_user_role.set("customer")
    result = await get_my_products()
    assert isinstance(result, dict)
    assert result.get("error") == "permission_denied"


@pytest.mark.asyncio
async def test_get_my_products_allowed_for_seller(
    seeded_seller: dict, seeded_product: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "GUARDRAILS_ENABLED", True, raising=False)
    current_user_email.set(seeded_seller["email"])
    current_user_role.set("seller")
    result = await get_my_products()
    assert isinstance(result, list)
    assert any(p["name"] == "Test Widget" for p in result)


@pytest.mark.asyncio
async def test_get_my_products_allowed_for_admin(
    seeded_seller: dict, seeded_product: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "GUARDRAILS_ENABLED", True, raising=False)
    current_user_email.set(seeded_seller["email"])
    current_user_role.set("admin")
    result = await get_my_products()
    # 管理员通过角色校验；没有所属商品时返回空列表也是正常结果。
    assert isinstance(result, list)
