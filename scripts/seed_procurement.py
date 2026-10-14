"""仅向 reliable_commerce_demo 追加固定采购合成商品；不重置库存、券或旧订单。"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

import asyncpg

DEMO_EMAIL = "customer@example.test"
DESCRIPTION = "仅用于本地采购 Agent 工程演示的合成商品，不代表真实销售或库存。"


def entity_id(key: str):
    return uuid5(NAMESPACE_URL, f"reliable-commerce:procurement-demo:v1:{key}")


async def seed(pool) -> dict:
    async with pool.acquire() as conn, conn.transaction():
        if await conn.fetchval("SELECT current_database()") != "reliable_commerce_demo":
            raise RuntimeError("采购演示种子只接受专用 reliable_commerce_demo 数据库")
        owner = await conn.fetchrow("SELECT id,role,is_active FROM users WHERE email=$1", DEMO_EMAIL)
        if not owner or owner["role"] != "customer" or not owner["is_active"]:
            raise RuntimeError("先用既有 showcase 初始化创建合成 customer@example.test 客户")
        warehouse_id = entity_id("warehouse")
        await conn.execute(
            """INSERT INTO warehouses(id,name,location,region)
               VALUES($1,'采购合成仓','本地演示','east') ON CONFLICT(id) DO NOTHING""",
            warehouse_id,
        )
        products = []
        for key, name, price, quantity, rating, review in [
            (
                "a",
                "采购专用耳机 headphones A",
                "500.00",
                12,
                "4.50",
                "声音清晰；长时间佩戴需要休息。",
            ),
            (
                "b",
                "采购专用耳机 headphones B",
                "430.00",
                16,
                "4.30",
                "连接稳定；包装说明较简略。",
            ),
            (
                "c",
                "采购专用耳机 headphones C",
                "380.00",
                12,
                "4.20",
                "可正常使用；包装说明较简略。",
            ),
            (
                "d",
                "采购专用耳机 headphones D",
                "390.00",
                10,
                "4.40",
                "能满足基本使用；头梁偏紧。",
            ),
            (
                "e",
                "采购专用键盘 keyboard E",
                "210.00",
                20,
                "4.60",
                "按键清晰；夜间打字有声音。",
            ),
        ]:
            pid = entity_id(key)
            existing = await conn.fetchrow("SELECT name,description FROM products WHERE id=$1", pid)
            if existing and (existing["name"] != name or existing["description"] != DESCRIPTION):
                raise RuntimeError("采购合成商品 ID 与既有数据冲突，未修改旧数据")
            await conn.execute(
                """INSERT INTO products(id,name,description,category,brand,price,rating,review_count)
                   VALUES($1,$2,$3,'Electronics','ProcurementDemo',$4,$5,1) ON CONFLICT(id) DO NOTHING""",
                pid,
                name,
                DESCRIPTION,
                Decimal(price),
                Decimal(rating),
            )
            await conn.execute(
                """INSERT INTO warehouse_inventory(warehouse_id,product_id,quantity)
                   VALUES($1,$2,$3) ON CONFLICT(warehouse_id,product_id) DO NOTHING""",
                warehouse_id,
                pid,
                quantity,
            )
            await conn.execute(
                """INSERT INTO reviews(id,product_id,user_id,rating,title,body,verified_purchase)
                   VALUES($1,$2,$3,4,'合成采购原文',$4,true) ON CONFLICT(id) DO NOTHING""",
                entity_id(f"review-{key}"),
                pid,
                owner["id"],
                review,
            )
            products.append({"key": key, "product_id": str(pid), "name": name})
        coupon_id = entity_id("coupon")
        await conn.execute(
            """INSERT INTO coupons(id,code,description,discount_type,discount_value,min_spend,
                                   usage_limit,times_used,valid_from,valid_until,applicable_categories,user_specific_email)
               VALUES($1,'PROCUREMENT-DEMO-30','本地合成满300减30单券','fixed',30,300,100,0,$2,$3,
                      ARRAY['Electronics'],$4) ON CONFLICT(id) DO NOTHING""",
            coupon_id,
            datetime.now(UTC) - timedelta(minutes=1),
            datetime.now(UTC) + timedelta(days=30),
            DEMO_EMAIL,
        )
    return {
        "database": "reliable_commerce_demo",
        "mode": "append_only",
        "products": products,
        "coupon_id": str(coupon_id),
        "example_spec": {
            "query": "采购专用耳机",
            "quantity": 3,
            "alternative_count": 2,
            "total_budget": "1500.00",
            "currency": "CNY",
            "in_stock": True,
            "min_rating": "4.0",
            "preferences": ["保留原始评论和风险引用"],
            "budget_scope": "goods",
            "quote_policy": "single_coupon_v1",
        },
    }


async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    try:
        print(json.dumps(await seed(pool), ensure_ascii=False, indent=2))
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
