"""持久化操作标识，每次读取都按已认证用户隔离。"""

import json
from contextvars import ContextVar
from typing import Any
from uuid import UUID, uuid4, uuid5

import asyncpg

from shared.after_sales.approval import payload_hash
from shared.after_sales.policy import POLICY_VERSION
from shared.context import current_user_email
from shared.db import get_pool
from shared.tool_inputs import InitiateReturnInput

NAMESPACE = UUID("a6b24f65-643a-4a2b-8c14-70bd85e7c620")
current_operation_id: ContextVar[str | None] = ContextVar("after_sales_operation_id", default=None)


def operation_id_for(request: InitiateReturnInput) -> UUID:
    explicit = current_operation_id.get()
    return UUID(explicit) if explicit else uuid5(NAMESPACE, f"{current_user_email.get()}:{payload_hash(request)}")


def decode(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value or {})


async def reserve(request: InitiateReturnInput, operation_id: UUID) -> asyncpg.Record | None:
    """以自动提交预留操作，即使后续业务事务回滚也保留记录。"""
    async with get_pool().acquire() as conn:
        return await reserve_on(conn, request, operation_id)


async def reserve_on(
    conn: asyncpg.Connection, request: InitiateReturnInput, operation_id: UUID
) -> asyncpg.Record | None:
    """复用调用方事务，原子地关联工作流审批。"""
    await conn.execute(
        """INSERT INTO after_sales_operations
           (user_id, operation_id, order_id, payload_hash, request_payload, policy_version)
           SELECT o.user_id, $1, o.id, $2, $3::jsonb, $4 FROM orders o JOIN users u ON u.id = o.user_id
           WHERE o.id = $5 AND u.email = $6
           ON CONFLICT (user_id, operation_id) DO NOTHING""",
        operation_id,
        payload_hash(request),
        json.dumps(request.model_dump(mode="json")),
        POLICY_VERSION,
        request.order_id,
        current_user_email.get(),
    )
    return await load(conn, operation_id)


async def load(conn: asyncpg.Connection, operation_id: UUID, *, lock: str = "") -> asyncpg.Record | None:
    if lock not in {"", "update", "share"}:
        raise ValueError("Unsupported operation lock")
    suffix = {"": "", "update": " FOR UPDATE OF op", "share": " FOR SHARE OF op"}[lock]
    return await conn.fetchrow(
        """SELECT op.* FROM after_sales_operations op JOIN users u ON u.id = op.user_id
           JOIN orders o ON o.id = op.order_id AND o.user_id = op.user_id
           WHERE op.operation_id = $1 AND u.email = $2"""
        + suffix,
        operation_id,
        current_user_email.get(),
    )


async def save_result(
    conn: asyncpg.Connection,
    row: asyncpg.Record,
    result: dict[str, Any],
    *,
    request_id: UUID | None = None,
) -> dict[str, Any]:
    result = {**result, "operation_id": str(row["operation_id"])}
    status = result["outcome"]
    if status not in {"SUCCEEDED", "REJECTED", "NEEDS_REVIEW", "AWAITING_APPROVAL", "RETRYABLE_FAILURE"}:
        status = "REJECTED"
    await conn.execute(
        """UPDATE after_sales_operations SET status = $3, result = $4::jsonb,
           attempts = attempts + 1, updated_at = clock_timestamp() WHERE user_id = $1 AND operation_id = $2""",
        row["user_id"],
        row["operation_id"],
        status,
        json.dumps(result),
    )
    await conn.execute(
        """INSERT INTO after_sales_operation_events (user_id, operation_id, request_id, attempt, outcome, error_code)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        row["user_id"],
        row["operation_id"],
        request_id or uuid4(),
        row["attempts"] + 1,
        result["outcome"],
        result.get("error_code"),
    )
    if row["approval_id"] is not None and status != "AWAITING_APPROVAL":
        await conn.execute(
            """UPDATE tool_approval_requests SET status = $2, execution_result = $3::jsonb,
               resolved_at = clock_timestamp() WHERE id = $1 AND status = 'processing'""",
            row["approval_id"],
            "executed" if status == "SUCCEEDED" else "approved",
            json.dumps(result),
        )
    return result


async def confirm(operation_id: UUID, *, expected_hash: str | None = None) -> dict[str, Any] | None:
    """等待在途写入者，不能把旧 READY 记录误判为已回滚。"""
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout = '1500ms'")
            row = await load(conn, operation_id, lock="share")
            if row is None:
                return None
            if expected_hash is not None and row["payload_hash"] != expected_hash:
                return {
                    "success": False,
                    "outcome": "REJECTED",
                    "error_code": "OPERATION_CONFLICT",
                    "message": "This operation ID belongs to different parameters.",
                    "operation_id": str(operation_id),
                }
            result = decode(row["result"])
            return result or {
                "success": False,
                "outcome": row["status"],
                "operation_id": str(operation_id),
                "message": "No return has been committed for this operation.",
            }


async def reject_workflow(request: InitiateReturnInput, operation_id: UUID) -> dict[str, Any]:
    """被拒绝的工作流意图在重试和切换入口后仍保持拒绝。"""
    from shared.after_sales.service import failure

    async with get_pool().acquire() as conn:
        async with conn.transaction():
            await reserve_on(conn, request, operation_id)
            row = await load(conn, operation_id, lock="update")
            if row is None:
                return failure("ORDER_NOT_FOUND", "Order not found or access denied.")
            if row["payload_hash"] != payload_hash(request):
                return failure("OPERATION_CONFLICT", "This operation ID belongs to different parameters.")
            if row["status"] in {"SUCCEEDED", "REJECTED"}:
                return decode(row["result"])
            return await save_result(
                conn, row, failure("APPROVAL_DENIED", "Return rejected by reviewer. No return has been created.")
            )


async def deny_tool_approval(request_id: str, admin_email: str, note: str | None) -> bool | None:
    """先锁操作记录再锁审批记录，与执行路径的锁顺序一致。"""
    from shared.after_sales.service import failure

    async with get_pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM after_sales_operations WHERE approval_id = $1 FOR UPDATE", request_id
            )
            if row is None:
                return None  # 兼容旧审批或非退货审批。
            if row["status"] in {"SUCCEEDED", "REJECTED"}:
                return False
            changed = await conn.fetchval(
                """UPDATE tool_approval_requests SET status = 'denied', approved_by = $2,
                   admin_note = $3, resolved_at = clock_timestamp()
                   WHERE id = $1 AND status = 'pending' RETURNING id""",
                request_id,
                admin_email,
                note,
            )
            if changed is None:
                return False
            await save_result(
                conn, row, failure("APPROVAL_DENIED", "Return rejected by reviewer. No return has been created.")
            )
            return True
