"""工具级角色授权。

先通过 requires_role 检查角色，再进入 MAF 人工审批；订单归属过滤
不能替代角色授权。身份从 current_user_role 读取，不由工具参数传入。
admin 始终允许；GUARDRAILS_ENABLED 控制启用。

requires_role 置于 @tool 下方，通过 wraps 保留签名；ensure_role 可在
既有工具函数开头作为守卫，拒绝时返回结构化结果，否则返回 None。
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from shared.config import settings
from shared.context import current_user_role

logger = logging.getLogger(__name__)

_ALWAYS_ALLOWED = {"admin"}

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def _denial(allowed: set[str], tool: str, role: str) -> dict[str, Any]:
    required = sorted(allowed)
    logger.warning(
        "guardrails.role_denied tool=%s role=%s required=%s",
        tool,
        role or "<none>",
        required,
    )
    return {
        "error": "permission_denied",
        "message": (
            f"You don't have permission to perform this action. It requires one of these roles: {', '.join(required)}."
        ),
        "required_roles": required,
    }


def ensure_role(*roles: str, tool: str = "tool") -> dict[str, Any] | None:
    """当前角色未获授权时返回拒绝对象，否则返回 None。

    工具函数开头可使用：
        denied = ensure_role("seller", "admin", tool="get_my_products")
        if denied:
            return denied
    """
    if not settings.GUARDRAILS_ENABLED:
        return None
    allowed = {r.lower() for r in roles} | _ALWAYS_ALLOWED
    role = (current_user_role.get() or "").lower()
    if role in allowed:
        return None
    return _denial(allowed, tool, role)


def requires_role(*roles: str) -> Callable[[F], F]:
    """角色未获授权时返回结构化拒绝对象的装饰器。

    放在 @tool 下方，functools.wraps 保留原始签名和注解：
        @tool(name="get_my_products", description="...")
        @requires_role("seller", "admin")
        async def get_my_products(...): ...
    """
    allowed = {r.lower() for r in roles} | _ALWAYS_ALLOWED

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if settings.GUARDRAILS_ENABLED:
                role = (current_user_role.get() or "").lower()
                if role not in allowed:
                    return _denial(allowed, getattr(fn, "__name__", "tool"), role)
            return await fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
