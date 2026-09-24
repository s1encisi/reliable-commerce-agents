"""MCP 服务 —— 商品发现领域。

通过 MCP streamable HTTP 传输暴露商品搜索（关键词 + 过滤）、商品详情、
对比、热门商品以及价格历史。

独立运行（供 MCP Inspector 使用的 stdio）：
    uv run python -m ecommerce_mcp_product.server

作为 HTTP 服务运行：
    uvicorn ecommerce_mcp_product.server:app --host 0.0.0.0 --port 9000

通过已安装的 console script 运行：
    ecommerce-mcp-product
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated

import asyncpg
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents",
)
# 嵌入模型维度 —— 必须与 product_embeddings 中存储的向量一致。
EMBEDDING_DIM = 1536

# OAuth 2.1 资源服务器模式（可选 —— 默认关闭，快速上手流程不变）。
MCP_AUTH_ENABLED = os.environ.get("MCP_AUTH_ENABLED", "false").lower() == "true"

_pool: asyncpg.Pool | None = None


def _or_joined_tsquery(param: str) -> str:
    """将文本参数转换为 OR 连接的 tsquery 的 SQL 表达式。

    ``plainto_tsquery`` 会用 AND 连接其词元，因此 "noise cancelling
    headphones" 只会匹配同时包含这三个词的商品。把运算符改写为 ``|``
    后任一词命中即可，并交由 ``ts_rank`` 把完全匹配排在部分匹配之前。

    内置自 ``shared/search.py`` —— 本包是独立的 uv 工作区成员，
    必须能在不依赖 shared 库的情况下安装。请保持两者同步。
    """
    return f"replace(plainto_tsquery('english', {param})::text, '&', '|')::tsquery"


@asynccontextmanager
async def _lifespan(server: FastMCP):
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=8)
    logger.info("product-mcp: DB pool ready")
    try:
        yield
    finally:
        if _pool:
            await _pool.close()


_mcp_kwargs: dict = {
    "instructions": (
        "Product catalog data for the E-Commerce Agents platform. "
        "Search products by keyword or semantic similarity, retrieve full product "
        "details, compare products side by side, and check price history."
    ),
    "lifespan": _lifespan,
    # 只要 `host` 保持默认的 "127.0.0.1"，FastMCP 就会自动启用
    # DNS 重绑定 Host 头保护，且仅把 localhost/127.0.0.1/::1 加入白名单 ——
    # 这会让经由 Docker 网络发起的每一次真实调用都被静默地返回 421
    # （例如某个专业智能体调用 http://mcp-product:9000）。
    # 本应用实际上是经 `uvicorn ... --host 0.0.0.0` 提供服务的
    # （参见 main()/Dockerfile 的 CMD），所以这里也显式声明该值 ——
    # `host="127.0.0.1"` 从来就不符合该进程真实的运行方式。
    "host": "0.0.0.0",
}

if MCP_AUTH_ENABLED:
    from mcp.server.auth.settings import AuthSettings

    from ecommerce_mcp_product.auth import (
        AUTH_SERVER_ISSUER,
        MCP_PRODUCT_REQUIRED_SCOPE,
        JwksTokenVerifier,
    )

    _resource_url = os.environ.get("MCP_PRODUCT_RESOURCE_URL", "http://localhost:9000/mcp")
    _mcp_kwargs["token_verifier"] = JwksTokenVerifier()
    _mcp_kwargs["auth"] = AuthSettings(
        issuer_url=AUTH_SERVER_ISSUER,
        resource_server_url=_resource_url,
        required_scopes=[MCP_PRODUCT_REQUIRED_SCOPE],
    )
    logger.info("product-mcp: OAuth 2.1 resource-server mode enabled issuer=%s", AUTH_SERVER_ISSUER)

mcp = FastMCP("product-discovery-mcp", **_mcp_kwargs)


def _get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — server not started yet")
    return _pool


# ─────────────────────── 工具 ──────────────────────────────────────────────


@mcp.tool()
async def search_products(
    query: Annotated[str | None, "Natural language search query"] = None,
    category: Annotated[str | None, "Category filter: Electronics, Clothing, Home, Sports, Books"] = None,
    min_price: Annotated[float | None, "Minimum price"] = None,
    max_price: Annotated[float | None, "Maximum price"] = None,
    min_rating: Annotated[float | None, "Minimum rating (1–5)"] = None,
    sort_by: Annotated[str | None, "Sort: price_asc, price_desc, rating, newest"] = None,
    limit: Annotated[int, "Max results (capped at 50)"] = 10,
) -> list[dict]:
    """使用关键词 + 可选过滤条件搜索商品目录。"""
    safe_limit = min(limit, 50)
    conditions = ["p.is_active = TRUE"]
    args: list = []
    idx = 1

    tsquery: str | None = None
    if query and query.strip():
        # 基于加权的 products.search_vector 列做 Postgres 全文检索
        # （name=A、brand=B、description=C）。与原生 product_discovery 工具
        # 保持一致，使 MCP_ENABLED 不改变结果 —— 这里过去是把整个查询当作
        # 一个 %phrase% 做 LIKE，要求精确子串匹配，与原生路径严重偏离。
        tsquery = _or_joined_tsquery(f"${idx}")
        # 仅含停用词或标点的查询会归约为空 tsquery，匹配不到任何内容；
        # 此时回退为仅使用过滤条件。
        conditions.append(f"({tsquery} = ''::tsquery OR p.search_vector @@ {tsquery})")
        args.append(query)
        idx += 1
    if category:
        conditions.append(f"p.category = ${idx}")
        args.append(category)
        idx += 1
    if min_price is not None:
        conditions.append(f"p.price >= ${idx}")
        args.append(min_price)
        idx += 1
    if max_price is not None:
        conditions.append(f"p.price <= ${idx}")
        args.append(max_price)
        idx += 1
    if min_rating is not None:
        conditions.append(f"p.rating >= ${idx}")
        args.append(min_rating)
        idx += 1

    order = {
        "price_asc": "p.price ASC",
        "price_desc": "p.price DESC",
        "rating": "p.rating DESC",
        "newest": "p.created_at DESC",
    }.get(sort_by or "", None)
    if order is None:
        # 有查询时按文本相关性排序，否则按评分排序。
        order = f"ts_rank(p.search_vector, {tsquery}) DESC, p.rating DESC" if tsquery else "p.rating DESC"

    where = " AND ".join(conditions)
    sql = f"""
        SELECT p.id, p.name, p.category, p.brand, p.price, p.original_price,
               p.rating, p.review_count, p.description, p.is_active
        FROM products p
        WHERE {where}
        ORDER BY {order}
        LIMIT {safe_limit}
    """
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "category": r["category"],
                "brand": r["brand"],
                "price": float(r["price"]),
                "original_price": float(r["original_price"]) if r["original_price"] else None,
                "rating": float(r["rating"]),
                "review_count": r["review_count"],
                "description": r["description"],
            }
            for r in rows
        ]


@mcp.tool()
async def get_product_details(
    product_id: Annotated[str, "UUID of the product"],
) -> dict:
    """获取商品的完整详情，包括规格、库存状态和商家信息。"""
    async with _get_pool().acquire() as conn:
        p = await conn.fetchrow(
            """SELECT p.id, p.name, p.category, p.brand, p.price, p.original_price,
                      p.rating, p.review_count, p.description, p.specs,
                      u.name as seller_name,
                      COALESCE(SUM(wi.quantity), 0) as total_stock
               FROM products p
               LEFT JOIN users u ON p.seller_id = u.id
               LEFT JOIN warehouse_inventory wi ON wi.product_id = p.id
               WHERE p.id = $1
               GROUP BY p.id, u.name""",
            product_id,
        )
        if not p:
            return {"error": f"Product not found: {product_id}"}

        specs = p["specs"]
        if isinstance(specs, str):
            import json

            specs = json.loads(specs)

        return {
            "id": str(p["id"]),
            "name": p["name"],
            "category": p["category"],
            "brand": p["brand"],
            "price": float(p["price"]),
            "original_price": float(p["original_price"]) if p["original_price"] else None,
            "rating": float(p["rating"]),
            "review_count": p["review_count"],
            "description": p["description"],
            "specs": specs or {},
            "seller": p["seller_name"],
            "in_stock": p["total_stock"] > 0,
            "total_stock": int(p["total_stock"]),
        }


@mcp.tool()
async def compare_products(
    product_ids: Annotated[list[str], "List of 2–3 product UUIDs to compare"],
) -> list[dict]:
    """按价格、评分、规格和库存对 2–3 个商品做并排对比。"""
    if not 2 <= len(product_ids) <= 3:
        return [{"error": "Provide 2 or 3 product IDs to compare"}]

    results = []
    async with _get_pool().acquire() as conn:
        for pid in product_ids:
            row = await conn.fetchrow(
                """SELECT p.id, p.name, p.category, p.brand, p.price, p.rating,
                          p.review_count, p.specs,
                          COALESCE(SUM(wi.quantity), 0) as total_stock
                   FROM products p
                   LEFT JOIN warehouse_inventory wi ON wi.product_id = p.id
                   WHERE p.id = $1
                   GROUP BY p.id""",
                pid,
            )
            if row:
                import json

                specs = row["specs"]
                if isinstance(specs, str):
                    specs = json.loads(specs)
                results.append(
                    {
                        "id": str(row["id"]),
                        "name": row["name"],
                        "category": row["category"],
                        "brand": row["brand"],
                        "price": float(row["price"]),
                        "rating": float(row["rating"]),
                        "review_count": row["review_count"],
                        "specs": specs or {},
                        "in_stock": row["total_stock"] > 0,
                    }
                )
    return results


@mcp.tool()
async def get_trending_products(
    category: Annotated[str | None, "Optional category filter"] = None,
    days: Annotated[int, "Trending period in days (default 30)"] = 30,
    limit: Annotated[int, "Max results"] = 10,
) -> list[dict]:
    """获取按近期订单量排名的热门商品。"""
    safe_limit = min(limit, 50)
    conditions = ["p.is_active = TRUE", f"o.created_at >= NOW() - INTERVAL '{days} days'"]
    args: list = []
    idx = 1

    if category:
        conditions.append(f"p.category = ${idx}")
        args.append(category)
        idx += 1

    where = " AND ".join(conditions)
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT p.id, p.name, p.category, p.brand, p.price, p.rating,
                       p.review_count, COUNT(oi.id) as order_count
                FROM products p
                JOIN order_items oi ON oi.product_id = p.id
                JOIN orders o ON oi.order_id = o.id
                WHERE {where}
                GROUP BY p.id
                ORDER BY order_count DESC
                LIMIT {safe_limit}""",
            *args,
        )
        return [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "category": r["category"],
                "brand": r["brand"],
                "price": float(r["price"]),
                "rating": float(r["rating"]),
                "review_count": r["review_count"],
                "recent_orders": r["order_count"],
            }
            for r in rows
        ]


