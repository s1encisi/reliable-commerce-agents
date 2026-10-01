"""Real PostgreSQL + loopback HTTP + persistent SQLite supplier, including process termination."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from shared.after_sales import fulfillment, service
from tests.test_after_sales_entries import (  # noqa: F401
    EMAIL,
    NOW,
    approve,
    returns_db,
    seed_order,
)

# Imported fixture is intentionally injected.
# ruff: noqa: F811
pytestmark = pytest.mark.asyncio
ROOT = Path(__file__).resolve().parents[3]


class SupplierProcess:
    def __init__(self, database: Path, port: int):
        self.database = database
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.process: subprocess.Popen | None = None

    def start(self):
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "shared.after_sales.simulator",
                "--database",
                str(self.database),
                "--port",
                str(self.port),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env={**os.environ, "APP_ENV_FILE": "/dev/null"},
        )
        deadline = time.monotonic() + 15
        with httpx.Client(trust_env=False, timeout=0.3) as client:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(self.process.communicate()[1].decode())
                try:
                    if client.get(self.url + "/health").json().get("simulation") is True:
                        return
                except (httpx.RequestError, ValueError):
                    pass
                time.sleep(0.05)
        self.stop()
        raise AssertionError("Local supplier did not start")

    def stop(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.communicate(timeout=5)
            self.process = None

    def effects(self):
        with closing(sqlite3.connect(self.database)) as conn:
            return conn.execute("SELECT count(*) FROM pickups").fetchone()[0]


@pytest.fixture
def supplier_process(tmp_path, unused_tcp_port_factory):
    process = SupplierProcess(tmp_path / "supplier.sqlite", unused_tcp_port_factory())
    try:
        process.start()
        yield process
    finally:
        process.stop()


@pytest.fixture
def simulation(returns_db, monkeypatch):
    monkeypatch.setattr(service.settings, "FULFILLMENT_SIMULATION_ENABLED", True)
    return returns_db


async def create_return(pool):
    order = await seed_order(pool, delivered_at=NOW - timedelta(days=5))
    result = await service.request_return(str(order), "local pickup experiment")
    assert result["outcome"] == "SUCCEEDED"
    assert result["fulfillment"]["status"] == "PENDING"
    return result


async def test_business_commit_and_outbox_are_atomic(simulation, monkeypatch):
    order = await seed_order(simulation, delivered_at=NOW - timedelta(days=5))

    async def interrupt(stage, conn, operation_id):
        if stage == "after_business_write":
            assert await conn.fetchval("SELECT count(*) FROM fulfillment_outbox") == 1
            raise RuntimeError("injected rollback")

    monkeypatch.setattr(service, "_fault_boundary", interrupt)
    with pytest.raises(RuntimeError, match="injected rollback"):
        await service.request_return(str(order), "local pickup experiment")
    assert await simulation.fetchval("SELECT count(*) FROM returns") == 0
    assert await simulation.fetchval("SELECT count(*) FROM fulfillment_outbox") == 0
    assert await simulation.fetchval("SELECT status FROM orders WHERE id = $1", order) == "delivered"


async def test_approval_does_not_enqueue_before_business_execution(simulation, monkeypatch):
    monkeypatch.setattr(service.settings, "HITL_ENABLED", True)
    order = await seed_order(simulation, delivered_at=NOW - timedelta(days=5))
    pending = await service.request_return(str(order), "needs approval")
    assert pending["outcome"] == "AWAITING_APPROVAL"
    assert await simulation.fetchval("SELECT count(*) FROM fulfillment_outbox") == 0
    result = await approve(pending["request_id"])
    assert result["outcome"] == "SUCCEEDED"
    assert result["fulfillment"]["status"] == "PENDING"
    assert await simulation.fetchval("SELECT count(*) FROM fulfillment_outbox") == 1


async def test_real_delivery_parallel_claim_and_no_transaction_during_http(simulation, supplier_process, monkeypatch):
    result = await create_return(simulation)
    async with fulfillment.LocalSupplier(supplier_process.url) as supplier:
        original = supplier.submit

        async def assert_unlocked(key, payload):
            async with simulation.acquire() as conn:
                async with conn.transaction():
                    await conn.fetchval("SELECT id FROM fulfillment_outbox FOR UPDATE NOWAIT")
            return await original(key, payload)

        monkeypatch.setattr(supplier, "submit", assert_unlocked)
        results = await asyncio.gather(
            fulfillment.dispatch_one(simulation, supplier), fulfillment.dispatch_one(simulation, supplier)
        )
        assert sum(row is not None for row in results) == 1
        assert next(row for row in results if row)["status"] == "DELIVERED"
        assert await fulfillment.dispatch_one(simulation, supplier) is None
    assert supplier_process.effects() == 1
    # The original immutable operation receipt still says enqueued, never external completion.
    receipt = await service.get_operation(result["operation_id"])
    assert receipt["fulfillment"]["status"] == "PENDING"
    status = await fulfillment.get_status(simulation, result["return_id"], EMAIL)
    assert status["status"] == "DELIVERED" and status["supplier_receipt"]["simulation"] is True


async def test_supplier_duplicate_posts_and_parameter_conflict(simulation, supplier_process):
    await create_return(simulation)
    event = await simulation.fetchrow("SELECT * FROM fulfillment_outbox")
    payload = json.loads(event["payload"])
    async with fulfillment.LocalSupplier(supplier_process.url) as supplier:
        receipts = await asyncio.gather(*(supplier.submit(event["idempotency_key"], payload) for _ in range(4)))
        assert all(receipt == receipts[0] for receipt in receipts)
        changed = {**payload, "order_id": "11111111-1111-4111-8111-111111111111"}
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await supplier.submit(event["idempotency_key"], changed)
        assert exc.value.response.status_code == 409
    assert supplier_process.effects() == 1


async def test_lost_response_reconciles_after_supplier_restart_without_second_post(
    simulation, supplier_process, monkeypatch
):
    await create_return(simulation)
    async with fulfillment.LocalSupplier(supplier_process.url) as supplier:
        original = supplier.submit

        async def lose_response(key, payload):
            await original(key, payload)
            raise httpx.ReadError("injected lost response after durable supplier commit")

        with monkeypatch.context() as context:
            context.setattr(supplier, "submit", lose_response)
            first = await fulfillment.dispatch_one(simulation, supplier, retry_seconds=0)
        assert first["status"] == "UNKNOWN"
        assert supplier_process.effects() == 1
        supplier_process.stop()
        supplier_process.start()

        async def no_repeat_post(*args):
            pytest.fail("An existing durable receipt must be reconciled with GET")

        monkeypatch.setattr(supplier, "submit", no_repeat_post)
        second = await fulfillment.dispatch_one(simulation, supplier)
        assert second["status"] == "DELIVERED"
    assert supplier_process.effects() == 1
    assert await simulation.fetchval("SELECT attempts FROM fulfillment_outbox") == 2


@pytest.mark.parametrize("stage", ["after_claim", "after_supplier_confirmation"])
async def test_worker_process_death_and_new_worker_resume(simulation, database_url, supplier_process, stage):
    await create_return(simulation)
    env = {**os.environ, "DATABASE_URL": database_url, "APP_ENV_FILE": "/dev/null"}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ROOT / "agents/python/tests/helpers/fulfillment_worker.py"),
        "--supplier-url",
        supplier_process.url,
        "--crash",
        stage,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
    assert process.returncode == 73, (stdout.decode(), stderr.decode())
    assert await simulation.fetchval("SELECT status FROM fulfillment_outbox") == "PROCESSING"
    assert supplier_process.effects() == int(stage == "after_supplier_confirmation")
    await simulation.execute("UPDATE fulfillment_outbox SET lease_until = clock_timestamp() - INTERVAL '1 second'")
    recovery = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ROOT / "scripts/run_fulfillment_worker.py"),
        "--supplier-url",
        supplier_process.url,
        "--once",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(recovery.communicate(), timeout=20)
    assert recovery.returncode == 0, stderr.decode()
    assert json.loads(stdout)["status"] == "DELIVERED"
    assert supplier_process.effects() == 1


async def test_stale_worker_cannot_overwrite_a_new_claim(simulation, supplier_process):
    await create_return(simulation)
    old = await fulfillment.claim(simulation)
    await simulation.execute("UPDATE fulfillment_outbox SET lease_until = clock_timestamp() - INTERVAL '1 second'")
    new = await fulfillment.claim(simulation)
    assert old["claim_token"] != new["claim_token"]
    async with fulfillment.LocalSupplier(supplier_process.url) as supplier:
        receipt = await supplier.submit(new["idempotency_key"], json.loads(new["payload"]))
    stale = await fulfillment._finish(simulation, old, "DELIVERED", receipt=receipt)
    assert stale["status"] == "LEASE_LOST"
    assert await simulation.fetchval("SELECT status FROM fulfillment_outbox") == "PROCESSING"
    assert (await fulfillment._finish(simulation, new, "DELIVERED", receipt=receipt))["status"] == "DELIVERED"
    assert (await fulfillment._finish(simulation, old, "UNKNOWN"))["status"] == "LEASE_LOST"
    assert await simulation.fetchval("SELECT status FROM fulfillment_outbox") == "DELIVERED"


async def test_unreachable_supplier_has_bounded_attempts(simulation, unused_tcp_port_factory):
    await create_return(simulation)
    async with fulfillment.LocalSupplier(f"http://127.0.0.1:{unused_tcp_port_factory()}", timeout=0.3) as supplier:
        first = await fulfillment.dispatch_one(simulation, supplier, max_attempts=1, retry_seconds=0)
        second = await fulfillment.dispatch_one(simulation, supplier, max_attempts=1, retry_seconds=0)
        assert first["status"] == "UNKNOWN"
        assert second["status"] == "NEEDS_REVIEW"
        assert await fulfillment.dispatch_one(simulation, supplier, max_attempts=1) is None
    assert await simulation.fetchval("SELECT attempts FROM fulfillment_outbox") == 2


async def test_mismatched_supplier_receipt_is_not_marked_delivered(simulation, supplier_process):
    await create_return(simulation)
    event = await simulation.fetchrow("SELECT * FROM fulfillment_outbox")
    payload = {**json.loads(event["payload"]), "order_id": "11111111-1111-4111-8111-111111111111"}
    async with fulfillment.LocalSupplier(supplier_process.url) as supplier:
        await supplier.submit(event["idempotency_key"], payload)
        result = await fulfillment.dispatch_one(simulation, supplier)
    assert result["status"] == "NEEDS_REVIEW"
    assert await simulation.fetchval("SELECT last_error FROM fulfillment_outbox") == "INVALID_SUPPLIER_RECEIPT"


async def test_status_is_owner_scoped_and_migration_is_repeatable(simulation):
    result = await create_return(simulation)
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from orchestrator.routes import router
    from shared.jwt_utils import create_access_token

    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for email, expected in [(EMAIL, 200), ("another@example.test", 404)]:
            user_id = await simulation.fetchval(
                """INSERT INTO users (email, password_hash, name) VALUES ($1, 'test', 'Status reader')
                   ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email RETURNING id""",
                email,
            )
            token = create_access_token(email, "customer", str(user_id))
            response = await client.get(
                f"/api/returns/{result['return_id']}/fulfillment", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == expected
            if expected == 200:
                assert response.json()["status"] == "PENDING"
    migration = (ROOT / "docker/postgres/migrations/004_fulfillment_outbox.sql").read_text()
    await simulation.execute(migration)
    await simulation.execute(migration)
    assert await simulation.fetchval("SELECT count(*) FROM returns") == 1
    assert await simulation.fetchval("SELECT count(*) FROM fulfillment_outbox") == 1


async def test_disabled_simulation_does_not_create_outbox(returns_db, monkeypatch):
    monkeypatch.setattr(service.settings, "FULFILLMENT_SIMULATION_ENABLED", False)
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    result = await service.request_return(str(order), "ordinary local return")
    assert result["outcome"] == "SUCCEEDED" and "fulfillment" not in result
    assert await returns_db.fetchval("SELECT count(*) FROM fulfillment_outbox") == 0


@pytest.mark.parametrize(
    "url", ["https://example.com", "http://example.com", "http://user:password@localhost", "http://localhost/api"]
)
async def test_supplier_rejects_external_or_ambiguous_urls(url):
    with pytest.raises(ValueError):
        fulfillment.LocalSupplier(url)


@pytest.mark.parametrize("accepted", [False, True])
async def test_manual_reconciliation_never_submits_a_new_pickup(simulation, supplier_process, monkeypatch, accepted):
    await create_return(simulation)
    event = await simulation.fetchrow("SELECT * FROM fulfillment_outbox")
    async with fulfillment.LocalSupplier(supplier_process.url) as supplier:
        if accepted:
            await supplier.submit(event["idempotency_key"], json.loads(event["payload"]))
        await simulation.execute("UPDATE fulfillment_outbox SET status = 'NEEDS_REVIEW', attempts = 6")

        async def must_not_post(*args):
            pytest.fail("Manual reconciliation is read-only at the supplier")

        monkeypatch.setattr(supplier, "submit", must_not_post)
        result = await fulfillment.dispatch_one(simulation, supplier, reconcile_id=event["id"])
    assert result["status"] == ("DELIVERED" if accepted else "NEEDS_REVIEW")
    assert supplier_process.effects() == int(accepted)
