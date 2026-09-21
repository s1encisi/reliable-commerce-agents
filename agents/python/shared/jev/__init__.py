"""Jev (TypeSafe System One) typed-decision integration.

Jev returns typed, calibrated decisions — ``choice``, ``score`` and ``noul`` —
rather than prose, which lets the caller branch on the value directly instead
of parsing it.

    from shared.jev import JevClient, route_specialist, safety_gate

    client = JevClient()                     # reads $TYPESAFE_API_KEY
    decision = route_specialist(client, "where is my order?")
    if decision.route == "order-management":
        ...

See ``README.md`` in this package for the decision-point map, the measured
comparison against rule-based baselines, and the list of things this does
*not* claim.
"""

from .client import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    ENV_API_KEY,
    JevAuthError,
    JevClient,
    JevError,
    JevRateLimitError,
    JevResponse,
    JevUnavailableError,
    choice,
    noul,
    score,
)
from .decisions import (
    DEFAULT_GATE_THRESHOLD,
    RELEVANCE_LEVELS,
    ROUTE_INSTRUCTIONS,
    SPECIALIST_ROUTES,
    GateDecision,
    RouteDecision,
    route_specialist,
    safety_gate,
    score_relevance,
)

__all__ = [
    # transport
    "JevClient",
    "JevResponse",
    "JevError",
    "JevAuthError",
    "JevRateLimitError",
    "JevUnavailableError",
    "DEFAULT_ENDPOINT",
    "DEFAULT_MODEL",
    "ENV_API_KEY",
    # question builders
    "choice",
    "score",
    "noul",
    # decisions
    "route_specialist",
    "safety_gate",
    "score_relevance",
    "RouteDecision",
    "GateDecision",
    "SPECIALIST_ROUTES",
    "ROUTE_INSTRUCTIONS",
    "RELEVANCE_LEVELS",
    "DEFAULT_GATE_THRESHOLD",
]
