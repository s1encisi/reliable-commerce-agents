"""Local fulfillment experiment: transactional outbox, leased dispatch and receipt reconciliation.

DELIVERED means the loopback simulator durably accepted a pickup request.
It never means a parcel was collected or a refund was paid.
"""

import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import httpx


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def enqueue(conn: asyncpg.Connection, *, return_id: UUID, order_id: UUID, user_id: UUID) -> dict[str, Any]:
    payload = {"return_id": str(return_id), "order_id": str(order_id), "action": "return_pickup"}
    event_id = uuid4()
    await conn.execute(
        """INSERT INTO fulfillment_outbox
           (id, return_id, user_id, order_id, idempotency_key, payload, payload_hash)
           VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)""",
        event_id,
        return_id,
        user_id,
        order_id,
        f"return-pickup:{return_id}",
        json.dumps(payload),
        payload_hash(payload),
    )
    return {"id": str(event_id), "status": "PENDING", "simulation": True}


class LocalSupplier:
    """Only the local simulator is supported; no proxies or redirects to external systems."""

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        url = httpx.URL(base_url)
        if url.scheme != "http" or url.host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Fulfillment simulation requires a loopback HTTP URL")
        if url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment:
            raise ValueError("Supply a plain loopback origin without credentials, path or query")
        self.http = httpx.AsyncClient(base_url=base_url, timeout=timeout, trust_env=False, follow_redirects=False)

    async def __aenter__(self) -> "LocalSupplier":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.http.aclose()

    async def lookup(self, key: str) -> dict[str, Any] | None:
        response = await self.http.get(f"/pickups/{key}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def submit(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.http.post("/pickups", headers={"Idempotency-Key": key}, json=payload)
        response.raise_for_status()
        return response.json()


async def claim(
    pool: asyncpg.Pool, *, lease_seconds: float = 30, event_id: UUID | None = None
) -> asyncpg.Record | None:
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    # The single statement commits the claim before any HTTP operation begins.
    return await pool.fetchrow(
        """WITH candidate AS (
               SELECT id FROM fulfillment_outbox
               WHERE ($3::uuid IS NULL AND (
                         (status IN ('PENDING', 'UNKNOWN') AND next_attempt_at <= clock_timestamp())
                         OR (status = 'PROCESSING' AND lease_until < clock_timestamp())))
                  OR ($3::uuid IS NOT NULL AND id = $3 AND (
                         status IN ('UNKNOWN', 'NEEDS_REVIEW')
                         OR (status = 'PROCESSING' AND lease_until < clock_timestamp())))
               ORDER BY next_attempt_at, created_at
               FOR UPDATE SKIP LOCKED LIMIT 1
           )
           UPDATE fulfillment_outbox o
           SET status = 'PROCESSING', claim_token = $1,
               lease_until = clock_timestamp() + $2 * INTERVAL '1 second',
               attempts = attempts + 1, updated_at = clock_timestamp()
           FROM candidate c WHERE o.id = c.id RETURNING o.*""",
        uuid4(),
        lease_seconds,
        event_id,
    )


async def _finish(
    pool: asyncpg.Pool,
    event: asyncpg.Record,
    status: str,
    *,
    receipt: dict[str, Any] | None = None,
    error: str | None = None,
    retry_seconds: float = 1,
) -> dict[str, Any]:
    saved = await pool.fetchval(
        """UPDATE fulfillment_outbox
           SET status = $3, supplier_receipt = $4::jsonb, last_error = $5,
               claim_token = NULL, lease_until = NULL,
               next_attempt_at = clock_timestamp() + $6 * INTERVAL '1 second',
               updated_at = clock_timestamp()
           WHERE id = $1 AND claim_token = $2 AND status = 'PROCESSING'
           RETURNING id""",
        event["id"],
        event["claim_token"],
        status,
        json.dumps(receipt) if receipt is not None else None,
        error,
        retry_seconds,
    )
    return {"id": str(event["id"]), "status": status if saved else "LEASE_LOST", "simulation": True}


async def _delivery_boundary(stage: str, event: asyncpg.Record) -> None:
    """Test-only process interruption boundary; never exposed as an HTTP option."""


async def dispatch_one(
    pool: asyncpg.Pool,
    supplier: LocalSupplier,
    *,
    lease_seconds: float = 30,
    max_attempts: int = 5,
    retry_seconds: float = 1,
    reconcile_id: UUID | None = None,
) -> dict[str, Any] | None:
    if max_attempts < 1 or retry_seconds < 0:
        raise ValueError("Invalid dispatch retry limits")
    event = await claim(pool, lease_seconds=lease_seconds, event_id=reconcile_id)
    if event is None:
        return None
    await _delivery_boundary("after_claim", event)
    exhausted = reconcile_id is not None or event["attempts"] > max_attempts
    try:
        # Always reconcile first: a previous process may have lost the POST response.
        receipt = await supplier.lookup(event["idempotency_key"])
        if receipt is None:
            if exhausted:
                return await _finish(
                    pool,
                    event,
                    "NEEDS_REVIEW",
                    error="NOT_ACCEPTED_BY_SUPPLIER" if reconcile_id is not None else "ATTEMPT_LIMIT",
                )
            payload = event["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            receipt = await supplier.submit(event["idempotency_key"], payload)
        if (
            not isinstance(receipt, dict)
            or receipt.get("idempotency_key") != event["idempotency_key"]
            or receipt.get("payload_hash") != event["payload_hash"]
            or receipt.get("status") != "ACCEPTED"
            or receipt.get("simulation") is not True
            or not receipt.get("supplier_reference")
        ):
            return await _finish(pool, event, "NEEDS_REVIEW", error="INVALID_SUPPLIER_RECEIPT")
    except httpx.HTTPStatusError as exc:
        terminal = exc.response.status_code in {400, 401, 403, 409, 422}
        return await _finish(
            pool,
            event,
            "NEEDS_REVIEW" if terminal or exhausted else "UNKNOWN",
            error=f"SUPPLIER_HTTP_{exc.response.status_code}",
            retry_seconds=retry_seconds,
        )
    except httpx.RequestError as exc:
        return await _finish(
            pool,
            event,
            "NEEDS_REVIEW" if exhausted else "UNKNOWN",
            error=type(exc).__name__,
            retry_seconds=retry_seconds,
        )
    except (ValueError, TypeError):
        return await _finish(pool, event, "NEEDS_REVIEW", error="INVALID_SUPPLIER_RECEIPT")
    await _delivery_boundary("after_supplier_confirmation", event)
    return await _finish(pool, event, "DELIVERED", receipt=receipt)


async def get_status(pool: asyncpg.Pool, return_id: UUID, user_email: str) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        """SELECT o.id, o.status, o.attempts, o.supplier_receipt, o.last_error, o.updated_at
           FROM fulfillment_outbox o JOIN users u ON u.id = o.user_id
           WHERE o.return_id = $1 AND u.email = $2""",
        return_id,
        user_email,
    )
    if row is None:
        return None
    receipt = row["supplier_receipt"]
    if isinstance(receipt, str):
        receipt = json.loads(receipt)
    return {
        "id": str(row["id"]),
        "return_id": str(return_id),
        "status": row["status"],
        "simulation": True,
        "attempts": row["attempts"],
        "supplier_receipt": receipt,
        "last_error": row["last_error"],
        "updated_at": row["updated_at"].isoformat(),
    }
