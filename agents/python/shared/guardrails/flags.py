"""请求级护栏触发标记。

MAF 每次聊天调用新建空的 ChatContext.metadata，不从 AgentContext
继承；在那里设置的标记无法被调用外读取。使用 ContextVar 保存可观察
副作用，运行开始时重置、结束后读取。安全评测据此断言实际护栏行为，
不只依赖回复措辞。
"""

from __future__ import annotations

from contextvars import ContextVar

current_guardrail_flags: ContextVar[dict[str, bool] | None] = ContextVar("current_guardrail_flags", default=None)


def reset_guardrail_flags() -> dict[str, bool]:
    """为当前请求或运行创建新的标记字典。"""
    fresh: dict[str, bool] = {}
    current_guardrail_flags.set(fresh)
    return fresh


def get_guardrail_flags() -> dict[str, bool]:
    """返回目前记录的标记；未启用记录时为空。"""
    return current_guardrail_flags.get() or {}
