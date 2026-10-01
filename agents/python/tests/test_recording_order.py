"""Only append fixed synthetic recording orders; old rows and wrong databases stay untouched."""

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from scripts.seed_portfolio import CUSTOMER_ID, ORDERS, prepare_recording_order, seed

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def recording_database(database_url):
    # This connection belongs to the existing isolated testcontainers fixture, not the host demo.
    admin = await asyncpg.connect(database_url)
    await admin.execute("CREATE DATABASE reliable_commerce_demo")
    parts = urlsplit(database_url)
    url = urlunsplit(parts._replace(path="/reliable_commerce_demo"))
    pool = await asyncpg.create_pool(url, min_size=1, max_size=3)
    try:
        await pool.execute((ROOT / "docker/postgres/init.sql").read_text())
        await seed(pool, reset=False)
        yield pool, url
    finally:
        await pool.close()
        await admin.execute("DROP DATABASE reliable_commerce_demo")
        await admin.close()


async def snapshot(pool):
    tables = await pool.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    return {
        row["tablename"]: sorted(
            await pool.fetch('SELECT to_jsonb(t)::text AS value FROM "' + row["tablename"].replace('"', '""') + '" t'),
            key=lambda item: item["value"],
        )
        for row in tables
    }


@pytest.mark.parametrize("scenario", ["normal", "missing_delivery", "expired"])
async def test_append_keeps_every_existing_row_and_uses_fixed_synthetic_identity(recording_database, scenario):
    pool, _ = recording_database
    # Model the already used E2E order and pre-existing approval/task that must not be reset.
    await pool.execute("UPDATE orders SET status = 'returned' WHERE id = $1", ORDERS["normal"])
    await pool.execute(
        """INSERT INTO tool_approval_requests(user_email,agent_name,tool_name,tool_input)
           VALUES('customer@example.test','recording-test','initiate_return','{}')"""
    )
    await pool.execute(
        "INSERT INTO agent_tasks(id,user_id,goal,status,revision) VALUES($1,$2,'保留已有任务','completed',3)",
        uuid4(),
        CUSTOMER_ID,
    )
    before = await snapshot(pool)
    result = await prepare_recording_order(pool, scenario=scenario)
    after = await snapshot(pool)
    new_id = UUID(result["order_id"])
    assert new_id not in ORDERS.values() and result["synthetic"] is True
    assert result["order_path"] == f"/orders/{new_id}"
    changed_tables = {"orders", "order_items", "order_status_history"}
    for table, old_rows in before.items():
        old_values = [row["value"] for row in old_rows]
        new_values = [row["value"] for row in after[table]]
        if table in changed_tables:
            assert set(old_values) <= set(new_values), table
        else:
            assert new_values == old_values, table
    assert len(after["orders"]) == len(before["orders"]) + 1
    assert len(after["order_items"]) == len(before["order_items"]) + 1
    assert len(after["order_status_history"]) == len(before["order_status_history"]) + int(
        scenario != "missing_delivery"
    )
    order = await pool.fetchrow("SELECT user_id,status,total FROM orders WHERE id=$1", new_id)
    assert order["user_id"] == CUSTOMER_ID and order["status"] == "delivered"
    assert await pool.fetchval("SELECT count(*) FROM order_items WHERE order_id=$1", new_id) == 1
    delivered_at = await pool.fetchval(
        "SELECT timestamp FROM order_status_history WHERE order_id=$1 AND status='delivered'", new_id
    )
    if scenario == "missing_delivery":
        assert delivered_at is None
    else:
        days = await pool.fetchval("SELECT EXTRACT(DAY FROM clock_timestamp() - $1::timestamptz)", delivered_at)
        assert days == (45 if scenario == "expired" else 5)


async def test_wrong_database_is_rejected_without_writes(clean_db):
    assert await clean_db.fetchval("SELECT current_database()") != "reliable_commerce_demo"
    before = await snapshot(clean_db)
    with pytest.raises(RuntimeError, match="restricted to reliable_commerce_demo"):
        await prepare_recording_order(clean_db)
    assert await snapshot(clean_db) == before


@pytest.mark.parametrize("changed", ["customer", "product"])
async def test_requires_existing_known_synthetic_customer_and_product(recording_database, changed):
    pool, _ = recording_database
    if changed == "customer":
        await pool.execute("UPDATE users SET role='seller' WHERE id=$1", CUSTOMER_ID)
    else:
        await pool.execute(
            "UPDATE products SET description='not a demo product' WHERE id=$1",
            UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1"),
        )
    before = await snapshot(pool)
    with pytest.raises(RuntimeError, match="Existing synthetic demo customer and product"):
        await prepare_recording_order(pool)
    assert await snapshot(pool) == before


async def test_cli_returns_a_fresh_order_id_without_resetting_old_orders(recording_database):
    pool, url = recording_database
    before = await pool.fetchval("SELECT count(*) FROM orders")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "scripts.prepare_demo_order",
        "--scenario",
        "normal",
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": url, "APP_ENV_FILE": "/dev/null"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
    assert process.returncode == 0, stderr.decode()
    result = json.loads(stdout)
    assert result["scenario"] == "normal" and result["synthetic"] is True
    assert await pool.fetchval("SELECT count(*) FROM orders WHERE id=$1", UUID(result["order_id"])) == 1
    assert await pool.fetchval("SELECT count(*) FROM orders") == before + 1
