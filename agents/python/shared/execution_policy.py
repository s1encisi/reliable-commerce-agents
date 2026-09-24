"""由已认证服务设置的只读执行范围；模型不能通过工具参数扩大权限。"""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from agent_framework._middleware import FunctionInvocationContext, FunctionMiddleware

current_execution_policy: ContextVar[str] = ContextVar("execution_policy", default="normal")
current_policy_denials: ContextVar[list[str] | None] = ContextVar("policy_denials", default=None)
READ_ONLY_TOOLS = frozenset(
    {
        "analyze_sentiment",
        "calculate_fulfillment_plan",
        "calculate_loyalty_discount",
        "check_bundle_eligibility",
        "check_return_eligibility",
        "check_stock",
        "compare_carriers",
        "compare_product_reviews",
        "compare_products",
        "detect_fake_reviews",
        "estimate_shipping",
        "find_product_by_name",
        "find_similar_products",
        "get_active_deals",
        "get_cart",
        "get_loyalty_benefits",
        "get_loyalty_tier",
        "get_my_products",
        "get_order_details",
        "get_order_tracking",
        "get_price_history",
        "get_product_details",
        "get_product_reviews",
        "get_purchase_history",
        "get_restock_schedule",
        "get_return_operation_status",
        "get_return_status",
        "get_seller_inventory",
        "get_seller_orders",
        "get_seller_stats",
        "get_sentiment_by_topic",
        "get_sentiment_trend",
        "get_tracking_status",
        "get_trending_products",
        "get_user_orders",
        "get_user_profile",
        "get_warehouse_availability",
        "optimize_cart",
        "recall_memories",
        "search_products",
        "search_reviews",
        "semantic_search",
        "validate_coupon",
    }
)


class ReadOnlyToolMiddleware(FunctionMiddleware):
    """默认拒绝未列入只读清单的工具，未知工具不能绕过。"""

    async def process(self, context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]]) -> None:
        if current_execution_policy.get() == "read_only" and context.function.name not in READ_ONLY_TOOLS:
            denials = current_policy_denials.get()
            if denials is not None:
                denials.append(context.function.name)
            context.result = {
                "error": "当前执行分支仅允许查询，请返回原业务流程处理写操作。",
                "error_code": "READ_ONLY_POLICY",
                "outcome": "REJECTED",
            }
            return
        await call_next()
