"""ecommerce-mcp-product 服务的测试。

分两个层次：
- 注册冒烟测试（不涉及数据库）—— 校验工具名称已注册，且 ASGI 应用可导入。
  这些测试在 CI 中总会运行。
- 集成测试（通过 testcontainers 连接数据库）—— 在带生产 schema 的真实
  Postgres 容器上校验实际的 SQL 查询。标记为 `integration`。
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from ecommerce_mcp_product.server import _get_pool, app, mcp

# ─────────────────────── 注册冒烟测试 ─────────────────────────────────


def test_mcp_server_name() -> None:
    assert mcp.name == "product-discovery-mcp"


def test_tool_names_registered() -> None:
    """全部 5 个商品工具都必须能在无数据库连接的情况下被发现。"""
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    expected = {
        "search_products",
        "get_product_details",
        "compare_products",
        "get_trending_products",
        "get_price_history",
    }
    assert expected == tool_names


def test_asgi_app_importable() -> None:
    """app 必须是可调用的 ASGI 应用（uvicorn 入口点检查）。"""
    assert callable(app)


def test_get_pool_raises_before_startup() -> None:
    """若在 lifespan 启动前调用，_get_pool() 必须显式报错。"""
    with pytest.raises(RuntimeError, match="DB pool not initialized"):
        _get_pool()


# ─────────────────────── 集成测试（真实数据库） ──────────────────────────────


@pytest.fixture
async def product_id(postgres_pool: asyncpg.Pool) -> str:
    """插入一条最小化的商品记录并返回其 id。"""
    async with postgres_pool.acquire() as conn:
        # 先插入商家（users 表）
        seller_id = await conn.fetchval(
            """INSERT INTO users (email, name, role, password_hash)
               VALUES ('seller@test.com', 'Test Seller', 'seller', 'hash')
               ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name
               RETURNING id"""
        )
        pid = await conn.fetchval(
            """INSERT INTO products
                   (name, category, brand, price, original_price, rating,
                    review_count, description, seller_id, is_active)
               VALUES ('Widget Pro', 'Electronics', 'Acme', 99.99, 129.99, 4.5,
                       10, 'A fine widget', $1, TRUE)
               ON CONFLICT DO NOTHING
               RETURNING id""",
            seller_id,
        )
        if pid is None:
            pid = await conn.fetchval("SELECT id FROM products WHERE name = 'Widget Pro' LIMIT 1")
        return str(pid)


@pytest_asyncio.fixture
async def _patched_pool(postgres_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch):
    """对模块级 _pool 打补丁，使工具函数使用测试容器。"""
    import ecommerce_mcp_product.server as srv

    monkeypatch.setattr(srv, "_pool", postgres_pool)
    yield


@pytest.mark.integration
async def test_search_products_returns_results(
    product_id: str,
    _patched_pool: None,
) -> None:
    from ecommerce_mcp_product.server import search_products

    results = await search_products(query="Widget")
    assert isinstance(results, list)
    assert any(r["id"] == product_id for r in results)


@pytest.mark.integration
async def test_get_product_details_found(
    product_id: str,
    _patched_pool: None,
) -> None:
    from ecommerce_mcp_product.server import get_product_details

    result = await get_product_details(product_id=product_id)
    assert "error" not in result
    assert result["id"] == product_id
    assert result["name"] == "Widget Pro"
    assert isinstance(result["in_stock"], bool)


@pytest.mark.integration
async def test_get_product_details_not_found(
    _patched_pool: None,
) -> None:
    from ecommerce_mcp_product.server import get_product_details

    result = await get_product_details(product_id="00000000-0000-0000-0000-000000000000")
    assert "error" in result


@pytest.mark.integration
async def test_search_products_no_results(
    _patched_pool: None,
) -> None:
    from ecommerce_mcp_product.server import search_products

    results = await search_products(query="zzz_no_such_product_xyz")
    assert isinstance(results, list)
    assert results == []


@pytest.mark.integration
async def test_compare_products_invalid_count(
    _patched_pool: None,
) -> None:
    from ecommerce_mcp_product.server import compare_products

    result = await compare_products(product_ids=["only-one"])
    assert result[0].get("error") is not None


@pytest.mark.integration
async def test_get_price_history_no_history(
    product_id: str,
    _patched_pool: None,
) -> None:
    from ecommerce_mcp_product.server import get_price_history

    result = await get_price_history(product_id=product_id, days=30)
    assert "error" not in result
    assert result["product_id"] == product_id
    # 没有种入 price_history 记录 → 存在 summary 字段
    assert "current_price" in result


# ─────────────────────── 全文检索一致性对齐 ────────────────────────────
#
# MCP 路径必须以相同方式回答与原生 product_discovery 工具相同的查询。
# 本服务过去是把整个查询当作一个 `%phrase%` 做 LIKE，因此任何多词查询
# 都需要精确子串匹配 —— 远比原生工具的逐词匹配严格，是 MCP_ENABLED=true
# 与 false 之间真实存在的行为差异。


@pytest_asyncio.fixture
async def fts_catalog(postgres_pool: asyncpg.Pool) -> dict[str, str]:
    """两个商品的用词在形态上与测试查询不同。"""
    async with postgres_pool.acquire() as conn:
        anc = await conn.fetchval(
            """INSERT INTO products (name, description, category, brand, price, rating, is_active)
               VALUES ('Wireless Headphones with ANC',
                       'Over-ear wireless headphones with active noise cancelling.',
                       'Electronics', 'Sony', 279.99, 4.5, TRUE)
               RETURNING id"""
        )
        kettle = await conn.fetchval(
            """INSERT INTO products (name, description, category, brand, price, rating, is_active)
               VALUES ('Electric Kettle', 'Stainless steel kettle with rapid boil.',
                       'Home', 'Breville', 59.99, 4.8, TRUE)
               RETURNING id"""
        )
    return {"anc": str(anc), "kettle": str(kettle)}


@pytest.mark.integration
async def test_search_matches_stemmed_terms(
    fts_catalog: dict[str, str],
    _patched_pool: None,
) -> None:
    """ "noise cancellation" 必须能找到 "noise cancelling" —— 不依赖字面子串。"""
    from ecommerce_mcp_product.server import search_products

    results = await search_products(query="noise cancellation headphones")

    assert fts_catalog["anc"] in [r["id"] for r in results]


@pytest.mark.integration
async def test_search_does_not_require_every_term(
    fts_catalog: dict[str, str],
    _patched_pool: None,
) -> None:
    """没有商品提到 bluetooth；这不能让结果集变空。"""
    from ecommerce_mcp_product.server import search_products

    results = await search_products(query="wireless bluetooth headphones")

    assert fts_catalog["anc"] in [r["id"] for r in results]


@pytest.mark.integration
async def test_search_stopword_only_query_falls_back_to_filters(
    fts_catalog: dict[str, str],
    _patched_pool: None,
) -> None:
    """`plainto_tsquery('the ???')` 是一个匹配不到任何行的空 tsquery ——
    这不能把一次带过滤条件的浏览变成零结果。

    本包的 `postgres_pool` 是会话级的，且没有逐测试的 truncate，
    因此断言的是成员关系与过滤条件，而不是精确的行集合。
    """
    from ecommerce_mcp_product.server import search_products

    results = await search_products(query="the ???", category="Home")

    assert fts_catalog["kettle"] in [r["id"] for r in results]
    assert all(r["category"] == "Home" for r in results)
