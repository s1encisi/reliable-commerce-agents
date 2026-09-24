"""MCP 服务 —— 库存与履约领域。

通过 MCP（Model Context Protocol，模型上下文协议）1.x 的 streamable HTTP
传输暴露库存水平、仓库可用性、补货计划、运费估算以及承运商对比。

任何兼容 MCP 的智能体或框架都能调用这些工具，无需定制集成 —— MCP 协议
会自动处理工具发现、schema 校验和调用分发。

独立运行（供 MCP Inspector 使用的 stdio）：
    uv run python -m ecommerce_mcp_inventory.server

作为 HTTP 服务运行（供 Docker Compose 中的 uvicorn 使用）：
    uvicorn ecommerce_mcp_inventory.server:app --host 0.0.0.0 --port 9001

通过已安装的 console script 运行：
    ecommerce-mcp-inventory
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

# OAuth 2.1 资源服务器模式（可选 —— 默认关闭，快速上手流程不变）。
MCP_AUTH_ENABLED = os.environ.get("MCP_AUTH_ENABLED", "false").lower() == "true"

_pool: asyncpg.Pool | None = None


@asynccontextmanager
async def _lifespan(server: FastMCP):
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=8)
    logger.info("inventory-mcp: DB pool ready")
    try:
        yield
    finally:
        if _pool:
            await _pool.close()


_mcp_kwargs: dict = {
    "instructions": (
        "Inventory and fulfillment data for the E-Commerce Agents platform. "
        "Check stock levels, warehouse availability, restock schedules, and "
        "shipping estimates across the East, Central, and West regional warehouses."
    ),
    "lifespan": _lifespan,
    # 只要 `host` 保持默认的 "127.0.0.1"，FastMCP 就会自动启用
    # DNS 重绑定 Host 头保护，且仅把 localhost/127.0.0.1/::1 加入白名单 ——
    # 这会让经由 Docker 网络发起的每一次真实调用都被静默地返回 421
    # （例如某个专业智能体调用 http://mcp-inventory:9001）。
    # 本应用实际上是经 `uvicorn ... --host 0.0.0.0` 提供服务的
    # （参见 main()/Dockerfile 的 CMD），所以这里也显式声明该值 ——
    # `host="127.0.0.1"` 从来就不符合该进程真实的运行方式。
    "host": "0.0.0.0",
}

if MCP_AUTH_ENABLED:
    from mcp.server.auth.settings import AuthSettings

    from ecommerce_mcp_inventory.auth import (
        AUTH_SERVER_ISSUER,
        MCP_INVENTORY_REQUIRED_SCOPE,
        JwksTokenVerifier,
    )

    _resource_url = os.environ.get("MCP_INVENTORY_RESOURCE_URL", "http://localhost:9001/mcp")
    _mcp_kwargs["token_verifier"] = JwksTokenVerifier()
    _mcp_kwargs["auth"] = AuthSettings(
        issuer_url=AUTH_SERVER_ISSUER,
        resource_server_url=_resource_url,
        required_scopes=[MCP_INVENTORY_REQUIRED_SCOPE],
    )
    logger.info("inventory-mcp: OAuth 2.1 resource-server mode enabled issuer=%s", AUTH_SERVER_ISSUER)

mcp = FastMCP("inventory-fulfillment-mcp", **_mcp_kwargs)


def _get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — server not started yet")
    return _pool


# ─────────────────────── 工具 ──────────────────────────────────────────────


@mcp.tool()
async def check_stock(product_id: Annotated[str, "UUID of the product to check"]) -> dict:
    """查询某商品在所有区域仓库的实时库存水平。"""
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT w.name as warehouse, w.region, wi.quantity, wi.reorder_threshold
               FROM warehouse_inventory wi
               JOIN warehouses w ON wi.warehouse_id = w.id
               WHERE wi.product_id = $1
               ORDER BY w.region""",
            product_id,
        )
        if not rows:
            return {"product_id": product_id, "in_stock": False, "total_quantity": 0, "warehouses": []}

        warehouses = [
            {
                "warehouse": r["warehouse"],
                "region": r["region"],
                "quantity": r["quantity"],
                "low_stock": r["quantity"] <= r["reorder_threshold"],
            }
            for r in rows
        ]
        total = sum(r["quantity"] for r in rows)
        return {
            "product_id": product_id,
            "in_stock": total > 0,
            "total_quantity": total,
            "warehouses": warehouses,
        }


