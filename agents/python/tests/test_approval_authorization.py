"""Approval decisions belong to administrators; business execution keeps the requester identity."""

import asyncio
import json
from datetime import timedelta
from typing import Any
from uuid import UUID

import asyncpg
import pytest

from shared.after_sales import service
from shared.context import current_user_email, current_user_role
from tests.test_after_sales_entries import (  # noqa: F401
    EMAIL,
    NOW,
    assert_unmodified,
    http_post,
    returns_db,
    seed_order,
)

pytestmark = pytest.mark.asyncio

# Imported fixture is intentionally injected by pytest.
# ruff: noqa: F811
ADMIN = "reviewer@example.test"
OTHER = "other-requester@example.test"


async def pending_return(pool: asyncpg.Pool, entry: str, order_id: UUID) -> tuple[str, str]:
    if entry == "workflow":
        response = await http_post(
            "/api/chat",
            {"message": f"Return order {order_id}: damaged", "mode": "workflow:return-replace"},
        )
        assert response.status_code == 200, response.text
        run_id = await pool.fetchval("SELECT workflow_run_id FROM hitl_requests WHERE user_email = $1", EMAIL)
        operation_id = await pool.fetchval(
            "SELECT operation_id FROM after_sales_operations WHERE order_id = $1", order_id
        )
        return f"/api/orchestration/{run_id}/resume", str(operation_id)
    if entry == "tool":
        from shared.tools.return_tools import initiate_return

        result = await initiate_return.func(order_id=str(order_id), reason="damaged")
    else:
        response = await http_post(f"/api/orders/{order_id}/return", {"reason": "damaged"})
        assert response.status_code == 409, response.text
        result = response.json()
    assert result["outcome"] == "AWAITING_APPROVAL"
    return f"/api/admin/hitl/requests/{result['request_id']}/approve", result["operation_id"]


async def decide(path: str, *, email: str = ADMIN, role: str = "admin", approved: bool = True):
    # Model separate server requests rather than leaking authentication ContextVars into the test task.
    return await asyncio.create_task(
        http_post(path, {"approved": approved} if "/orchestration/" in path else {}, email=email, role=role)
    )


@pytest.mark.parametrize("entry", ["tool", "rest", "workflow"])
async def test_only_admin_can_decide_and_execution_uses_requester(
    returns_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    monkeypatch.setattr(service.settings, "HITL_ENABLED", True)
    order_id = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    path, operation_id = await pending_return(returns_db, entry, order_id)
    for email, role in [(EMAIL, "customer"), (OTHER, "customer"), ("seller@example.test", "seller")]:
        for approved in [True, False]:
            decision_path = path if entry == "workflow" or approved else path.removesuffix("/approve") + "/deny"
            denied = await decide(decision_path, email=email, role=role, approved=approved)
            assert denied.status_code == 403, denied.text
            await assert_unmodified(returns_db, order_id)
            assert (await service.get_operation(operation_id))["outcome"] == "AWAITING_APPROVAL"
    table = "hitl_requests" if entry == "workflow" else "tool_approval_requests"
    assert await returns_db.fetchval(f"SELECT status FROM {table}") == "pending"

    identities: list[tuple[str, str]] = []
    original = service._load_snapshot

    async def observe_identity(conn, uid, email, *, lock):
        identities.append((current_user_email.get(), current_user_role.get()))
        return await original(conn, uid, email, lock=lock)

    monkeypatch.setattr(service, "_load_snapshot", observe_identity)
    approved = await decide(path)
    assert approved.status_code == 200, approved.text
    receipt = approved.json() if entry == "workflow" else approved.json()["execution_result"]
    assert receipt["outcome"] == "SUCCEEDED", receipt
    assert identities and set(identities) == {(EMAIL, "customer")}
    assert await returns_db.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order_id) == 1
    assert (await service.get_operation(operation_id))["outcome"] == "SUCCEEDED"
    if entry == "workflow":
        decision = json.loads(await returns_db.fetchval("SELECT response FROM hitl_requests"))
        assert decision["reviewed_by"] == ADMIN
    else:
        assert await returns_db.fetchval("SELECT approved_by FROM tool_approval_requests") == ADMIN


@pytest.mark.parametrize("entry", ["tool", "rest", "workflow"])
async def test_admin_cannot_use_approval_after_order_ownership_changes(
    returns_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    monkeypatch.setattr(service.settings, "HITL_ENABLED", True)
    order_id = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    path, _ = await pending_return(returns_db, entry, order_id)
    other_id = await returns_db.fetchval(
        "INSERT INTO users (email, password_hash, name) VALUES ($1, 'hash', 'Other') RETURNING id", OTHER
    )
    await returns_db.execute("UPDATE orders SET user_id = $2 WHERE id = $1", order_id, other_id)
    approved = await decide(path)
    assert approved.status_code == 200, approved.text
    receipt = approved.json() if entry == "workflow" else approved.json()["execution_result"]
    assert receipt["outcome"] != "SUCCEEDED"
    await assert_unmodified(returns_db, order_id)


async def test_tool_approval_restores_caller_context_after_execution_failure(
    returns_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shared.hitl import claim_hitl_request, execute_approved_action

    monkeypatch.setattr(service.settings, "HITL_ENABLED", True)
    order_id = await seed_order(returns_db, delivered_at=NOW - timedelta(days=5))
    path, _ = await pending_return(returns_db, "tool", order_id)
    request_id = path.split("/")[-2]
    request = await claim_hitl_request(request_id)
    assert request is not None
    seen: list[tuple[str, str]] = []

    async def fail_submission(**kwargs: Any):
        seen.append((current_user_email.get(), current_user_role.get()))
        raise RuntimeError("injected execution failure")

    monkeypatch.setattr(service, "request_return", fail_submission)
    email_token = current_user_email.set(ADMIN)
    role_token = current_user_role.set("admin")
    try:
        with pytest.raises(RuntimeError, match="injected execution failure"):
            await execute_approved_action("initiate_return", request["tool_input"], EMAIL, request_id)
        assert seen == [(EMAIL, "customer")]
        assert current_user_email.get() == ADMIN
        assert current_user_role.get() == "admin"
    finally:
        current_user_email.reset(email_token)
        current_user_role.reset(role_token)
    await assert_unmodified(returns_db, order_id)
