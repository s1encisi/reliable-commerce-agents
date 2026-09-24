"""编排模式注册表。

``/api/chat`` 与 ``/api/chat/stream`` 不再硬编码「构建工具路由智能体并运行它」
—— 它们把一个模式名解析为一个 :class:`OrchestrationMode`，然后调用它的
``run()``。正因如此，这个毕业项目的旗舰主张才成立：同一个业务领域，
经由普通 LLM 工具路由、MAF ``HandoffBuilder`` 网格、MAF ``WorkflowBuilder``
扇出/扇入与顺序+人工参与图，以及一场圆桌辩论，都能从同一个端点并排运行。

截至本步骤共注册了六个模式 —— ``tool`` 与 ``handoff``（Phase 1.2）、
``workflow:pre-purchase``/``workflow:return-replace``（固定的 MAF 工作流图，
此前仅有测试）以及 ``group-chat``（顺序共享记录辩论，同样此前仅有测试）。
``magentic`` 与声明式 YAML 模式可能在后续步骤落地；``get_mode()`` 对未注册的
模式已经会抛出清晰、带名字的错误，而不是裸的 ``KeyError``，因此提前请求
其中之一是可诊断的。
"""

from __future__ import annotations

from .base import ModeCapabilities, OrchestrationMode, RunContext
from .decision_router import DecisionRouterMode
from .group_chat_mode import GroupChatMode
from .handoff_mode import HandoffMode
from .tool_router import ToolRouterMode
from .workflow_mode import PrePurchaseMode, ReturnReplaceMode

MODES: dict[str, OrchestrationMode] = {
    "tool": ToolRouterMode(),
    "decision-router": DecisionRouterMode(),
    "handoff": HandoffMode(),
    "workflow:pre-purchase": PrePurchaseMode(),
    "workflow:return-replace": ReturnReplaceMode(),
    "group-chat": GroupChatMode(),
}

DEFAULT_MODE = "tool"


class UnknownModeError(ValueError):
    """``get_mode`` 对不在注册表中的名字抛出的错误。"""


def get_mode(name: str | None) -> OrchestrationMode:
    """把模式名解析为对应的 :class:`OrchestrationMode`。

    ``None`` 或 ``""`` 会解析为 :data:`DEFAULT_MODE` —— 执行完整优先级链
    （请求体 ``mode`` → ``settings.ORCHESTRATION_MODE`` → 默认值）的调用方，
    在调用本函数之前应当已经替换为真实的名字；这里只是防御一个显式为空的
    名字抵达此处。
    """
    resolved = name or DEFAULT_MODE
    try:
        return MODES[resolved]
    except KeyError:
        raise UnknownModeError(
            f"Unknown orchestration mode {resolved!r}. Available: {sorted(MODES)}. "
            "workflow:*, group-chat, and magentic are registered in later Phase 1 steps."
        ) from None


def list_modes() -> list[dict[str, object]]:
    """供 ``GET /api/orchestration/modes`` 使用的可序列化模式清单。"""
    return [
        {
            "name": name,
            "label": mode.label,  # UI 显示名（面向用户，中文）
            "description": mode.description,  # UI 显示描述（面向用户，中文）
            "capabilities": mode.capabilities.__dict__,
            "default": name == DEFAULT_MODE,
        }
        for name, mode in MODES.items()
    ]


__all__ = [
    "MODES",
    "DEFAULT_MODE",
    "ModeCapabilities",
    "OrchestrationMode",
    "RunContext",
    "UnknownModeError",
    "get_mode",
    "list_modes",
]
