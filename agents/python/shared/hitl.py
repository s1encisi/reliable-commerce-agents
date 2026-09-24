"""人工参与（HITL）审批中间件与数据库辅助函数。

敏感工具在执行前进入 hitl_requests 待审批队列，管理员通过
/admin/approvals 批准或拒绝。批准后才执行底层业务操作；拒绝会记录
决定。是否启用由 HITL_ENABLED 控制，关闭时工具按原路径执行。

表定义位于 docker/postgres/init.sql；已有部署应按迁移说明升级，
不能为了更新表结构而重建有价值的业务数据。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from agent_framework._middleware import FunctionInvocationContext, FunctionMiddleware

from shared.config import settings
from shared.context import current_session_id, current_user_email
from shared.idempotency import idempotent

logger = logging.getLogger(__name__)

# 执行前需要人工审批的工具。
# 与 @tool(approval_mode="always_require") 声明一致。
HITL_GATED_TOOLS: frozenset[str] = frozenset(
    {
        "cancel_order",
        "modify_order",
        "process_refund",
        "initiate_return",
        "place_backorder",
    }
)


# ─────────────────────── Middleware ─────────────────────────────────────────


class HITLFunctionMiddleware(FunctionMiddleware):
    """拦截受控工具，送入审批队列。

    创建 pending 记录，不执行工具，并返回 pending_approval 结构化结果。
    实际业务执行由管理员审批端点调用 execute_approved_action 完成。
    """

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        if not settings.HITL_ENABLED:
            await call_next()
            return

        fn = getattr(context, "function", None)
        tool_name: str = getattr(fn, "name", None) or getattr(fn, "__name__", None) or ""

        if tool_name not in HITL_GATED_TOOLS:
            await call_next()
            return

        # 提取工具参数。
        raw_args: dict[str, Any] = {}
        if hasattr(context, "arguments"):
            args = context.arguments
            if isinstance(args, dict):
                raw_args = args

        user_email = current_user_email.get() or "unknown"
        session_id = current_session_id.get("") or None

        # 从上下文层级读取智能体名称。
        agent_name = _extract_agent_name(context)

        if tool_name == "initiate_return":
            from shared.after_sales.service import failure, request_return

            try:
                # 操作服务只入队一次，或重放已确认的回执；
                # 不会把已有成功结果转为新审批。
                context.result = await request_return(**raw_args, force_approval=True)
            except (TypeError, ValueError):
                context.result = failure("VALIDATION_ERROR", "Invalid return request parameters.")
            except Exception:
                logger.exception("Return approval preparation failed")
                context.result = failure(
                    "DEPENDENCY_UNAVAILABLE", "Could not confirm the return request. Check its status."
                )
            return

        try:
            request_id = await _create_hitl_request(
                user_email=user_email,
                session_id=session_id,
                agent_name=agent_name,
                tool_name=tool_name,
                tool_input=raw_args,
            )
        except Exception:
            logger.exception("hitl: failed to create approval request for %s", tool_name)
            # 审批记录写入失败时必须拒绝执行。
            # 取消订单、退款、退货、修改订单和预订等敏感操作，
            # 不能因为数据库无法保存审批记录，
            # 就绕过审批继续调用。
            # 调用方应得到明确拒绝，
            # 临时数据库故障不等于用户授权。
            context.result = {
                "status": "error",
                "message": (
                    f"Could not submit your {tool_name.replace('_', ' ')} request for approval "
                    "right now — please try again in a moment. No changes have been made."
                ),
            }
            return

        logger.info(
            "hitl.pending tool=%s user=%s request_id=%s",
            tool_name,
            user_email,
            request_id,
        )

        label = tool_name.replace("_", " ")
        context.result = {
            "status": "pending_approval",
            "message": (
                f"Your request to {label} has been submitted for manager approval "
                f"(ref: {str(request_id)[:8]}). "
                "You will be notified once an admin reviews it. "
                "No changes have been made yet."
            ),
            "request_id": str(request_id),
        }
        # 不调用 call_next()，工具尚未执行。


def _decode_jsonb(value: Any) -> dict | None:
    """显式解码 asyncpg 返回的 JSONB 文本，同时兼容已注册编解码器的连接池。

    不能用 dict(value) 处理字符串，否则会按字符迭代并抛出难以理解的
    ValueError；字符串先 JSON 解码，字典保持原样。
    """
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value) if value else None
    return dict(value)


def _extract_agent_name(context: Any) -> str:
    """尽可能从 MAF 上下文提取当前智能体名称。"""
    for attr in ("agent_context", "agent", "_agent"):
        obj = getattr(context, attr, None)
        if obj is None:
            continue
        name = getattr(obj, "name", None)
        if name:
            return str(name)
    return "unknown"


# ─────────────────────── DB helpers ─────────────────────────────────────────


async def _create_hitl_request(
    user_email: str,
    session_id: str | None,
    agent_name: str,
    tool_name: str,
    tool_input: dict,
) -> UUID:
    if tool_name == "initiate_return":
        from shared.after_sales.service import request_return

        identity_token = current_user_email.set(user_email)
        try:
            result = await request_return(**tool_input, force_approval=True)
        finally:
            current_user_email.reset(identity_token)
        if result.get("status") != "pending_approval":
            raise ValueError("Return is not awaiting approval")
        return UUID(result["request_id"])
    from shared.db import get_pool

    pool = get_pool()
    row = await pool.fetchrow(
        """INSERT INTO tool_approval_requests
               (user_email, session_id, agent_name, tool_name, tool_input)
           VALUES ($1, $2, $3, $4, $5::jsonb)
           RETURNING id""",
        user_email,
        session_id,
        agent_name,
        tool_name,
        json.dumps(tool_input),
    )
    return row["id"]


async def list_hitl_requests(
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """读取管理员审批队列中的 HITL 请求。"""
    from shared.db import get_pool

    pool = get_pool()

    where = "WHERE status = $1" if status else ""
    args: list = [status] if status else []
    args += [min(limit, 200)]

    rows = await pool.fetch(
        f"""SELECT id, user_email, agent_name, tool_name, tool_input,
                   status, admin_note, approved_by, execution_result,
                   created_at, resolved_at
            FROM tool_approval_requests
            {where}
            ORDER BY created_at DESC
            LIMIT ${len(args)}""",
        *args,
    )
    return [
        {
            "id": str(r["id"]),
            "user_email": r["user_email"],
            "agent_name": r["agent_name"],
            "tool_name": r["tool_name"],
            "tool_input": _decode_jsonb(r["tool_input"]) or {},
            "status": r["status"],
            "admin_note": r["admin_note"],
            "approved_by": r["approved_by"],
            "execution_result": _decode_jsonb(r["execution_result"]),
            "created_at": r["created_at"].isoformat(),
            "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
        }
        for r in rows
    ]


async def get_hitl_request(request_id: str) -> dict | None:
    from shared.db import get_pool

    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM tool_approval_requests WHERE id = $1", request_id)
    if not row:
        return None
    return {
        "id": str(row["id"]),
        "user_email": row["user_email"],
        "agent_name": row["agent_name"],
        "tool_name": row["tool_name"],
        "tool_input": _decode_jsonb(row["tool_input"]) or {},
        "status": row["status"],
    }


async def claim_hitl_request(request_id: str) -> dict | None:
    """原子地把 pending 请求认领为 processing，并返回完整记录。

    必须先认领再执行，避免两个并发审批都通过检查后重复产生副作用。
    记录不存在或已被处理时返回 None；调用方应拒绝继续执行，不重试认领。
    """
    from shared.db import get_pool

    pool = get_pool()
    row = await pool.fetchrow(
        """UPDATE tool_approval_requests
           SET status = 'processing'
           WHERE id = $1 AND status = 'pending'
           RETURNING id, user_email, agent_name, tool_name, tool_input""",
        request_id,
    )
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "user_email": row["user_email"],
        "agent_name": row["agent_name"],
        "tool_name": row["tool_name"],
        "tool_input": _decode_jsonb(row["tool_input"]) or {},
    }


async def resolve_hitl_request(
    request_id: str,
    decision: str,  # 审批决定：approved 或 denied。
    admin_email: str,
    note: str | None = None,
    execution_result: dict | None = None,
) -> bool:
    """更新审批状态；实际更新记录时返回 True。

    允许结束 pending（拒绝路径）或 processing（批准路径）状态；
    已 approved、denied 或 executed 的记录保持不变并返回 False。
    """
    from shared.db import get_pool

    pool = get_pool()
    final_status = decision
    if decision == "denied":
        from shared.after_sales.operations import deny_tool_approval

        resolved = await deny_tool_approval(request_id, admin_email, note)
        if resolved is not None:
            return resolved
    if decision == "approved" and execution_result:
        failed = execution_result.get("success") is False or "error" in execution_result
        # 保留管理员界面使用的审批状态词汇。
        # 审批决定可以是 approved，但 execution_result 仍可能因业务校验拒绝。
        final_status = "approved" if failed else "executed"
    result = await pool.execute(
        """UPDATE tool_approval_requests
           SET status = $1, admin_note = $2, approved_by = $3,
               execution_result = $4::jsonb, resolved_at = NOW()
           WHERE id = $5 AND (status = 'pending' OR (status = 'processing' AND $6 = 'approved'))""",
        final_status,
        note,
        admin_email,
        json.dumps(execution_result) if execution_result else None,
        request_id,
        decision,
    )
    return result.endswith("1")  # UPDATE 1 表示更新成功。


# ─────────────────────── Action executor ────────────────────────────────────


async def execute_approved_action(
    tool_name: str,
    tool_input: dict,
    user_email: str,
    approval_id: str | None = None,
) -> dict:
    """退货使用绑定政策版本的审批路径；其他工具保留原缓存键。"""
    if tool_name == "initiate_return":
        return await _execute_approved_return(tool_name, tool_input, user_email, approval_id)
    return await _execute_legacy_action(tool_name, tool_input, user_email)


async def _execute_approved_return(
    tool_name: str,
    tool_input: dict,
    user_email: str,
    approval_id: str | None,
) -> dict:
    """只有已认领的服务端审批才能授权共享退货服务执行。"""
    from shared.db import get_pool

    pool = get_pool()
    from shared.after_sales.approval import ReturnApproval, current_return_approval
    from shared.after_sales.service import failure, request_return

    if approval_id is None:
        return failure("APPROVAL_REQUIRED", "A claimed approval record is required.")
    row = await pool.fetchrow(
        "SELECT user_email, tool_name, tool_input, status FROM tool_approval_requests WHERE id = $1",
        approval_id,
    )
    if (
        row is None
        or row["status"] not in {"processing", "executed", "approved"}
        or row["user_email"] != user_email
        or row["tool_name"] != tool_name
        or _decode_jsonb(row["tool_input"]) != tool_input
    ):
        return failure("APPROVAL_INVALID", "Approval does not authorize this return request.")
    params = dict(tool_input)
    from shared.after_sales.operations import current_operation_id

    try:
        approval = ReturnApproval.from_dict(params.pop("_return_approval"))
        operation_id = str(UUID(params.pop("_operation_id")))
    except (KeyError, TypeError, ValueError):
        return failure("APPROVAL_STALE", "This approval predates the return policy. Request fresh approval.")
    identity_token = current_user_email.set(user_email)
    approval_token = current_return_approval.set(approval)
    operation_token = current_operation_id.set(operation_id)
    try:
        return await request_return(**params)
    finally:
        current_return_approval.reset(approval_token)
        current_operation_id.reset(operation_token)
        current_user_email.reset(identity_token)


@idempotent("hitl_execute", identity_fn=lambda tool_name, tool_input, user_email: user_email)
async def _execute_legacy_action(tool_name: str, tool_input: dict, user_email: str) -> dict:
    """Existing non-return actions keep their original idempotency protocol."""
    from shared.db import get_pool

    pool = get_pool()

    if tool_name == "cancel_order":
        order_id = tool_input.get("order_id", "")
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE orders SET status = 'cancelled', updated_at = NOW()
                   WHERE id = $1
                     AND user_id = (SELECT id FROM users WHERE email = $2)
                     AND status IN ('placed', 'confirmed')
                   RETURNING id, status, total""",
                order_id,
                user_email,
            )
        if row:
            return {
                "success": True,
                "order_id": order_id,
                "new_status": "cancelled",
                "message": f"Order {order_id[:8]} cancelled. Refund of ${float(row['total']):.2f} initiated.",
            }
        return {"success": False, "message": "Order not found or already processed."}

    if tool_name == "process_refund":
        # process_refund 接受 return_id，
        # 而非 order_id。中间件记录真实工具参数，
        # 因此 tool_input 在这里应该包含
        # return_id，不能读取不存在的 order_id。
        # 旧实现读取空订单标识，
        # 且错误更新 orders 而非 returns，
        # 导致审批后退款状态没有变化。
        # 当前按 return_id 操作 returns，
        # 并用状态条件防止重复执行。
        # 即使不同审批请求绕过同一幂等键，
        # 重复执行也应返回已退款，
        # 不能再次报告新退款成功。
        return_id = tool_input.get("return_id", "")
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE returns SET status = 'refunded', resolved_at = NOW()
                   WHERE id = $1
                     AND user_id = (SELECT id FROM users WHERE email = $2)
                     AND status NOT IN ('refunded', 'denied')
                   RETURNING id, order_id, refund_method, refund_amount""",
                return_id,
                user_email,
            )
        if row:
            amount = float(row["refund_amount"]) if row["refund_amount"] else 0.0
            method = row["refund_method"] or "original_payment"
            return {
                "success": True,
                "return_id": return_id,
                "order_id": str(row["order_id"]),
                "refunded_amount": amount,
                "message": f"Refund of ${amount:.2f} processed via {method.replace('_', ' ')}.",
            }
        return {"success": False, "message": "Return not found, already resolved, or access denied."}

    if tool_name == "modify_order":
        order_id = tool_input.get("order_id", "")
        new_address = tool_input.get("new_address", {})
        import json as _json

        async with pool.acquire() as conn:
            result = await conn.execute(
                """UPDATE orders SET shipping_address = $3::jsonb, updated_at = NOW()
                   WHERE id = $1
                     AND user_id = (SELECT id FROM users WHERE email = $2)
                     AND status NOT IN ('shipped', 'delivered', 'cancelled')""",
                order_id,
                user_email,
                _json.dumps(new_address),
            )
        if result.endswith("1"):
            return {"success": True, "order_id": order_id, "message": "Shipping address updated."}
        return {"success": False, "message": "Order not found or already shipped."}

    if tool_name == "place_backorder":
        return {
            "success": True,
            "message": f"Backorder approved for product {tool_input.get('product_id', 'unknown')[:8]}.",
        }

    return {
        "success": False,
        "message": f"Auto-execution not configured for tool: {tool_name}",
    }
