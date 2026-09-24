"""向智能体运行注入电商上下文的 MAF 提供器。

三个可组合提供器：
- UserProfileProvider：查询用户，写入 state["user"]，并追加用户说明。
- RecentOrdersProvider：附加最近 5 笔订单及对应指令段。
- AgentMemoriesProvider：读取有效长期记忆，写入 state["memories"]。

ECommerceContextProvider 按既定顺序组合三者，并继续生成兼容用的
state["user_context"]。只需要部分能力的专业智能体可单独挂载提供器。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent_framework import ContextProvider

from shared.context import current_user_email
from shared.db import get_pool

# ──────────────────────── Fine-grained providers ────────────────────────


class UserProfileProvider(ContextProvider):
    """注入当前用户资料。

    state["user"] 包含 name、email、role、loyalty_tier、total_spend，
    同时通过 extend_instructions 向 MAF 追加简短用户说明。
    """

    def __init__(self) -> None:
        super().__init__(source_id="user-profile")

    async def before_run(self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]) -> None:
        email = current_user_email.get()
        if not email or email == "system":
            return

        try:
            pool = get_pool()
        except RuntimeError:
            return

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT name, role, loyalty_tier, total_spend FROM users WHERE email = $1",
                email,
            )
        if not row:
            return

        profile = {
            "name": row["name"],
            "email": email,
            "role": row["role"],
            "loyalty_tier": row["loyalty_tier"],
            "total_spend": float(row["total_spend"]),
        }
        state["user"] = profile

        # 通过 MAF 原生接口追加简短系统指令。
        if hasattr(context, "extend_instructions"):
            context.extend_instructions(
                "user-profile",
                (
                    f"Current user: {profile['name']} ({profile['email']}). "
                    f"Role: {profile['role']}, Loyalty tier: {profile['loyalty_tier']}, "
                    f"Total spend: ${profile['total_spend']:.2f}."
                ),
            )

    async def after_run(self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]) -> None:
        pass


class RecentOrdersProvider(ContextProvider):
    """注入最近 N 笔订单，默认最多 5 笔。

    需要同一链中先运行 UserProfileProvider，或已设置
    state["user"]["email"]。limit 控制最大条数。
    """

    def __init__(self, *, limit: int = 5) -> None:
        super().__init__(source_id="recent-orders")
        self._limit = limit

    async def before_run(self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]) -> None:
        email = state.get("user", {}).get("email") or current_user_email.get()
        if not email or email == "system":
            return

        try:
            pool = get_pool()
        except RuntimeError:
            return

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT o.id, o.status, o.total, o.created_at
                   FROM orders o
                   JOIN users u ON o.user_id = u.id
                   WHERE u.email = $1
                   ORDER BY o.created_at DESC
                   LIMIT $2""",
                email,
                self._limit,
            )
        if not rows:
            return

        orders = [
            {
                "id": str(row["id"]),
                "status": row["status"],
                "total": float(row["total"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        state["recent_orders"] = orders

        if hasattr(context, "extend_instructions"):
            lines = [f"Recent orders ({len(orders)}):"]
            for order in orders:
                date = order["created_at"].strftime("%Y-%m-%d")
                lines.append(f"  - order_id={order['id']} | {order['status']} | ${order['total']:.2f} | {date}")
            context.extend_instructions("recent-orders", "\n".join(lines))

    async def after_run(self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]) -> None:
        pass


class AgentMemoriesProvider(ContextProvider):
    """注入当前用户的有效长期记忆；limit 默认为 10。"""

    def __init__(self, *, limit: int = 10) -> None:
        super().__init__(source_id="agent-memories")
        self._limit = limit

    async def before_run(self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]) -> None:
        email = state.get("user", {}).get("email") or current_user_email.get()
        if not email or email == "system":
            return

        try:
            pool = get_pool()
        except RuntimeError:
            return

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT category, content, importance
                   FROM agent_memories m
                   JOIN users u ON m.user_id = u.id
                   WHERE u.email = $1 AND m.is_active = TRUE AND m.confirmed_at IS NOT NULL
                     AND (m.expires_at IS NULL OR m.expires_at > NOW())
                   ORDER BY m.importance DESC, m.created_at DESC
                   LIMIT $2""",
                email,
                self._limit,
            )
        if not rows:
            return

        memories = [
            {
                "category": row["category"],
                "content": row["content"],
                "importance": row["importance"],
            }
            for row in rows
        ]
        state["memories"] = memories

        if hasattr(context, "extend_instructions"):
            lines = ["## User Preferences & History"]
            for memory in memories:
                lines.append(f"  - [{memory['category']}] {memory['content']} (importance: {memory['importance']})")
            context.extend_instructions("agent-memories", "\n".join(lines))

    async def after_run(self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]) -> None:
        pass


# ──────────────────────── Back-compat composite ────────────────────────


class ECommerceContextProvider(ContextProvider):
    """兼容现有调用方的组合提供器。

    依次执行三个细粒度提供器，再组装兼容字段 user_context。
    编排器可使用完整组合；只需要部分信息的专业智能体宜单独选择提供器。
    """

    def __init__(self, *, providers: Sequence[ContextProvider] | None = None) -> None:
        super().__init__(source_id="ecommerce-context")
        self._providers: Sequence[ContextProvider] = (
            providers
            if providers is not None
            else (UserProfileProvider(), RecentOrdersProvider(), AgentMemoriesProvider())
        )

    async def before_run(self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]) -> None:
        for provider in self._providers:
            await provider.before_run(agent=agent, session=session, context=context, state=state)

        # 重新组装兼容用的 user_context 字符串。
        lines: list[str] = []
        user = state.get("user")
        if user:
            lines.append(f"Current user: {user['name']} ({user['email']})")
            lines.append(
                f"Role: {user['role']}, Loyalty tier: {user['loyalty_tier']}, Total spend: ${user['total_spend']:.2f}"
            )

        orders = state.get("recent_orders")
        if orders:
            lines.append(f"Recent orders ({len(orders)}):")
            for order in orders:
                date = order["created_at"].strftime("%Y-%m-%d")
                lines.append(f"  - order_id={order['id']} | {order['status']} | ${order['total']:.2f} | {date}")

        memories = state.get("memories")
        if memories:
            lines.append("")
            lines.append("## User Preferences & History")
            for memory in memories:
                lines.append(f"  - [{memory['category']}] {memory['content']} (importance: {memory['importance']})")

        if lines:
            state["user_context"] = "\n".join(lines)

    async def after_run(self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]) -> None:
        for provider in self._providers:
            await provider.after_run(agent=agent, session=session, context=context, state=state)
