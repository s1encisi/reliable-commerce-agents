"""Shared return tools — eligibility checks, returns, refunds."""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool
from pydantic import Field, ValidationError

from shared.context import current_user_email
from shared.db import get_pool
from shared.guardrails.roles import requires_role
from shared.idempotency import idempotent
from shared.tool_inputs import (
    ProcessRefundInput,
    validation_error_payload,
)


@tool(
    name="check_return_eligibility",
    description="Check if an order is eligible for return. Orders must be delivered within the last 30 days.",
)
async def check_return_eligibility(
    order_id: Annotated[str, Field(description="UUID of the order to check")],
) -> dict:
    from shared.after_sales.service import check_eligibility

    return await check_eligibility(order_id)


@tool(
    name="initiate_return",
    description="Initiate a return for a delivered order. Generates a return shipping label.",
    approval_mode="always_require",
)
@idempotent("initiate_return:returns-v1", cache_result=lambda result: result.get("success") is True)
async def initiate_return(
    order_id: Annotated[str, Field(description="UUID of the order to return")],
    reason: Annotated[str, Field(description="Reason for the return")],
    refund_method: Annotated[
        str, Field(description="Refund method: 'original_payment' or 'store_credit'")
    ] = "original_payment",
) -> dict:
    from shared.after_sales.service import request_return

    return await request_return(order_id, reason, refund_method)


@tool(
    name="process_refund",
    description="Process the refund for an approved return. Updates return status to refunded.",
    approval_mode="always_require",
)
@idempotent("process_refund")
@requires_role("customer", "seller", "admin")
async def process_refund(
    return_id: Annotated[str, Field(description="UUID of the return to process refund for")],
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    try:
        validated = ProcessRefundInput(return_id=return_id)
    except ValidationError as exc:
        return validation_error_payload("process_refund", exc)
    return_id = str(validated.return_id)

    pool = get_pool()
    async with pool.acquire() as conn:
        # Lock the return row before re-checking status. Without this a
        # double-click on "issue refund" can fund the customer twice.
        async with conn.transaction():
            ret = await conn.fetchrow(
                """SELECT r.id, r.order_id, r.status, r.refund_method, r.refund_amount
                   FROM returns r
                   JOIN users u ON r.user_id = u.id
                   WHERE r.id = $1 AND u.email = $2
                   FOR UPDATE OF r""",
                return_id,
                email,
            )
            if not ret:
                return {"error": f"Return not found or access denied: {return_id}"}

            if ret["status"] == "refunded":
                return {"error": "This return has already been refunded.", "return_id": str(ret["id"])}

            if ret["status"] == "denied":
                return {"error": "This return was denied and cannot be refunded.", "return_id": str(ret["id"])}

            await conn.execute(
                "UPDATE returns SET status = 'refunded', resolved_at = NOW() WHERE id = $1",
                return_id,
            )

        refund_amount = float(ret["refund_amount"]) if ret["refund_amount"] else 0
        refund_method = ret["refund_method"] or "original_payment"

        return {
            "return_id": str(ret["id"]),
            "order_id": str(ret["order_id"]),
            "status": "refunded",
            "refund_method": refund_method,
            "refund_amount": refund_amount,
            "message": f"Refund of ${refund_amount:.2f} processed via {refund_method.replace('_', ' ')}.",
        }


@tool(name="get_return_status", description="Get the current return processing status for an order.")
async def get_return_status(
    order_id: Annotated[str, Field(description="UUID of the order to check return status for")],
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        # Verify order ownership
        order_check = await conn.fetchrow(
            """SELECT o.id FROM orders o
               JOIN users u ON o.user_id = u.id
               WHERE o.id = $1 AND u.email = $2""",
            order_id,
            email,
        )
        if not order_check:
            return {"error": f"Order not found or access denied: {order_id}"}

        ret = await conn.fetchrow(
            """SELECT id, reason, status, return_label_url, refund_method,
                      refund_amount, created_at, resolved_at
               FROM returns
               WHERE order_id = $1""",
            order_id,
        )
        if not ret:
            return {
                "order_id": order_id,
                "has_return": False,
                "message": "No return found for this order.",
            }

        return {
            "return_id": str(ret["id"]),
            "order_id": order_id,
            "has_return": True,
            "status": ret["status"],
            "reason": ret["reason"],
            "refund_method": ret["refund_method"],
            "refund_amount": float(ret["refund_amount"]) if ret["refund_amount"] else None,
            "return_label_url": ret["return_label_url"],
            "created_at": ret["created_at"].isoformat(),
            "resolved_at": ret["resolved_at"].isoformat() if ret["resolved_at"] else None,
        }
