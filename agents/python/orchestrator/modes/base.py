"""``OrchestrationMode`` 协议 + 每个模式的 ``run()`` 所接收的请求级上下文。

身份信息（用户 email/角色/会话）刻意*不*放在 ``RunContext`` 上 ——
本仓库的约定是使用 ContextVar（``shared/context.py``），由需要它们的
地方（工具、``call_specialist_agent``、A2A 头构建）直接读取，从不作为
函数参数传递。``RunContext`` 只承载模式需要、但并非环境天然提供的那些东西：
本轮的对话历史，以及任何模式想在运行中途持久化时所需的、已解析的会话 id
（目前还没有模式这样做 —— Phase 1.5 的检查点机制是第一个消费者）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from orchestrator.events import OrchestrationEvent


@dataclass(frozen=True)
class ModeCapabilities:
    """某个模式支持哪些能力 —— 通过 ``GET /api/orchestration/modes`` 暴露，
    使 Web UI（Phase 1.6a）无需硬编码各模式的知识就能展示能力标签，
    也让 ``chat.py`` 能通用地做出「该模式是否支持本次请求所需」的判断，
    而不是写一串 ``if mode_name == "x"`` 检查。
    """

    streams: bool = True
    supports_hitl: bool = False
    supports_checkpoints: bool = False
    is_graph: bool = False


@dataclass
class RunContext:
    """请求级、不含身份信息的状态，传入每个模式的 ``run()``。"""

    history: list[dict[str, str]] = field(default_factory=list)
    conversation_id: str | None = None


class OrchestrationMode(Protocol):
    """把一条用户消息变成响应的某种方式。

    这是一个 ``typing.Protocol``（结构化的，而不是要去继承的基类）——
    每个模式都是一个恰好符合这个形状的普通类。
    对于下方的工具路由与处理权交接网格，``graph_mermaid()`` 返回 ``None``，
    因为两者都不是固定图 —— 它们是 LLM（或 MAF）在每轮做出路由决策。
    由工作流支撑的模式（Phase 1 的后续步骤）在那里返回真实的 Mermaid 源码。
    """

    name: str
    label: str  # UI 显示名（面向用户）
    description: str  # UI 显示描述（面向用户）
    capabilities: ModeCapabilities

    async def run(self, message: str, ctx: RunContext) -> AsyncIterator[OrchestrationEvent]:
        """运行一轮。必须作为最后一项恰好产出一个 ``kind="run_completed"``
        事件，在 ``payload["text"]`` 中携带完整响应文本，在
        ``payload["agents_involved"]`` 中携带参与智能体的有序列表
        —— 这是每个调用方（阻塞式 ``/api/chat``、流式
        ``/api/chat/stream``）寻找最终答案的唯一位置，因此模式不必为每个
        调用方各自定制一套「我该如何把文本取回来」的契约。
        """
        ...

    def graph_mermaid(self) -> str | None:
        """本模式结构的静态 Mermaid 源码；若该模式没有固定图则返回 None
        （工具路由与处理权交接网格是按轮路由的，而非沿固定拓扑 ——
        见类文档字符串）。
        """
        ...
