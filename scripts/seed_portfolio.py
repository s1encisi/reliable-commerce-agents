"""隔离、无模型作品演示使用的小型合成数据集。"""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
from shared.jwt_utils import hash_password

CUSTOMER_ID = UUID("11111111-1111-4111-8111-111111111111")
ADMIN_ID = UUID("22222222-2222-4222-8222-222222222222")
ORDERS = {
    "normal": UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"),
    "missing_delivery": UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"),
    "expired": UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3"),
    "response_loss": UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4"),
}
# 仅用于本地演示的公开合成账号，不用于生产。
DEMO_PASSWORD = "DemoPass123!"


async def seed(pool: asyncpg.Pool, *, reset: bool = False) -> None:
    if await pool.fetchval("SELECT current_database()") != "reliable_commerce_demo":
        raise RuntimeError("Portfolio seeding is restricted to reliable_commerce_demo")
    if reset:
        tables = await pool.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename != 'schema_migrations'"
        )
        names = ",".join('"' + r["tablename"].replace('"', '""') + '"' for r in tables)
        await pool.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
    now = datetime.now(UTC)
    for uid, email, role, name in [
        (CUSTOMER_ID, "customer@example.test", "customer", "演示客户"),
        (ADMIN_ID, "admin@example.test", "admin", "演示审批员"),
    ]:
        await pool.execute(
            "INSERT INTO users(id,email,password_hash,name,role) VALUES($1,$2,$3,$4,$5) ON CONFLICT(id) DO NOTHING",
            uid,
            email,
            hash_password(DEMO_PASSWORD),
            name,
            role,
        )
    address = {
        "name": "演示客户",
        "street": "合成示例街道100号",
        "city": "合成示例城市",
        "state": "CA",
        "zip": "90001",
        "country": "US",
    }
    warehouse_id = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    await pool.execute(
        "INSERT INTO warehouses(id,name,location,region) VALUES($1,'演示仓库','合成示例城市','east') "
        "ON CONFLICT(id) DO NOTHING",
        warehouse_id,
    )
    labels = {
        "normal": "可申请",
        "missing_delivery": "签收待核实",
        "expired": "已超过期限",
        "response_loss": "中断恢复",
    }
    for index, (scenario, oid) in enumerate(ORDERS.items(), 1):
        created = await pool.fetchval(
            """INSERT INTO orders(id,user_id,status,total,shipping_address,billing_address,created_at)
               VALUES($1,$2,'delivered',79.00,$3::jsonb,$3::jsonb,$4) ON CONFLICT(id) DO NOTHING RETURNING id""",
            oid,
            CUSTOMER_ID,
            json.dumps(address),
            now - timedelta(days=60),
        )
        product_id = UUID(f"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb{index}")
        await pool.execute(
            """INSERT INTO products(id,name,description,category,price,image_url,rating)
               VALUES($1,$2,$3,'Electronics',79.00,'/demo-headphones.svg',4.5) ON CONFLICT(id) DO NOTHING""",
            product_id,
            f"合成耳机 Headphones · {labels[scenario]}",
            "仅用于本地作品演示的合成商品，不代表真实销售或库存。",
        )
        await pool.execute(
            "INSERT INTO warehouse_inventory(warehouse_id,product_id,quantity) VALUES($1,$2,10) "
            "ON CONFLICT(warehouse_id,product_id) DO NOTHING",
            warehouse_id,
            product_id,
        )
        if not created:
            continue
        await pool.execute(
            "INSERT INTO order_items(order_id,product_id,quantity,unit_price,subtotal) VALUES($1,$2,1,79.00,79.00)",
            oid,
            product_id,
        )
        if scenario != "missing_delivery":
            await pool.execute(
                "INSERT INTO order_status_history(order_id,status,timestamp,notes) VALUES($1,'delivered',$2,$3)",
                oid,
                now - timedelta(days=45 if scenario == "expired" else 5),
                "合成签收证据",
            )


async def prepare_recording_order(pool: asyncpg.Pool, *, scenario: str = "normal") -> dict[str, str | bool]:
    """只追加一笔固定场景的合成订单，绝不重置已有订单、审批、任务或账号。"""
    delivery_days = {"normal": 5, "missing_delivery": None, "expired": 45}
    if scenario not in delivery_days:
        raise ValueError("Only normal, missing_delivery and expired recording scenarios are supported")
    product_index = list(ORDERS).index(scenario) + 1
    product_id = UUID(f"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb{product_index}")
    now = datetime.now(UTC)
    order_id = uuid4()
    address = {
        "name": "演示客户",
        "street": "合成示例街道100号",
        "city": "合成示例城市",
        "state": "CA",
        "zip": "90001",
        "country": "US",
    }
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL lock_timeout = '3s'")
        if await conn.fetchval("SELECT current_database()") != "reliable_commerce_demo":
            raise RuntimeError("Recording orders are restricted to reliable_commerce_demo")
        customer = await conn.fetchval(
            """SELECT id FROM users WHERE id = $1 AND email = 'customer@example.test'
               AND role = 'customer' FOR SHARE""",
            CUSTOMER_ID,
        )
        product = await conn.fetchrow(
            """SELECT id, price FROM products
               WHERE id = $1 AND description = $2 AND is_active FOR SHARE""",
            product_id,
            "仅用于本地作品演示的合成商品，不代表真实销售或库存。",
        )
        if customer is None or product is None:
            raise RuntimeError("Existing synthetic demo customer and product are required; no records were created")
        await conn.execute(
            """INSERT INTO orders(id,user_id,status,total,shipping_address,billing_address,created_at)
               VALUES($1,$2,'delivered',$3,$4::jsonb,$4::jsonb,$5)""",
            order_id,
            customer,
            product["price"],
            json.dumps(address),
            now - timedelta(days=60),
        )
        await conn.execute(
            """INSERT INTO order_items(order_id,product_id,quantity,unit_price,subtotal)
               VALUES($1,$2,1,$3,$3)""",
            order_id,
            product_id,
            product["price"],
        )
        days = delivery_days[scenario]
        if days is not None:
            await conn.execute(
                """INSERT INTO order_status_history(order_id,status,timestamp,notes)
                   VALUES($1,'delivered',$2,$3)""",
                order_id,
                now - timedelta(days=days),
                f"录制用合成签收证据：{scenario}",
            )
    return {
        "order_id": str(order_id),
        "order_path": f"/orders/{order_id}",
        "scenario": scenario,
        "customer_email": "customer@example.test",
        "synthetic": True,
    }
