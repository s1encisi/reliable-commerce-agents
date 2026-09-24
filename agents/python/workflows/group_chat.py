"""群聊 / 圆桌辩论工作流 —— MAF 顺序式多视角模式。

这是本包中一个 *独特* 的 MAF 模式：

- ``pre_purchase`` 是并发的扇出/扇入（相互独立的探测，最后合并一次）。
- ``return_replace`` 是带人工参与（Human-in-the-Loop，HITL）闸门的顺序流程。
- ``group_chat``（即本文件）是顺序式的 **圆桌**：各与会者围绕一份 *共享记录*
  轮流发言 —— 每位与会者都能看到前面的人说了什么 —— 最后由主持人综合出
  最终结论。它模拟的是一场"这东西值不值得买？"的辩论，例如性价比/定价视角
  与质量/评论视角之间的交锋。

与会者的行为以 ``Responder`` 可调用对象的形式注入，因此该工作流是确定性的，
无需 LLM 即可做单元测试；生产代码可以传入由智能体支撑的 responder。

导入使用 ``agent_framework._workflows`` 的子模块（而非包根）：在普通 checkout 中
agent-framework v1.0 beta 的顶层 ``__init__`` 是空的，所以其他工作流同样使用
这些稳定的子模块路径。
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from agent_framework._workflows._executor import Executor, handler
from agent_framework._workflows._workflow_builder import WorkflowBuilder
from agent_framework._workflows._workflow_context import WorkflowContext

logger = logging.getLogger(__name__)

# 给定问题和不断增长的记录（此前的发言轮次），返回该与会者的发言内容 ——
# 可以是直接返回（同步方式，本模块中每个测试都用这种方式），也可以返回
# 可等待对象（异步方式，用于由真实智能体支撑的与会者 —— 参见
# orchestrator/modes/group_chat_mode.py，它是第一个生产调用方）。
Responder = Callable[[str, list[dict[str, str]]], "str | Awaitable[str]"]


@dataclass
class GroupChatState:
    """贯穿整场圆桌的共享状态。"""

    question: str
    transcript: list[dict[str, str]] = field(default_factory=list)
    verdict: str = ""
    completed_steps: list[str] = field(default_factory=list)


def _default_synthesis(state: GroupChatState) -> str:
    speakers = ", ".join(turn["speaker"] for turn in state.transcript)
    return f"Synthesized {len(state.transcript)} perspective(s) ({speakers}) on: {state.question}"


# ─────────────────────────── 执行器 ───────────────────────────


class _PanelistExecutor(Executor):
    """某位与会者的发言轮次：向共享记录追加一段发言。"""

    def __init__(self, name: str, responder: Responder) -> None:
        super().__init__(id=f"panelist-{name}")
        self._name = name
        self._responder = responder

    @handler
    async def run(self, state: GroupChatState, ctx: WorkflowContext[GroupChatState, GroupChatState]) -> None:
        try:
            result = self._responder(state.question, list(state.transcript))
            text = await result if inspect.isawaitable(result) else result
        except Exception as exc:  # pragma: no cover - 防御性处理
            text = f"({self._name} could not respond: {exc})"
            logger.exception("group_chat.panelist_failed name=%s", self._name)
        state.transcript.append({"speaker": self._name, "text": text})
        state.completed_steps.append(self._name)
        await ctx.send_message(state)


class _ModeratorExecutor(Executor):
    """最后发言轮次：将记录综合为最终结论并输出。"""

    def __init__(self, synthesizer: Callable[[GroupChatState], str] | None = None) -> None:
        super().__init__(id="moderator")
        self._synthesizer = synthesizer or _default_synthesis

    @handler
    async def run(self, state: GroupChatState, ctx: WorkflowContext[None, GroupChatState]) -> None:
        state.verdict = self._synthesizer(state)
        state.completed_steps.append("moderator")
        await ctx.yield_output(state)


# ─────────────────────────── 工作流 ───────────────────────────


@dataclass
class GroupChatWorkflow:
    """由若干与会者顺序发言、随后由主持人收尾的圆桌。

    Args:
        panelists: 有序的 ``(name, responder)`` 对；每个按顺序只运行一次，
            并能看到此前与会者累积的记录。
        synthesizer: 可选的结论生成函数，作用于最终状态。
    """

    panelists: list[tuple[str, Responder]]
    synthesizer: Callable[[GroupChatState], str] | None = None

    def _build(self) -> Any:
        if not self.panelists:
            raise ValueError("group chat needs at least one panelist")
        execs = [_PanelistExecutor(name, responder) for name, responder in self.panelists]
        moderator = _ModeratorExecutor(self.synthesizer)
        builder = WorkflowBuilder(start_executor=execs[0], name="group-chat-debate")
        for upstream, downstream in zip(execs, execs[1:]):
            builder = builder.add_edge(upstream, downstream)
        builder = builder.add_edge(execs[-1], moderator)
        return builder.build()

    async def execute(self, question: str) -> GroupChatState:
        """运行圆桌并返回最终填充完毕的状态。"""
        workflow = self._build()
        final = GroupChatState(question=question)
        async for event in workflow.run(final, stream=True):
            if getattr(event, "type", None) == "output":
                data = getattr(event, "data", None)
                if isinstance(data, GroupChatState):
                    final = data
        return final
