"""定义哪些工具输出包含需要净化的不可信文本。

仅处理含用户生成或存储文本的工具，避免破坏数值和结构化结果。
键对应 @tool 注册名；值是任意嵌套深度需要净化的字段名集合，
None 表示净化该工具结果中的所有字符串。
"""

from __future__ import annotations

# 工具名映射到待净化字段集合；None 表示全部字符串。
SANITIZE_TOOLS: dict[str, set[str] | None] = {
    # 评论正文和标题是存储型注入的主要来源。
    "get_product_reviews": {"title", "body", "review", "comment", "reviewer", "reviewer_name"},
    "analyze_sentiment": {"title", "body", "summary", "quote", "quotes", "theme", "themes", "comment"},
    "compare_reviews": {"title", "body", "summary", "comment"},
    "detect_fake_reviews": {"title", "body", "comment", "reviewer", "reviewer_name"},
    "get_review_trends": {"title", "body", "summary"},
    # 商品描述和规格可能由商家编辑。
    "search_products": {"name", "description", "specs", "features"},
    "get_product_details": {"name", "description", "specs", "features"},
    "find_similar_products": {"name", "description"},
    "semantic_search": {"name", "description"},
    "get_trending_products": {"name", "description"},
    # 订单备注与状态历史可能包含自由文本。
    "get_order_details": {"note", "notes", "reason", "comment", "description"},
    "get_user_orders": {"note", "notes", "reason"},
}
