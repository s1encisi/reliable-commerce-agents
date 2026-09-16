"""Small synthetic dataset for the isolated, no-model portfolio demo."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

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
# Public, synthetic credentials for a localhost-only demo. Never used for production.
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
        (CUSTOMER_ID, "customer@example.test", "customer", "Demo Customer"),
        (ADMIN_ID, "admin@example.test", "admin", "Demo Reviewer"),
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
        "name": "Demo Customer",
        "street": "100 Example Street",
        "city": "Example City",
        "state": "CA",
        "zip": "90001",
        "country": "US",
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
            f"Demo Headphones · {scenario.replace('_', ' ')}",
            "Synthetic portfolio product.",
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
                "Synthetic delivery evidence",
            )
