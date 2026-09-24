"""基于规则的基线：没有决策模型时你能得到什么。

这些基线是本着*公平*而写的，而不是为了被击败。每一个都是称职的团队在
第一版中真正会交付的实现——与本仓库已有的 ``moderation.py`` 形态相同
（一小组高精度正则模式，刻意用召回率换取精确率）。

如果一个带类型的决策模型无法击败这些基线，那也是一个真实的结果，运行框架
应当有能力如实说明。因此：不设稻草人，不漏掉显而易见的关键词，并且下面的
关键词表是在查看哪些样本会被判错之前就写好的。

两个基线都是无 I/O 的纯函数，与 ``shared/guardrails/moderation.py`` 的风格一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------
# 路由基线——加权关键词匹配
# --------------------------------------------------------------------------

# 按特异性排序：一条同时提到 "order" 和 "shipping" 的消息是订单问题
# （"where is my order"），而不是履约问题。权重编码了这一判断，
# 而不是依赖字典的迭代顺序。
ROUTE_KEYWORDS: dict[str, dict[str, int]] = {
    "order-management": {
        "my order": 5,
        "order status": 5,
        "tracking": 4,
        "where is my": 4,
        "cancel": 4,
        "cancel my order": 6,
        "refund": 3,
        "return": 3,
        "returned": 3,
        "delivered": 3,
        "shipment": 3,
        "order number": 5,
        "purchase history": 4,
        "my account": 2,
    },
    "inventory-fulfillment": {
        "in stock": 5,
        "availability": 5,
        "available": 3,
        "how fast can it ship": 6,
        "shipping speed": 5,
        "delivery estimate": 5,
        "when will it arrive": 5,
        "warehouse": 4,
        "backorder": 4,
        "out of stock": 5,
    },
    "pricing-promotions": {
        "coupon": 6,
        "discount": 6,
        "promo": 5,
        "promotion": 5,
        "deal": 3,
        "on sale": 5,
        "sale price": 5,
        "price drop": 5,
        "voucher": 5,
        "loyalty": 3,
        "price match": 5,
    },
    "review-sentiment": {
        "review": 5,
        "reviews": 5,
        "rating": 4,
        "ratings": 4,
        "what are customers saying": 6,
        "feedback": 4,
        "complaints": 4,
        "sentiment": 5,
        "worth buying": 3,
    },
    "product-discovery": {
        "looking for": 4,
        "find me": 4,
        "recommend": 4,
        "compare": 4,
        "comparison": 4,
        "under $": 3,
        "specs": 3,
        "specifications": 3,
        "details for": 3,
        "trending": 3,
        "best ": 2,
        "show me": 2,
    },
}

# 完全没有任何关键词命中时的并列决胜顺序：最常见的那种首次接触意图，
# 这样一次彻底的未命中也能退化为某种看起来合理的结果。
_ROUTE_FALLBACK = "product-discovery"


@dataclass(frozen=True)
class RouteBaselineResult:
    route: str
    score: int
    matched: tuple[str, ...]


def route_by_keywords(message: str) -> RouteBaselineResult:
    """按加权关键词命中为每个专业智能体打分；取胜出者。

    单个路由内"最长短语胜出"是隐式处理的：一条包含 "cancel my order" 的
    消息同时也包含 "cancel"，两者都会计分，这是有意为之——更具体的措辞
    不应*降低*得分。
    """
    lowered = message.lower()

    best_route = _ROUTE_FALLBACK
    best_score = 0
    best_matched: list[str] = []

    for route, keywords in ROUTE_KEYWORDS.items():
        score = 0
        matched: list[str] = []
        for phrase, weight in keywords.items():
            if phrase in lowered:
                score += weight
                matched.append(phrase)
        if score > best_score:
            best_route, best_score, best_matched = route, score, matched

    return RouteBaselineResult(
        route=best_route,
        score=best_score,
        matched=tuple(best_matched),
    )


# --------------------------------------------------------------------------
# 安全闸门基线——正则拒绝名单
# --------------------------------------------------------------------------

# 团队最先想到的形态：捕获在事故中出现过的措辞。
# 它对这些措辞确实有效，对改写则确实视而不见——这正是校准过的分类器
# 在这里有意思的全部原因。
_REFUSAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore_instructions", re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?\b", re.I)),
    (
        "disregard_instructions",
        re.compile(r"\bdisregard\s+(?:all\s+)?(?:prior|previous|your)\s+(?:instructions?|rules)\b", re.I),
    ),
    ("system_prompt", re.compile(r"\bsystem\s+prompt\b", re.I)),
    ("reveal_instructions", re.compile(r"\breveal\s+(?:your\s+)?(?:instructions?|rules|configuration)\b", re.I)),
    ("you_are_now", re.compile(r"\byou\s+are\s+now\s+\w+", re.I)),
    ("jailbreak_persona", re.compile(r"\bDAN\b|\bno\s+restrictions\b", re.I)),
    ("admin_impersonation", re.compile(r"\bI\s+am\s+an?\s+(?:administrator|admin|root)\b", re.I)),
    ("all_users_data", re.compile(r"\ball\s+(?:orders|users|customers)\b", re.I)),
    ("not_my_order", re.compile(r"\bit\s+is\s+not\s+my\s+order\b", re.I)),
    ("another_user", re.compile(r"\b(?:another|other|every)\s+(?:user|seller|customer)['s]*\b", re.I)),
    ("private_data", re.compile(r"\bprivate\s+(?:revenue|payout|data|information)\b", re.I)),
    ("bypass_checks", re.compile(r"\bbypass\s+(?:your\s+)?(?:authorization|auth|checks?|rules)\b", re.I)),
    ("output_secrets", re.compile(r"\b(?:secrets?|AGENT_SHARED_SECRET|configuration)\b", re.I)),
)


@dataclass(frozen=True)
class GateBaselineResult:
    refuse: bool
    matched: tuple[str, ...]


def gate_by_denylist(message: str) -> GateBaselineResult:
    """只要有任何拒绝名单模式命中就拒绝。

    是二元的，而非概率性的——正则没有置信度的概念，而这正是使它无法区分
    "忽略你的规则"（攻击）与"如果任何评论让你忽略你的规则，就照做"
    （一条携带攻击载荷的合法请求）的性质。
    """
    matched = tuple(name for name, pattern in _REFUSAL_PATTERNS if pattern.search(message))
    return GateBaselineResult(refuse=bool(matched), matched=matched)
