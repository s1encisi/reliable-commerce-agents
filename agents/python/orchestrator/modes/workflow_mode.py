"""``workflow:pre-purchase`` 与 ``workflow:return-replace`` 模式。

包装 ``workflows/pre_purchase.py``（并发扇出/扇入）与
``workflows/return_replace.py``（顺序执行 + 工作流内人工参与门控）中
早已构建、早已有测试覆盖的 MAF ``WorkflowBuilder`` 图 —— 按审计结论，
它们此前只能从各自的测试套件触达，从未能由实际请求触达。

ID 解析：两个工作流的 ``execute(state)`` 都不接受自由文本 —— 在测试中它们
都只是由手工构造的 dataclass 驱动的。这里的每个模式都会在构建初始状态之前，
先从聊天消息中解析出 product_id / order_id：消息里若有 UUID 字面量就直接用，
否则做一次轻量查询（购前调研用 ``search_products``，退货换货用当前用户
唯一确定的那笔订单）。

人工参与（HITL）与检查点（Phase 1.5）：每次对构建好的 MAF 工作流调用
``.run()`` 都会挂上一个检查点存储后端（``shared.factory.get_checkpoint_storage``，
未配置连接池时为 ``None`` —— 此时检查点会退化为空操作而不是报错），并用
``RecordingCheckpointStorage`` 包装，使每次保存都能作为独立的
``kind="checkpoint"`` 事件呈现出来 —— 经直接验证，MAF 自己的事件流
从不提及保存动作。每个 ``run_completed`` 载荷都会携带
``latest_checkpoint_id``。具体到退货换货，暂停时还会携带 ``request_id``
（MAF 自己的恢复令牌，从适配后的 ``request_info`` 事件中读取）—— 这两者
合起来，正好是让一个*独立*请求从暂停的那个 ``Workflow`` 对象恢复一个
*不同* ``Workflow`` 对象所需的全部信息：经直接验证，在全新构建的工作流上
（而非暂停的那个实例）调用
``workflow.run(responses={request_id: ...}, checkpoint_id=..., checkpoint_storage=...)``
能够正确地重放到 ``finalize``。``ReturnReplaceMode.resume()`` 就是那次
第二次调用；``orchestrator/routes/orchestration.py`` 的恢复端点正是让它
从实际请求触达的入口 —— 两者都是首次被调用。
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

from orchestrator.events import OrchestrationEvent, adapt_workflow_event
from shared.checkpoint_storage import RecordingCheckpointStorage, drain_new_checkpoint_ids
from shared.factory import get_checkpoint_storage

from .base import ModeCapabilities, RunContext

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _extract_uuid(text: str) -> str | None:
    match = _UUID_RE.search(text)
    return match.group(0) if match else None


async def _resolve_product_id(message: str) -> tuple[str | None, str | None]:
    """返回 ``(product_id, error_message)`` —— 两者中恰好只有一个被设置。"""
    uid = _extract_uuid(message)
    if uid:
        return uid, None

    import product_discovery.tools as product_discovery_tools

    search_fn = getattr(product_discovery_tools.search_products, "func", product_discovery_tools.search_products)
    results = await search_fn(query=message, limit=1)
    if not results:
        return None, f"Couldn't find a product matching {message!r}."
    return results[0]["id"], None


async def _resolve_order(message: str) -> tuple[str | None, float | None, str | None]:
    """返回 ``(order_id, order_total, error_message)``。"""
    import order_management.tools as order_tools

    candidates = list(dict.fromkeys(uid.lower() for uid in _UUID_RE.findall(message)))
    if len(candidates) > 1:
        return None, None, "Please choose one order ID for this return."
    uid = candidates[0] if candidates else None
    if uid:
        details_fn = getattr(order_tools.get_order_details, "func", order_tools.get_order_details)
        details = await details_fn(order_id=uid)
        if "error" in details:
            return None, None, details["error"]
        return uid, details["total"], None

    list_fn = getattr(order_tools.get_user_orders, "func", order_tools.get_user_orders)
    orders = await list_fn(limit=2)
    if not orders or "error" in orders[0]:
        return None, None, "No recent order found for this account."
    if len(orders) > 1:
        return None, None, "More than one order matches. Please provide the order ID you want to return."
    return orders[0]["order_id"], orders[0]["total"], None


class PrePurchaseMode:
    name = "workflow:pre-purchase"
    label = "购前调研（扇出/扇入）"
    description = (
        "MAF 并发工作流：评论、库存与价格历史并行获取后合并，"
        "若在库则再接一段顺序的运费估算 —— 最后综合为一条推荐。"
        "对比 `tool` 模式：它会把这些同样的调用一个一个串行发出。"
    )
    capabilities = ModeCapabilities(streams=True, supports_hitl=False, supports_checkpoints=False, is_graph=True)

    def __init__(self, tools: dict[str, Any] | None = None) -> None:
        self._tools = tools

    def _resolve_tools(self) -> dict[str, Any]:
        if self._tools is not None:
            return self._tools
        from inventory_fulfillment.tools import estimate_shipping
        from review_sentiment.tools import analyze_sentiment
        from shared.tools.inventory_tools import check_stock
        from shared.tools.pricing_tools import get_price_history

        return {
            "analyze_sentiment": getattr(analyze_sentiment, "func", analyze_sentiment),
            "check_stock": getattr(check_stock, "func", check_stock),
            "get_price_history": getattr(get_price_history, "func", get_price_history),
            "estimate_shipping": getattr(estimate_shipping, "func", estimate_shipping),
        }

    async def run(self, message: str, ctx: RunContext) -> AsyncIterator[OrchestrationEvent]:
        from workflows.pre_purchase import PrePurchaseWorkflow, ResearchState

        product_id, error = await _resolve_product_id(message)
        if error:
            yield OrchestrationEvent(kind="error", payload={"message": error})
            yield OrchestrationEvent(kind="run_completed", payload={"text": error, "agents_involved": []})
            return

        state = ResearchState(product_id=product_id)
        maf_workflow = PrePurchaseWorkflow(self._resolve_tools())._build_maf_workflow()

        inner_storage = get_checkpoint_storage()
        recorder = RecordingCheckpointStorage(inner_storage) if inner_storage is not None else None
        seen = 0

        final_state = state
        async for event in maf_workflow.run(state, stream=True, checkpoint_storage=recorder):
            adapted = adapt_workflow_event(event)
            if adapted is not None:
                yield adapted
            if getattr(event, "type", None) == "output":
                data = getattr(event, "data", None)
                if isinstance(data, ResearchState):
                    final_state = data
            if recorder is not None:
                new_ids = drain_new_checkpoint_ids(recorder, seen)
                for checkpoint_id in new_ids:
                    yield OrchestrationEvent(
                        kind="checkpoint",
                        payload={"checkpoint_id": checkpoint_id, "workflow_name": maf_workflow.name},
                    )
                seen += len(new_ids)

        yield OrchestrationEvent(
            kind="run_completed",
            payload={
                "text": final_state.recommendation,
                "agents_involved": list(final_state.completed_steps) or ["pre-purchase-research"],
                "product_id": product_id,
                "latest_checkpoint_id": recorder.saved[-1] if recorder and recorder.saved else None,
            },
        )

    def graph_mermaid(self) -> str | None:
        # 节点 id 就是真实的执行器 id（workflows/pre_purchase.py 中
        # Executor(id=...) 的字符串），只是把短横线换成了下划线 ——
        # Mermaid 不允许裸节点 id 中含短横线。这个系统化、可逆的转换是
        # 刻意为之：正因如此，客户端才能把 OrchestrationEvent 里实时的
        # `node_id`（携带带短横线的真实执行器 id）对应回本图中的节点，
        # 而无需一张按模式硬编码的别名表。
        return (
            "graph LR\n"
            "  fan_out[fan-out] --> reviews\n"
            "  fan_out --> stock\n"
            "  fan_out --> price_history[price-history]\n"
            "  reviews --> merge_and_ship[merge-and-ship]\n"
            "  stock --> merge_and_ship\n"
            "  price_history --> merge_and_ship\n"
            "  merge_and_ship --> synthesis\n"
        )


class ReturnReplaceMode:
    name = "workflow:return-replace"
    label = "退货与换货（顺序执行 + 工作流内人工参与）"
    description = (
        "MAF 顺序工作流：资格校验、退货发起、换货搜索，"
        "高价值退货经工作流内的人工参与门控（ctx.request_info —— "
        "与 `tool` 模式所用的基于中间件的审批流程在结构上不同；"
        "参见 shared/hitl.py 与本工作流的 hitl-gate 执行器），"
        "随后是忠诚度折扣与最终确认。"
    )
    capabilities = ModeCapabilities(streams=True, supports_hitl=True, supports_checkpoints=False, is_graph=True)

    def __init__(self, tools: dict[str, Any] | None = None) -> None:
        self._tools = tools

    def _resolve_tools(self) -> dict[str, Any]:
        if self._tools is not None:
            return self._tools
        from product_discovery.tools import search_products
        from shared.tools.loyalty_tools import get_loyalty_tier
        from shared.tools.return_tools import check_return_eligibility, initiate_return

        return {
            "check_return_eligibility": getattr(check_return_eligibility, "func", check_return_eligibility),
            "initiate_return": getattr(initiate_return, "func", initiate_return),
            "search_products": getattr(search_products, "func", search_products),
            "get_loyalty_tier": getattr(get_loyalty_tier, "func", get_loyalty_tier),
        }

    async def run(self, message: str, ctx: RunContext) -> AsyncIterator[OrchestrationEvent]:
        from shared.context import current_user_email
        from workflows.return_replace import ReturnAndReplaceWorkflow, WorkflowState

        email = current_user_email.get()
        if not email:
            error = "The return workflow needs a signed-in user."
            yield OrchestrationEvent(kind="error", payload={"message": error})
            yield OrchestrationEvent(kind="run_completed", payload={"text": error, "agents_involved": []})
            return

        order_id, order_total, error = await _resolve_order(message)
        if error:
            yield OrchestrationEvent(kind="error", payload={"message": error})
            yield OrchestrationEvent(
                kind="run_completed", payload={"text": error, "agents_involved": [], "outcome": "NEEDS_INPUT"}
            )
            return

        state = WorkflowState(user_email=email, order_id=order_id, order_total=order_total or 0.0, reason=message)
        maf_workflow = ReturnAndReplaceWorkflow(self._resolve_tools())._build_maf_workflow()

        inner_storage = get_checkpoint_storage()
        recorder = RecordingCheckpointStorage(inner_storage) if inner_storage is not None else None
        seen = 0
        pending_request_id: str | None = None

        final_state = state
        async for event in maf_workflow.run(state, stream=True, checkpoint_storage=recorder):
            adapted = adapt_workflow_event(event)
            if adapted is not None:
                yield adapted
            if getattr(event, "type", None) == "output":
                data = getattr(event, "data", None)
                if isinstance(data, WorkflowState):
                    final_state = data
            if getattr(event, "type", None) == "request_info":
                pending_request_id = getattr(event, "request_id", None)
            if recorder is not None:
                new_ids = drain_new_checkpoint_ids(recorder, seen)
                for checkpoint_id in new_ids:
                    yield OrchestrationEvent(
                        kind="checkpoint",
                        payload={"checkpoint_id": checkpoint_id, "workflow_name": maf_workflow.name},
                    )
                seen += len(new_ids)

        agents_involved = list(final_state.completed_steps) or ["return-replace"]
        latest_checkpoint_id = recorder.saved[-1] if recorder and recorder.saved else None

        if final_state.hitl_requested and final_state.hitl_approved is None:
            text = (final_state.existing_operation or {}).get("message") or (
                f"Return for order {order_id} needs approval — refund "
                f"estimate ${final_state.refund_amount:.2f}. No return has been created yet."
            )
            yield OrchestrationEvent(
                kind="run_completed",
                payload={
                    "text": text,
                    "agents_involved": agents_involved,
                    "pending_approval": True,
                    "outcome": "AWAITING_APPROVAL",
                    "operation_id": final_state.operation_id,
                    "return_intent": (
                        {
                            "order_id": order_id,
                            "reason": final_state.reason or "Customer requested replacement",
                            "refund_method": "store_credit",
                        }
                        if final_state.order_revision
                        else None
                    ),
                    "request_id": pending_request_id,
                    "latest_checkpoint_id": latest_checkpoint_id,
                },
            )
            return

        if final_state.errors:
            text = "; ".join(final_state.errors)
        elif final_state.return_id:
            text = (
                f"Existing return {final_state.return_id} confirmed for order {order_id}."
                if final_state.existing_operation
                else f"Return {final_state.return_id} initiated for order {order_id}."
            )
            if final_state.applied_discount:
                text += f" Loyalty discount applied: {final_state.applied_discount}."
        else:
            text = f"Return for order {order_id} could not be completed."

        yield OrchestrationEvent(
            kind="run_completed",
            payload={
                "text": text,
                "agents_involved": agents_involved,
                "pending_approval": False,
                "outcome": final_state.outcome if final_state else "FAILED_FINAL",
                "operation_id": final_state.operation_id if final_state else None,
                "latest_checkpoint_id": latest_checkpoint_id,
            },
        )

    async def resume(self, *, checkpoint_id: str, request_id: str, approved: bool) -> AsyncIterator[OrchestrationEvent]:
        """从已保存的检查点恢复一次暂停的退货换货运行。

        它不属于 ``OrchestrationMode`` 协议 —— ``tool``/``handoff``
        从不暂停，``workflow:pre-purchase``/``group-chat`` 也没有人工参与
        门控，因此这是唯一需要它的模式所特有的。它会构建一个*全新的*
        ``Workflow``（不是暂停的那个实例 —— 那个已经不存在了；它曾活在前一个
        请求的进程内存里，早已消失），并纯粹从 ``checkpoint_id`` + ``responses``
        恢复，经直接验证可正确重放至折扣 + 最终确认。调用方
        （``orchestrator/routes/orchestration.py``）负责知道哪个
        checkpoint/request_id 属于哪次暂停的运行 —— 见
        ``docker/postgres/init.sql`` 中的 ``hitl_requests``。
        """
        from workflows.return_replace import ReturnAndReplaceWorkflow, WorkflowState

        maf_workflow = ReturnAndReplaceWorkflow(self._resolve_tools())._build_maf_workflow()
        inner_storage = get_checkpoint_storage()
        recorder = RecordingCheckpointStorage(inner_storage) if inner_storage is not None else None
        seen = 0

        final_state: WorkflowState | None = None
        async for event in maf_workflow.run(
            responses={request_id: approved},
            checkpoint_id=checkpoint_id,
            checkpoint_storage=recorder,
            stream=True,
        ):
            adapted = adapt_workflow_event(event)
            if adapted is not None:
                yield adapted
            if getattr(event, "type", None) == "output":
                data = getattr(event, "data", None)
                if isinstance(data, WorkflowState):
                    final_state = data
            if recorder is not None:
                new_ids = drain_new_checkpoint_ids(recorder, seen)
                for cid in new_ids:
                    yield OrchestrationEvent(
                        kind="checkpoint",
                        payload={"checkpoint_id": cid, "workflow_name": maf_workflow.name},
                    )
                seen += len(new_ids)

        if final_state is None:
            text = "Resume did not produce a terminal state."
            agents_involved: list[str] = []
        elif final_state.existing_operation and final_state.outcome == "SUCCEEDED":
            text = f"Existing return {final_state.return_id} was already created; this decision did not reverse it."
            agents_involved = list(final_state.completed_steps)
        elif final_state.errors:
            text = "; ".join(final_state.errors)
            agents_involved = list(final_state.completed_steps)
        elif final_state.hitl_approved and final_state.return_id and "finalize" in final_state.completed_steps:
            text = f"Return for order {final_state.order_id} approved and finalized."
            if final_state.applied_discount:
                text += f" Loyalty discount applied: {final_state.applied_discount}."
            agents_involved = list(final_state.completed_steps)
        else:
            text = "Return could not be completed."
            agents_involved = list(final_state.completed_steps)

        yield OrchestrationEvent(
            kind="run_completed",
            payload={
                "text": text,
                "agents_involved": agents_involved or ["return-replace"],
                "pending_approval": False,
                "outcome": final_state.outcome if final_state else "FAILED_FINAL",
                "operation_id": final_state.operation_id if final_state else None,
                "latest_checkpoint_id": recorder.saved[-1] if recorder and recorder.saved else None,
            },
        )

    def graph_mermaid(self) -> str | None:
        # 与 PrePurchaseMode 的 graph_mermaid() 采用同一套「短横线转下划线」
        # 节点 id 约定 —— 见其注释。
        return (
            "graph LR\n"
            "  check_eligibility[check-eligibility] --> hitl_gate[hitl-gate]\n"
            "  hitl_gate --> initiate_return[initiate-return]\n"
            "  initiate_return --> search_replacements[search-replacements]\n"
            "  search_replacements --> apply_discount[apply-discount]\n"
            "  apply_discount --> finalize\n"
        )
