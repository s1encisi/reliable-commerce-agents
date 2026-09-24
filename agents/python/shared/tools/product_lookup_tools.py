"""专业智能体共用的商品名称到标识解析。

评论、定价和库存工具需要商品 UUID，但用户通常只提供名称。
提供真实查询能力，避免专业智能体在没有商品搜索工具时编造 UUID，
再把查询空结果误报为商品没有数据。
"""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.db import get_pool


@tool(
    name="find_product_by_name",
    description=(
        "Resolve a product's UUID from its name (or a close match). Call this first "
        "whenever the user refers to a product by name rather than a UUID, before "
        "calling any tool that requires product_id."
    ),
)
async def find_product_by_name(
    name: Annotated[str, Field(description="Product name or a close match, e.g. 'Sony WH-1000XM5'")],
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        # 先忽略大小写做完整名称匹配，再按词做子串匹配，
        # 与 search_products 的分词方式一致，
        # 让品牌与品类组合也能找到具体型号。
        exact = await conn.fetchrow(
            "SELECT id, name FROM products WHERE is_active = TRUE AND name ILIKE $1 LIMIT 1",
            name,
        )
        if exact:
            return {"found": True, "product_id": str(exact["id"]), "product_name": exact["name"]}

        words = [w for w in name.strip().split() if len(w) >= 2]
        if not words:
            return {"found": False, "message": f"No product matching '{name}'"}

        conditions = " AND ".join(f"name ILIKE ${i + 1}" for i in range(len(words)))
        args = [f"%{w}%" for w in words]
        row = await conn.fetchrow(
            f"SELECT id, name FROM products WHERE is_active = TRUE AND {conditions} LIMIT 1",
            *args,
        )
        if not row:
            return {"found": False, "message": f"No product matching '{name}'"}

        return {"found": True, "product_id": str(row["id"]), "product_name": row["name"]}
