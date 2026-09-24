"""意图分类类型与智能体路由映射。

实际的意图分类由 LLM 通过系统提示词与工具选择完成。
这些类型的存在是为了结构化日志与分析。
"""

from __future__ import annotations

from enum import StrEnum


class Intent(StrEnum):
    """用于分析与路由的用户意图分类。"""

    PRODUCT_QUESTION = "product_question"
    ORDER_INQUIRY = "order_inquiry"
    RETURN_REQUEST = "return_request"
    PRICING_QUESTION = "pricing_question"
    REVIEW_QUESTION = "review_question"
    SHIPPING_QUESTION = "shipping_question"
    COMPLAINT = "complaint"
    GENERAL_FAQ = "general_faq"


INTENT_TO_AGENT: dict[Intent, str] = {
    Intent.PRODUCT_QUESTION: "product-discovery",
    Intent.ORDER_INQUIRY: "order-management",
    Intent.RETURN_REQUEST: "order-management",
    Intent.PRICING_QUESTION: "pricing-promotions",
    Intent.REVIEW_QUESTION: "review-sentiment",
    Intent.SHIPPING_QUESTION: "inventory-fulfillment",
    Intent.COMPLAINT: "orchestrator",
    Intent.GENERAL_FAQ: "orchestrator",
}
