"""M3: actual database transactions, process death and dropped COMMIT replies."""

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import asyncpg
import pytest

from shared.after_sales import operations, service
from shared.after_sales.operations import current_operation_id
from shared.after_sales.recovery import RetryBudget, retry_read
from shared.context import current_user_email
from tests.test_after_sales_entries import EMAIL, NOW, returns_db, seed_order  # noqa: F401,F811

pytestmark = pytest.mark.asyncio

# Imported fixture is intentionally consumed through pytest's parameter injection.
# ruff: noqa: F811


async def test_tool_approval_middleware_replays_a_committed_receipt(
    returns_db: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shared import hitl
    from tests.test_after_sales_entries import approve

    monkeypatch.setattr(hitl.settings, "HITL_ENABLED", True)
    monkeypatch.setattr(service.settings, "HITL_ENABLED", True)
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    context = SimpleNamespace(
        function=SimpleNamespace(name="initiate_return"),
        arguments={"order_id": str(order), "reason": "middleware test"},
        result=None,
    )

    async def must_not_execute() -> None:
        pytest.fail("Unapproved downstream tool execution")

    await hitl.HITLFunctionMiddleware().process(context, must_not_execute)
    assert context.result["outcome"] == "AWAITING_APPROVAL"
    receipt = await approve(context.result["request_id"])
    current_user_email.set(EMAIL)
    await hitl.HITLFunctionMiddleware().process(context, must_not_execute)
    assert context.result == receipt
    assert await returns_db.fetchval("SELECT count(*) FROM tool_approval_requests") == 1
    assert await returns_db.fetchval("SELECT count(*) FROM returns") == 1


async def test_denial_is_visible_in_operation_status_and_cannot_be_requeued_silently(
    returns_db: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_after_sales_entries import http_post

    monkeypatch.setattr(service.settings, "HITL_ENABLED", True)
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    pending = await service.request_return(str(order), "denied intent")
    response = await http_post(
        f"/api/admin/hitl/requests/{pending['request_id']}/deny", {}, email="admin@example.test", role="admin"
    )
    assert response.status_code == 200
    current_user_email.set(EMAIL)
    receipt = await service.get_operation(pending["operation_id"])
    assert receipt["outcome"] == "REJECTED" and receipt["error_code"] == "APPROVAL_DENIED"
    assert await service.request_return(str(order), "denied intent") == receipt
    assert await returns_db.fetchval("SELECT count(*) FROM tool_approval_requests") == 1
    assert await returns_db.fetchval("SELECT count(*) FROM returns") == 0


@pytest.mark.parametrize("approved", [True, False])
async def test_workflow_operation_has_persisted_wait_and_terminal_receipts(
    returns_db: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
    approved: bool,
) -> None:
    from tests.test_after_sales_entries import http_post

    monkeypatch.setattr(service.settings, "HITL_ENABLED", True)
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    message = f"Return order {order} because it is damaged"
    assert (await http_post("/api/chat", {"message": message, "mode": "workflow:return-replace"})).status_code == 200
    row = await returns_db.fetchrow(
        "SELECT operation_id, result FROM after_sales_operations WHERE order_id = $1", order
    )
    operation_id = str(row["operation_id"])
    pending = await service.get_operation(operation_id)
    assert pending["outcome"] == "AWAITING_APPROVAL"
    # A repeated initial request references the original approval, not a second one.
    assert (await http_post("/api/chat", {"message": message, "mode": "workflow:return-replace"})).status_code == 200
    assert await returns_db.fetchval("SELECT count(*) FROM hitl_requests") == 1
    response = await http_post(f"/api/orchestration/{pending['workflow_run_id']}/resume", {"approved": approved})
    assert response.status_code == 200, response.text
    receipt = await service.get_operation(operation_id)
    assert receipt["outcome"] == ("SUCCEEDED" if approved else "REJECTED")
    replay = await http_post("/api/chat", {"message": message, "mode": "workflow:return-replace"})
    assert replay.status_code == 200
    assert await returns_db.fetchval("SELECT count(*) FROM hitl_requests") == 1
    assert await returns_db.fetchval("SELECT count(*) FROM returns") == int(approved)


async def test_explicit_operation_replays_and_conflicting_payload_does_not_overwrite(returns_db: asyncpg.Pool) -> None:
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    token = current_operation_id.set(str(uuid4()))
    try:
        first = await service.request_return(str(order), "Wrong size")
        assert first["success"]
        assert await service.request_return(str(order), "Wrong size") == first
        conflict = await service.request_return(str(order), "Different intent")
        assert conflict["error_code"] == "OPERATION_CONFLICT"
        assert await service.get_operation(first["operation_id"]) == first
    finally:
        current_operation_id.reset(token)
    assert await returns_db.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order) == 1


async def test_operation_lookup_reauthenticates_owner(returns_db: asyncpg.Pool) -> None:
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    result = await service.request_return(str(order), "wrong")
    token = current_user_email.set("different@example.test")
    try:
        denied = await service.get_operation(result["operation_id"])
        unknown = await service.get_operation(str(uuid4()))
        assert denied == unknown
        assert denied["error_code"] == "OPERATION_NOT_FOUND"
    finally:
        current_user_email.reset(token)


async def test_same_operation_concurrently_returns_identical_result(returns_db: asyncpg.Pool) -> None:
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    token = current_operation_id.set(str(uuid4()))
    try:
        results = await asyncio.gather(*(service.request_return(str(order), "wrong") for _ in range(5)))
    finally:
        current_operation_id.reset(token)
    assert all(result == results[0] and result["success"] for result in results)
    assert await returns_db.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order) == 1
    assert await returns_db.fetchval("SELECT count(*) FROM after_sales_operation_events") == 1


