"""商品工具共用的全文检索和混合检索辅助函数。

products.search_vector 按名称、品牌、描述赋予 A/B/C 权重并使用 GIN
索引。MCP 商品包为保持可独立安装，维护自己的 SQL 片段副本。
"""

from __future__ import annotations

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
