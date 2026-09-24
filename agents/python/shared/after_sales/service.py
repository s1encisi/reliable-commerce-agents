"""工具、REST、审批和工作流共用的退货事务。

先锁定属于当前用户的订单，再检查已有退货与签收证据。持锁期间不调用
模型、网络或等待人工操作。业务写入与操作回执一并提交；响应丢失时
通过操作标识核实结果。
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from pydantic import ValidationError

from shared.after_sales import operations
from shared.after_sales.approval import bind_approval, current_return_approval, order_revision, payload_hash
from shared.after_sales.contracts import Outcome, ReturnDecision, ReturnSnapshot
from shared.after_sales.policy import POLICY_VERSION, evaluate_return
from shared.after_sales.recovery import TRANSIENT, RetryBudget, retry_read
from shared.config import settings
from shared.context import current_user_email
from shared.db import get_pool
from shared.tool_inputs import InitiateReturnInput

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


def approval_required(total: float) -> bool:
    return settings.HITL_ENABLED or total > settings.RETURN_HITL_THRESHOLD


def failure(code: str, message: str, outcome: Outcome = Outcome.REJECTED) -> dict[str, Any]:
    return {
        "success": False,
        "eligible": False,
        "outcome": outcome.value,
        "error_code": code,
        "error": message,
        "reason": message,
        "message": message,
        "retryable": False,
        "policy_version": POLICY_VERSION,
    }


async def _load_snapshot(conn: asyncpg.Connection, order_id: UUID, email: str, *, lock: bool) -> ReturnSnapshot | None:
    row = await conn.fetchrow(
        """SELECT o.id, o.user_id, o.status, o.total, o.created_at FROM orders o
           JOIN users u ON o.user_id = u.id WHERE o.id = $1 AND u.email = $2"""
        + (" FOR UPDATE OF o" if lock else ""),
        order_id,
        email,
    )
    if row is None:
        return None
    existing = await conn.fetchval("SELECT id FROM returns WHERE order_id = $1 LIMIT 1", order_id)
    if lock:
        # 锁定已有证据，防止并发更正或删除。
        # 新子记录的外键也需要获取当前已锁订单的键共享锁。
        history = await conn.fetch(
            "SELECT status, timestamp FROM order_status_history WHERE order_id = $1 FOR SHARE",
            order_id,
        )
        events = [row for row in history if row["status"] == "delivered"]
    else:
        # 两个不同值即可证明证据矛盾；判定资格时，
        # 不能让有效事件掩盖缺失的 NULL 值。
        events = await conn.fetch(
            """SELECT DISTINCT timestamp FROM order_status_history
               WHERE order_id = $1 AND status = 'delivered' ORDER BY timestamp NULLS FIRST LIMIT 2""",
            order_id,
        )
    return ReturnSnapshot(
        row["id"],
        row["user_id"],
        row["status"],
        row["total"],
        row["created_at"],
        tuple(r["timestamp"] for r in events),
        existing,
    )


def _decision_payload(snapshot: ReturnSnapshot, decision: ReturnDecision, now: datetime) -> dict[str, Any]:
    if not decision.eligible:
        result = failure(decision.code, decision.message, decision.outcome)
        if snapshot.existing_return_id:
            result["return_id"] = str(snapshot.existing_return_id)
    else:
        result = {
            "success": True,
            "eligible": True,
            "outcome": Outcome.READY.value,
            "error_code": decision.code,
            "policy_version": POLICY_VERSION,
            "message": decision.message,
            "total": float(snapshot.total),
            "order_revision": order_revision(snapshot),
            "requires_approval": approval_required(float(snapshot.total)),
        }
    result["order_id"] = str(snapshot.order_id)
    if decision.delivered_at is not None and decision.deadline is not None:
        result.update(
            delivered_at=decision.delivered_at.isoformat(),
            return_deadline=decision.deadline.isoformat(),
            days_remaining=max(0, (decision.deadline - now).days),
        )
    return result


async def check_eligibility(order_id: str) -> dict[str, Any]:
    email = current_user_email.get()
    if not email:
        return failure("AUTH_REQUIRED", "Sign in to check this order.")
    try:
        uid = UUID(order_id)
    except (ValueError, TypeError, AttributeError):
        return failure("VALIDATION_ERROR", "Choose a valid order ID.", Outcome.NEEDS_INPUT)
    async with get_pool().acquire() as conn:
        snapshot = await _load_snapshot(conn, uid, email, lock=False)
    if snapshot is None:
        return failure("ORDER_NOT_FOUND", "Order not found or access denied.")
    now = utc_now()
    return _decision_payload(snapshot, evaluate_return(snapshot, now=now), now)


async def prepare_approval_input(tool_input: dict[str, Any], email: str) -> dict[str, Any]:
    """入队前校验，并绑定服务端保存的不可变请求。"""
    request = InitiateReturnInput.model_validate(tool_input)
    async with get_pool().acquire() as conn:
        snapshot = await _load_snapshot(conn, request.order_id, email, lock=False)
    now = utc_now()
    if snapshot is None or not evaluate_return(snapshot, now=now).eligible:
        raise ValueError("Return eligibility must be resolved before requesting approval")
    return {
        **request.model_dump(mode="json"),
        "_return_approval": bind_approval(request, snapshot, email, now).to_dict(),
    }


class CommitUncertainError(Exception):
    """COMMIT 已发送或完成，但调用方没有收到结果。"""


async def _fault_boundary(stage: str, conn: asyncpg.Connection, operation_id: UUID) -> None:
    """隔离故障测试用的空操作注入点，不暴露为请求参数。"""


def _submission_decision(snapshot: ReturnSnapshot, now: datetime) -> ReturnDecision:
    """命名边界仅供评测消融使用，不增加运行时功能开关。"""
    return evaluate_return(snapshot, now=now)


async def _apply_operation(
    conn: asyncpg.Connection,
    request: InitiateReturnInput,
    operation_id: UUID,
    *,
    force_approval: bool,
) -> dict[str, Any]:
    row = await operations.load(conn, operation_id, lock="update")
    if row is None:
        return failure("ORDER_NOT_FOUND", "Order not found or access denied.")
    if row["payload_hash"] != payload_hash(request) or row["order_id"] != request.order_id:
        return {
            **failure("OPERATION_CONFLICT", "This operation ID belongs to different parameters."),
            "operation_id": str(operation_id),
        }
    if row["status"] in {"SUCCEEDED", "REJECTED"}:
        return operations.decode(row["result"])
    approval = current_return_approval.get()
    if row["status"] == "AWAITING_APPROVAL" and approval is None:
        return operations.decode(row["result"])
    snapshot = await _load_snapshot(conn, request.order_id, current_user_email.get(), lock=True)
    if snapshot is None:
        return failure("ORDER_NOT_FOUND", "Order not found or access denied.")
    now = utc_now()
    decision = _submission_decision(snapshot, now)
    if not decision.eligible:
        return await operations.save_result(conn, row, _decision_payload(snapshot, decision, now))
    if row["policy_version"] != POLICY_VERSION:
        return await operations.save_result(
            conn, row, failure("POLICY_CHANGED", "Start a new operation under the current policy.")
        )
    if approval is not None and not approval.valid_for(request, snapshot, current_user_email.get(), now):
        return await operations.save_result(
            conn, row, failure("APPROVAL_STALE", "Order, parameters or approval changed. Request fresh approval.")
        )
    if approval is not None and row["approval_id"] is not None:
        stored = await conn.fetchrow(
            "SELECT status, tool_input FROM tool_approval_requests WHERE id = $1 FOR UPDATE",
            row["approval_id"],
        )
        if (
            stored is None
            or stored["status"] != "processing"
            or operations.decode(stored["tool_input"]).get("_return_approval") != approval.to_dict()
        ):
            return await operations.save_result(
                conn, row, failure("APPROVAL_INVALID", "Approval is not valid for execution.")
            )
    if approval is None and (force_approval or approval_required(float(snapshot.total))):
        params = {
            **request.model_dump(mode="json"),
            "_operation_id": str(operation_id),
            "_return_approval": bind_approval(request, snapshot, current_user_email.get(), now).to_dict(),
        }
        approval_id = await conn.fetchval(
            """INSERT INTO tool_approval_requests (user_email, agent_name, tool_name, tool_input)
               VALUES ($1, 'after-sales', 'initiate_return', $2::jsonb) RETURNING id""",
            current_user_email.get(),
            json.dumps(params),
        )
        await conn.execute(
            "UPDATE after_sales_operations SET approval_id = $3 WHERE user_id = $1 AND operation_id = $2",
            row["user_id"],
            operation_id,
            approval_id,
        )
        return await operations.save_result(
            conn,
            row,
            {
                "success": False,
                "outcome": "AWAITING_APPROVAL",
                "status": "pending_approval",
                "policy_version": POLICY_VERSION,
                "request_id": str(approval_id),
                "order_id": str(request.order_id),
                "message": "Return request submitted for approval. No return or refund has been created.",
            },
        )
    label = f"/api/returns/{uuid4().hex[:12]}/label"
    return_id = await conn.fetchval(
        """INSERT INTO returns (order_id, user_id, reason, status, return_label_url, refund_method, refund_amount)
           VALUES ($1, $2, $3, 'requested', $4, $5, $6) RETURNING id""",
        request.order_id,
        snapshot.user_id,
        request.reason,
        label,
        request.refund_method,
        snapshot.total,
    )
    await conn.execute("UPDATE orders SET status = 'returned' WHERE id = $1", request.order_id)
    await conn.execute(
        "INSERT INTO order_status_history (order_id, status, notes) VALUES ($1, 'returned', $2)",
        request.order_id,
        f"Return initiated: {request.reason}",
    )
    await _fault_boundary("after_business_write", conn, operation_id)
    return await operations.save_result(
        conn,
        row,
        {
            "success": True,
            "outcome": "SUCCEEDED",
            "policy_version": POLICY_VERSION,
            "return_id": str(return_id),
            "order_id": str(request.order_id),
            "status": "requested",
            "reason": request.reason,
            "refund_method": request.refund_method,
            "refund_amount": float(snapshot.total),
            "return_label_url": label,
            "message": "Return request created. No refund has been issued.",
        },
    )


async def _commit_return(
    request: InitiateReturnInput,
    operation_id: UUID,
    *,
    force_approval: bool = False,
) -> dict[str, Any]:
    async with get_pool().acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        committing = False
        try:
            await conn.execute("SET LOCAL lock_timeout = '2500ms'")
            result = await _apply_operation(conn, request, operation_id, force_approval=force_approval)
            await _fault_boundary("before_commit", conn, operation_id)
            committing = True
            await tx.commit()
            await _fault_boundary("after_commit", conn, operation_id)
            return result
        except asyncio.CancelledError:
            # 取消后停止新尝试；后续状态查询会获取行锁，
            # 确认该事务是否真正提交。
            if not committing and not conn.is_closed():
                await tx.rollback()
            raise
        except Exception as exc:
            if committing:
                raise CommitUncertainError from exc
            if not conn.is_closed():
                await tx.rollback()
            raise


async def get_operation(
    operation_id: str,
    *,
    timeout_seconds: float = 3.0,
    expected_payload_hash: str | None = None,
) -> dict[str, Any]:
    if not current_user_email.get():
        return failure("AUTH_REQUIRED", "Sign in to inspect this operation.")
    try:
        uid = UUID(operation_id)
    except (ValueError, TypeError, AttributeError):
        return failure("VALIDATION_ERROR", "Provide a valid operation ID.", Outcome.NEEDS_INPUT)
    try:
        async with asyncio.timeout(timeout_seconds):
            result = (
                await operations.confirm(uid)
                if expected_payload_hash is None
                else await operations.confirm(uid, expected_hash=expected_payload_hash)
            )
    except (*TRANSIENT, TimeoutError, asyncpg.LockNotAvailableError):
        return {
            **failure("UNKNOWN_OUTCOME", "Result is not confirmed. Query this operation again."),
            "outcome": "UNKNOWN",
            "operation_id": str(uid),
        }
    return result or failure("OPERATION_NOT_FOUND", "Operation not found or access denied.")


async def request_return(
    order_id: str,
    reason: str,
    refund_method: str = "original_payment",
    *,
    force_approval: bool = False,
) -> dict[str, Any]:
    if not current_user_email.get():
        return failure("AUTH_REQUIRED", "Sign in to request a return.")
    try:
        request = InitiateReturnInput(order_id=order_id, reason=reason, refund_method=refund_method)
        operation_id = operations.operation_id_for(request)
    except (ValidationError, ValueError):
        return failure(
            "VALIDATION_ERROR",
            "Provide valid order/operation IDs, a reason of 1–255 characters, and a supported refund method.",
            Outcome.NEEDS_INPUT,
        )
    # 最多进行三次准备和三次已确认未提交的写入尝试，
    # 共用一个截止时间，不叠加传输层重试。
    budget = RetryBudget.start(seconds=10.0, max_attempts=3)
    try:
        row = await retry_read(lambda: operations.reserve(request, operation_id), budget)
        if row is None:
            return failure("ORDER_NOT_FOUND", "Order not found or access denied.")
        for attempt in range(1, 4):
            try:
                async with asyncio.timeout(budget.remaining):
                    return await _commit_return(request, operation_id, force_approval=force_approval)
            except CommitUncertainError:
                confirmed = await get_operation(str(operation_id), timeout_seconds=min(3.0, budget.remaining))
                if confirmed.get("outcome") == "READY":
                    return {
                        **confirmed,
                        "outcome": "RETRYABLE_FAILURE",
                        "retryable": True,
                        "message": "No return was committed. Retry with this same operation ID.",
                    }
                return confirmed
            except (*TRANSIENT, asyncpg.LockNotAvailableError):
                logger.warning("after_sales.retry operation_id=%s attempt=%s", operation_id, attempt)
                if attempt == 3:
                    raise
                await budget.wait(0.05 * 2 ** (attempt - 1))
    except TimeoutError:
        return {
            **failure("DEADLINE_EXCEEDED", "Operation deadline reached. Check the operation before retrying."),
            "outcome": "UNKNOWN",
            "operation_id": str(operation_id),
        }
    except TRANSIENT:
        return {
            **failure("DEPENDENCY_UNAVAILABLE", "Database is temporarily unavailable. Retry with this operation ID."),
            "outcome": "RETRYABLE_FAILURE",
            "operation_id": str(operation_id),
            "retryable": True,
        }
    except asyncpg.LockNotAvailableError:
        return {
            **failure("OPERATION_BUSY", "Operation is busy. Query its status before retrying."),
            "outcome": "UNKNOWN",
            "operation_id": str(operation_id),
        }
    raise RuntimeError("Unreachable operation state")