@pytest.mark.parametrize("crash", ["before_commit", "after_commit"])
async def test_process_death_recovers_without_duplicate_effect(
    returns_db: asyncpg.Pool,
    database_url: str,
    crash: str,
) -> None:
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    operation = str(uuid4())
    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "PYTHONPATH": str(root),
        "OPENAI_API_KEY": "",
        "AZURE_OPENAI_KEY": "",
        "AZURE_OPENAI_API_KEY": "",
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(root / "tests/helpers/after_sales_worker.py"),
        "--order",
        str(order),
        "--operation",
        operation,
        "--email",
        EMAIL,
        "--now",
        NOW.isoformat(),
        "--crash",
        crash,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    assert proc.returncode == 71, (stdout.decode(), stderr.decode())
    before_retry = await returns_db.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order)
    assert before_retry == (1 if crash == "after_commit" else 0)
    token = current_operation_id.set(operation)
    try:
        result = await service.request_return(str(order), "Crash test")
        assert result["success"] is True
        assert await service.get_operation(operation) == result
    finally:
        current_operation_id.reset(token)
    assert await returns_db.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order) == 1
    assert (
        await returns_db.fetchval(
            "SELECT count(*) FROM order_status_history WHERE order_id = $1 AND status = 'returned'",
            order,
        )
        == 1
    )


async def test_known_rollback_retries_under_one_budget(
    returns_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    attempts = 0

    async def fail_twice(stage: str, conn: asyncpg.Connection, operation_id: UUID) -> None:
        nonlocal attempts
        if stage == "before_commit":
            attempts += 1
            if attempts < 3:
                raise asyncpg.SerializationError("injected serialization conflict")

    monkeypatch.setattr(service, "_fault_boundary", fail_twice)
    result = await service.request_return(str(order), "wrong")
    assert result["success"]
    assert attempts == 3
    assert await returns_db.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order) == 1


async def test_lost_result_after_commit_is_reconciled(
    returns_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    hits = 0

    async def lose_result(stage: str, conn: asyncpg.Connection, operation_id: UUID) -> None:
        nonlocal hits
        if stage == "after_commit":
            hits += 1
            raise ConnectionResetError("injected lost application response")

    monkeypatch.setattr(service, "_fault_boundary", lose_result)
    result = await service.request_return(str(order), "wrong")
    assert result["success"] and hits == 1
    assert await returns_db.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order) == 1