@mcp.tool()
async def get_price_history(
    product_id: Annotated[str, "UUID of the product"],
    days: Annotated[int, "History window: 30, 60, or 90 days"] = 30,
) -> dict:
    """获取价格趋势数据，包含平均值、最小值、最大值以及优惠质量信号。"""
    async with _get_pool().acquire() as conn:
        product = await conn.fetchrow("SELECT name, price FROM products WHERE id = $1", product_id)
        if not product:
            return {"error": f"Product not found: {product_id}"}

        rows = await conn.fetch(
            """SELECT price, recorded_at
               FROM price_history
               WHERE product_id = $1 AND recorded_at >= NOW() - ($2 || ' days')::interval
               ORDER BY recorded_at""",
            product_id,
            str(days),
        )
        current = float(product["price"])
        if not rows:
            return {
                "product_id": product_id,
                "product_name": product["name"],
                "current_price": current,
                "history": [],
                "summary": "No price history available",
            }

        prices = [float(r["price"]) for r in rows]
        avg = sum(prices) / len(prices)
        return {
            "product_id": product_id,
            "product_name": product["name"],
            "current_price": current,
            "period_days": days,
            "average_price": round(avg, 2),
            "min_price": round(min(prices), 2),
            "max_price": round(max(prices), 2),
            "is_good_deal": current <= avg * 0.95,
            "data_points": len(prices),
        }


# ─────────────────────── ASGI 入口点 ───────────────────────────────────

# Starlette ASGI 应用 —— 供 Docker Compose 中的 uvicorn 以及本地开发使用。
# MAF 的 MCPStreamableHTTPTool 连接到此处暴露的 /mcp 端点。
app = mcp.streamable_http_app()


def main() -> None:
    """Console script 入口点。通过 uvicorn 运行 HTTP 服务。"""
    import uvicorn

    port = int(os.environ.get("PORT", "9000"))
    uvicorn.run("ecommerce_mcp_product.server:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    # stdio 传输，用于配合 MCP Inspector 做本地测试
    mcp.run()
