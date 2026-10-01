"""商品工具共用的全文检索和混合检索辅助函数。

products.search_vector 按名称、品牌、描述赋予 A/B/C 权重并使用 GIN
索引。MCP 商品包为保持可独立安装，维护自己的 SQL 片段副本。
"""

from __future__ import annotations

import math

# 倒数排名融合（RRF）的平滑常量取 60。
# 该值是本项目采用的既定设置，
# 让靠前名次之间的分数差异较小，
# 从而提升同时被两路检索命中的候选。
RRF_K = 60


def or_joined_tsquery(param: str) -> str:
    """将文本参数构造成以 OR 连接的 tsquery SQL 表达式。

    plainto_tsquery 默认要求全部词元匹配；改用 | 让部分匹配也能召回，
    再由 ts_rank 排序。param 是 $1 一类位置参数占位符，不插入用户文本。
    """
    return f"replace(plainto_tsquery('english', {param})::text, '&', '|')::tsquery"


def expand_catalog_query(query: str) -> str:
    """保留原词并补充受控品类别名，不让改写覆盖用户原始约束。"""
    aliases = {
        "耳机": "headphones",
        "键盘": "keyboard",
        "充电宝": "power bank",
        "手环": "fitness tracker",
        "空气炸锅": "air fryer",
        "运动鞋": "shoes",
    }
    additions = [value for key, value in aliases.items() if key in query and value not in query.lower()]
    return " ".join([query, *additions])


def catalog_filters(
    *,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_rating: float | None = None,
    parameter_offset: int = 0,
) -> tuple[list[str], list]:
    """词法与向量召回共用硬过滤；参数化数值，拒绝非有限或矛盾边界。"""
    for name, value in (("min_price", min_price), ("max_price", max_price), ("min_rating", min_rating)):
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError(f"{name} 必须是有限非负数")
    if min_price is not None and max_price is not None and min_price > max_price:
        raise ValueError("最低价格不能高于最高价格")
    if min_rating is not None and min_rating > 5:
        raise ValueError("最低评分必须在0到5之间")
    category = {"电子产品": "Electronics", "服装": "Clothing", "家居": "Home", "运动": "Sports", "图书": "Books"}.get(
        category, category
    )
    conditions = ["p.is_active = TRUE"]
    values = []
    for column, operator, value in (
        ("category", "=", category),
        ("price", ">=", min_price),
        ("price", "<=", max_price),
        ("rating", ">=", min_rating),
    ):
        if value is not None and value != "":
            values.append(value)
            conditions.append(f"p.{column} {operator} ${parameter_offset + len(values)}")
    return conditions, values