@mcp.tool()
async def get_warehouse_availability(
    product_id: Annotated[str, "UUID of the product"],
) -> dict:
    """获取某商品的仓库库存以及即将到来的补货计划。"""
    async with _get_pool().acquire() as conn:
        inventory = await conn.fetch(
            """SELECT w.name, w.region, w.location, wi.quantity, wi.reorder_threshold
               FROM warehouse_inventory wi
               JOIN warehouses w ON wi.warehouse_id = w.id
               WHERE wi.product_id = $1""",
            product_id,
        )
        restocks = await conn.fetch(
            """SELECT w.name as warehouse, rs.expected_quantity, rs.expected_date
               FROM restock_schedule rs
               JOIN warehouses w ON rs.warehouse_id = w.id
               WHERE rs.product_id = $1 AND rs.expected_date >= CURRENT_DATE
               ORDER BY rs.expected_date""",
            product_id,
        )
        return {
            "product_id": product_id,
            "warehouses": [
                {
                    "name": r["name"],
                    "region": r["region"],
                    "location": r["location"],
                    "quantity": r["quantity"],
                    "low_stock": r["quantity"] <= r["reorder_threshold"],
                }
                for r in inventory
            ],
            "upcoming_restocks": [
                {
                    "warehouse": r["warehouse"],
                    "expected_quantity": r["expected_quantity"],
                    "expected_date": r["expected_date"].isoformat(),
                }
                for r in restocks
            ],
        }


@mcp.tool()
async def get_restock_schedule(
    product_id: Annotated[str, "UUID of the product"],
) -> list[dict]:
    """获取所有仓库即将到来的补货日期与数量。"""
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT w.name as warehouse, w.region, rs.expected_quantity, rs.expected_date
               FROM restock_schedule rs
               JOIN warehouses w ON rs.warehouse_id = w.id
               WHERE rs.product_id = $1 AND rs.expected_date >= CURRENT_DATE
               ORDER BY rs.expected_date""",
            product_id,
        )
        return [
            {
                "warehouse": r["warehouse"],
                "region": r["region"],
                "expected_quantity": r["expected_quantity"],
                "expected_date": r["expected_date"].isoformat(),
            }
            for r in rows
        ]


@mcp.tool()
async def estimate_shipping(
    product_id: Annotated[str, "UUID of the product"],
    destination_region: Annotated[str, "Destination region: east, central, or west"],
) -> dict:
    """估算从最近的有货仓库发货的运费与送达时间。"""
    async with _get_pool().acquire() as conn:
        source = await conn.fetchrow(
            """SELECT w.region
               FROM warehouse_inventory wi
               JOIN warehouses w ON wi.warehouse_id = w.id
               WHERE wi.product_id = $1 AND wi.quantity > 0
               ORDER BY CASE w.region
                   WHEN $2 THEN 0
                   WHEN 'central' THEN 1
                   ELSE 2
               END
               LIMIT 1""",
            product_id,
            destination_region,
        )
        if not source:
            return {"available": False, "message": "Product out of stock in all warehouses"}

        rates = await conn.fetch(
            """SELECT c.name as carrier, c.speed_tier, sr.price,
                      sr.estimated_days_min, sr.estimated_days_max
               FROM shipping_rates sr
               JOIN carriers c ON sr.carrier_id = c.id
               WHERE sr.region_from = $1 AND sr.region_to = $2
               ORDER BY sr.price""",
            source["region"],
            destination_region,
        )
        return {
            "available": True,
            "ships_from": source["region"],
            "destination": destination_region,
            "options": [
                {
                    "carrier": r["carrier"],
                    "speed_tier": r["speed_tier"],
                    "price": float(r["price"]),
                    "estimated_days": f"{r['estimated_days_min']}–{r['estimated_days_max']}",
                }
                for r in rates
            ],
        }


@mcp.tool()
async def compare_carriers(
    region_from: Annotated[str, "Origin region: east, central, or west"],
    region_to: Annotated[str, "Destination region: east, central, or west"],
) -> list[dict]:
    """对比两个区域之间的所有承运商，含价格与预计送达时间。"""
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT c.name as carrier, c.speed_tier, sr.price,
                      sr.estimated_days_min, sr.estimated_days_max
               FROM shipping_rates sr
               JOIN carriers c ON sr.carrier_id = c.id
               WHERE sr.region_from = $1 AND sr.region_to = $2
               ORDER BY sr.price""",
            region_from,
            region_to,
        )
        return [
            {
                "carrier": r["carrier"],
                "speed_tier": r["speed_tier"],
                "price": float(r["price"]),
                "estimated_days": f"{r['estimated_days_min']}–{r['estimated_days_max']}",
            }
            for r in rows
        ]


# ─────────────────────── ASGI 入口点 ───────────────────────────────────

# Starlette ASGI 应用 —— 供 Docker Compose 中的 uvicorn 以及本地开发使用。
# MAF 的 MCPStreamableHTTPTool 连接到此处暴露的 /mcp 端点。
app = mcp.streamable_http_app()


def main() -> None:
    """Console script 入口点。通过 uvicorn 运行 HTTP 服务。"""
    import uvicorn

    port = int(os.environ.get("PORT", "9001"))
    uvicorn.run("ecommerce_mcp_inventory.server:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    # stdio 传输，用于配合 MCP Inspector 做本地测试
    mcp.run()
