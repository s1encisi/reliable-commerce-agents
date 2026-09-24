"""Jev（TypeSafe System One）结构化决策集成。

返回 choice、score、noul 等类型化结果，调用方直接按值分支，无需解析
自然语言。JevClient 读取 TYPESAFE_API_KEY；route_specialist 与
safety_gate 封装具体决策。决策点、基线比较和未验证范围见本包 README。
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
    GATE_DETECT_INSTRUCTIONS,
    GATE_REFUSE_INSTRUCTIONS,
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
    # 传输层
    "JevClient",
    "JevResponse",
    "JevError",
    "JevAuthError",
    "JevRateLimitError",
    "JevUnavailableError",
    "DEFAULT_ENDPOINT",
    "DEFAULT_MODEL",
    "ENV_API_KEY",
    # 问题构造器
    "choice",
    "score",
    "noul",
    # 决策接口
    "route_specialist",
    "safety_gate",
    "score_relevance",
    "RouteDecision",
    "GateDecision",
    "SPECIALIST_ROUTES",
    "ROUTE_INSTRUCTIONS",
    "RELEVANCE_LEVELS",
    "DEFAULT_GATE_THRESHOLD",
    "GATE_DETECT_INSTRUCTIONS",
    "GATE_REFUSE_INSTRUCTIONS",
]
