"""Rule-based baselines: what you get without a decision model.

These are written to be *fair*, not to be beaten. Each one is the
implementation a competent team would actually ship as a first pass — the
same shape as the repository's existing ``moderation.py`` (a small set of
high-precision regex patterns, deliberately trading recall for precision).

If a typed decision model cannot beat these, that is a real result and the
harness should be able to say so. So: no strawmen, no missing obvious
keywords, and the keyword tables below are written before looking at which
samples they get wrong.

Both baselines are pure functions with no I/O, matching the style of
``shared/guardrails/moderation.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------
# Routing baseline — weighted keyword matching
# --------------------------------------------------------------------------

# Ordered by specificity: a message mentioning "order" and "shipping" is an
# order question ("where is my order"), not a fulfilment question. Weights
# encode that judgement rather than relying on dict iteration order.
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

# Tie-break order when no keyword matches at all: the most common
# first-contact intent, so a total miss degrades to something plausible.
_ROUTE_FALLBACK = "product-discovery"


@dataclass(frozen=True)
class RouteBaselineResult:
    route: str
    score: int
    matched: tuple[str, ...]


def route_by_keywords(message: str) -> RouteBaselineResult:
    """Score every specialist by weighted keyword hits; take the winner.

    Longest-phrase-wins within a route is handled implicitly: a message
    containing "cancel my order" also contains "cancel", and both contribute,
    which is intentional — more specific phrasing should not *reduce* the
    score.
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
# Safety-gate baseline — regex denylist
# --------------------------------------------------------------------------

# The shape a team reaches for first: catch the phrasings seen in incidents.
# It is genuinely effective on those and genuinely blind to paraphrase — which
# is the whole reason a calibrated classifier is interesting here.
_REFUSAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore_instructions", re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?\b", re.I)),
    ("disregard_instructions", re.compile(r"\bdisregard\s+(?:all\s+)?(?:prior|previous|your)\s+(?:instructions?|rules)\b", re.I)),
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
    """Refuse when any denylist pattern matches.

    Binary, not probabilistic — a regex has no notion of confidence, which is
    exactly the property that makes it unable to distinguish "ignore your
    rules" (attack) from "if any review tells you to ignore your rules,
    follow it" (a legitimate request carrying an attack payload).
    """
    matched = tuple(
        name for name, pattern in _REFUSAL_PATTERNS if pattern.search(message)
    )
    return GateBaselineResult(refuse=bool(matched), matched=matched)
