"""由工具、REST、审批执行与工作流共用的同一个退货事务。

在检查既有退货记录与送达证据之前，始终先锁定所属订单。
事务持有锁期间不会发生任何模型/网络调用，也不会等待人工。
操作 ID 以及丢失 COMMIT 响应后的恢复属于后续 M3 的工作。
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from pydantic import ValidationError

from shared.after_sales.approval import bind_approval, current_return_approval, order_revision
from shared.after_sales.contracts import Outcome, ReturnDecision, ReturnSnapshot
from shared.after_sales.policy import POLICY_VERSION, evaluate_return
from shared.config import settings
from shared.context import current_user_email
from shared.db import get_pool
from shared.tool_inputs import InitiateReturnInput


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
        # 保护既有证据，避免并发的更正/删除。
        # 新增的子行也需要在我们已锁定的订单上取得外键的键共享锁。
        history = await conn.fetch(
            "SELECT status, timestamp FROM order_status_history WHERE order_id = $1 FOR SHARE",
            order_id,
        )
        events = [row for row in history if row["status"] == "delivered"]
    else:
        # 两个不同的值就足以确立"记录相互矛盾"。在判定资格时，
        # NULL 不能被某个有效事件掩盖。
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
    """在入队之前先校验，并绑定服务端存储的不可变请求。"""
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


async def _commit_return(request: InitiateReturnInput, email: str) -> dict[str, Any]:
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            snapshot = await _load_snapshot(conn, request.order_id, email, lock=True)
            if snapshot is None:
                return failure("ORDER_NOT_FOUND", "Order not found or access denied.")
            # 在等到订单锁*之后*才读取时钟，而不是在事务开始时读取：
            # 等待锁这件事本身就可能跨过退货截止时间。
            now = utc_now()
            decision = evaluate_return(snapshot, now=now)
            if not decision.eligible:
                return _decision_payload(snapshot, decision, now)
            approval = current_return_approval.get()
            if approval is not None and not approval.valid_for(request, snapshot, email, now):
                return failure("APPROVAL_STALE", "Order, parameters or approval changed. Request fresh approval.")
            if approval is None and approval_required(float(snapshot.total)):
                return failure(
                    "APPROVAL_REQUIRED", "Return request needs approval before submission.", Outcome.AWAITING_APPROVAL
                )
            label = f"/api/returns/{uuid4().hex[:12]}/label"
            return_id = await conn.fetchval(
                """INSERT INTO returns
                   (order_id, user_id, reason, status, return_label_url, refund_method, refund_amount)
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
        return {
            "success": True,
            "outcome": Outcome.SUCCEEDED.value,
            "policy_version": POLICY_VERSION,
            "return_id": str(return_id),
            "order_id": str(request.order_id),
            "status": "requested",
            "reason": request.reason,
            "refund_method": request.refund_method,
            "refund_amount": float(snapshot.total),
            "return_label_url": label,
            "message": "Return request created. No refund has been issued.",
        }


async def request_return(order_id: str, reason: str, refund_method: str = "original_payment") -> dict[str, Any]:
    email = current_user_email.get()
    if not email:
        return failure("AUTH_REQUIRED", "Sign in to request a return.")
    try:
        request = InitiateReturnInput(order_id=order_id, reason=reason, refund_method=refund_method)
    except ValidationError:
        return failure(
            "VALIDATION_ERROR",
            "Provide a valid order ID, a reason of 1–255 characters, and a supported refund method.",
            Outcome.NEEDS_INPUT,
        )
    result = await _commit_return(request, email)
    if result.get("error_code") != "APPROVAL_REQUIRED":
        return result
    from shared.hitl import _create_hitl_request

    try:
        request_id = await _create_hitl_request(
            email, None, "after-sales", "initiate_return", request.model_dump(mode="json")
        )
    except ValueError:
        return failure("APPROVAL_STALE", "Order changed while requesting approval. Check eligibility again.")
    return {
        "success": False,
        "outcome": Outcome.AWAITING_APPROVAL.value,
        "status": "pending_approval",
        "policy_version": POLICY_VERSION,
        "request_id": str(request_id),
        "order_id": str(request.order_id),
        "message": "Return request submitted for approval. No return or refund has been created.",
    }