async def test_cancel_before_commit_leaves_no_partial_effect(
    returns_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    reached = asyncio.Event()

    async def wait_before_commit(stage: str, conn: asyncpg.Connection, operation_id: UUID) -> None:
        if stage == "before_commit":
            reached.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(service, "_fault_boundary", wait_before_commit)
    task = asyncio.create_task(service.request_return(str(order), "wrong"))
    await asyncio.wait_for(reached.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await returns_db.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order) == 0


async def test_read_retry_budget_and_excess_retry_after() -> None:
    attempts = 0

    async def read() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionResetError("injected")
        return "ok"

    budget = RetryBudget.start(seconds=2)
    assert await retry_read(read, budget) == "ok"
    assert attempts == 3
    with pytest.raises(TimeoutError):
        await budget.wait(100)  # a Retry-After longer than the remaining deadline


async def test_persistent_dependency_failure_stops_after_three_preparation_attempts(
    returns_db: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    attempts = 0

    async def fail(*args: object) -> None:
        nonlocal attempts
        attempts += 1
        raise ConnectionResetError("injected outage")

    monkeypatch.setattr(operations, "reserve", fail)
    result = await service.request_return(str(order), "wrong")
    assert result["outcome"] == "RETRYABLE_FAILURE" and attempts == 3
    assert await returns_db.fetchval("SELECT count(*) FROM returns") == 0


async def test_commit_reply_dropped_by_tcp_proxy_is_reconciled(
    returns_db: asyncpg.Pool,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real wire failure: forward COMMIT to Postgres, discard its acknowledgement."""
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    url = urlsplit(database_url)
    dropped = asyncio.Event()
    handlers: set[asyncio.Task] = set()

    async def proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        handlers.add(task)
        remote_reader, remote_writer = await asyncio.open_connection(url.hostname, url.port)
        commit_sent = False

        async def upstream() -> None:
            nonlocal commit_sent
            while data := await reader.read(65536):
                if b"COMMIT" in data:
                    commit_sent = True
                remote_writer.write(data)
                await remote_writer.drain()

        async def downstream() -> None:
            buffered = b""
            while data := await remote_reader.read(65536):
                if commit_sent and not dropped.is_set():
                    buffered += data
                    if b"COMMIT\x00" in buffered:
                        dropped.set()
                        return
                    continue
                writer.write(data)
                await writer.drain()

        pumps = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
        try:
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            writer.close()
            remote_writer.close()
            await asyncio.gather(writer.wait_closed(), remote_writer.wait_closed(), return_exceptions=True)
            handlers.discard(task)

    server = await asyncio.start_server(proxy, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    netloc = f"{url.username}:{url.password}@127.0.0.1:{port}"
    proxy_url = urlunsplit((url.scheme, netloc, url.path, "", ""))
    pool = await asyncpg.create_pool(proxy_url, min_size=1, max_size=3, ssl=False)
    try:
        with monkeypatch.context() as context:
            context.setattr("shared.db._pool", pool)
            result = await asyncio.wait_for(service.request_return(str(order), "wire failure"), timeout=15)
            assert dropped.is_set(), "infrastructure errors must not count as injected faults"
            assert result["success"] is True
            assert await service.get_operation(result["operation_id"]) == result
        assert await returns_db.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order) == 1
    finally:
        await pool.close()
        server.close()
        await server.wait_closed()
        for handler in tuple(handlers):
            handler.cancel()
        await asyncio.gather(*tuple(handlers), return_exceptions=True)


async def test_fresh_schema_matches_incremental_migration() -> None:
    root = Path(__file__).resolve().parents[3]
    migration = (root / "docker/postgres/migrations/001_after_sales_operations.sql").read_text().strip()
    init = (root / "docker/postgres/init.sql").read_text()
    assert migration in init


async def test_uncertain_result_stays_unknown_when_confirmation_is_unavailable(
    returns_db: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    writes = 0

    async def lose_reply(stage: str, conn: asyncpg.Connection, operation_id: UUID) -> None:
        nonlocal writes
        if stage == "after_commit":
            writes += 1
            raise ConnectionResetError("lost reply")

    async def cannot_confirm(operation_id: UUID) -> None:
        raise ConnectionResetError("confirmation connection unavailable")

    with monkeypatch.context() as context:
        context.setattr(service, "_fault_boundary", lose_reply)
        context.setattr(operations, "confirm", cannot_confirm)
        result = await service.request_return(str(order), "unknown result")
    assert result["outcome"] == "UNKNOWN" and writes == 1
    confirmed = await service.get_operation(result["operation_id"])
    assert confirmed["success"] is True
    assert await returns_db.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order) == 1


async def test_confirmation_does_not_mistake_inflight_ready_row_for_rollback(
    returns_db: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    operation = str(uuid4())
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold(stage: str, conn: asyncpg.Connection, operation_id: UUID) -> None:
        if stage == "before_commit":
            entered.set()
            await release.wait()

    token = current_operation_id.set(operation)
    try:
        monkeypatch.setattr(service, "_fault_boundary", hold)
        task = asyncio.create_task(service.request_return(str(order), "in flight"))
        await asyncio.wait_for(entered.wait(), 5)
        result = await service.get_operation(operation)
        assert result["outcome"] == "UNKNOWN"
        release.set()
        assert (await task)["success"]
        assert (await service.get_operation(operation))["success"]
    finally:
        release.set()
        current_operation_id.reset(token)


async def test_incremental_migration_is_repeatable_and_refuses_duplicate_data(returns_db: asyncpg.Pool) -> None:
    root = Path(__file__).resolve().parents[3]
    sql = (root / "docker/postgres/migrations/001_after_sales_operations.sql").read_text()
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    await service.request_return(str(order), "migration")
    async with returns_db.acquire() as conn:
        for _ in range(2):
            async with conn.transaction():
                await conn.execute(sql)

        assert await conn.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order) == 1
        await conn.execute("DROP INDEX ux_returns_one_per_order")
        duplicate = await conn.fetchval(
            """INSERT INTO returns (order_id, user_id, reason)
               SELECT order_id, user_id, 'duplicate fixture' FROM returns WHERE order_id = $1 RETURNING id""",
            order,
        )
        try:
            with pytest.raises(asyncpg.RaiseError, match="Duplicate returns exist"):
                async with conn.transaction():
                    await conn.execute(sql)
            assert await conn.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order) == 2
        finally:
            await conn.execute("DELETE FROM returns WHERE id = $1", duplicate)
            async with conn.transaction():
                await conn.execute(sql)


@pytest.mark.parametrize("after_write", [False, True])
async def test_workflow_processing_record_recovers_after_interrupted_resume(
    returns_db: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
    after_write: bool,
) -> None:
    from orchestrator.modes import get_mode
    from shared.config import settings
    from tests.test_after_sales_entries import http_post

    monkeypatch.setattr(service.settings, "HITL_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    order = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    response = await http_post(
        "/api/chat", {"message": f"Return order {order} because it is damaged", "mode": "workflow:return-replace"}
    )
    assert response.status_code == 200
    run_id = await returns_db.fetchval("SELECT workflow_run_id FROM hitl_requests WHERE user_email = $1", EMAIL)
    mode = get_mode("workflow:return-replace")
    original = mode.resume

    async def interrupted(**kwargs: Any) -> AsyncIterator[Any]:
        if after_write:
            async for event in original(**kwargs):
                yield event
        raise RuntimeError("injected interrupted resume")

    with monkeypatch.context() as context:
        context.setattr(mode, "resume", interrupted)
        with pytest.raises(RuntimeError, match="injected interrupted resume"):
            await http_post(f"/api/orchestration/{run_id}/resume", {"approved": True})
    assert (
        await returns_db.fetchval("SELECT status FROM hitl_requests WHERE workflow_run_id = $1", run_id) == "processing"
    )
    assert await returns_db.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order) == int(after_write)
    conflict = await http_post(f"/api/orchestration/{run_id}/resume", {"approved": False})
    assert conflict.status_code == 409
    recovered = await http_post(f"/api/orchestration/{run_id}/resume", {"approved": True})
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["outcome"] == "SUCCEEDED"
    assert await returns_db.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order) == 1
