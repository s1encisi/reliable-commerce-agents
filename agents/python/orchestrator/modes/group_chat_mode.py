"""``group-chat`` 模式：围绕一份共享记录进行的顺序圆桌辩论。

包装 ``workflows/group_chat.py`` 的 ``GroupChatWorkflow`` —— 按审计结论，
它此前只在其自身测试中用合成的同步响应者被演练过。这是第一个生产调用方：
两位由智能体支撑的圆桌成员（一个从性价比/定价视角，一个从质量/评论视角），
每位发言前都能看到此前发言，最后由主持人综合出结论。按该模块自己的
文档字符串所述，这是一个与已接入的其他模式都*不同*的 MAF 模式 ——
不是扇出（``workflow:pre-purchase``），也不是 LLM 工具路由（``tool``）
或网格（``handoff``）—— 当某个决策受益于多个具名视角而非某一位专业智能体
的答案时，它很有用。

它需要对 ``workflows/group_chat.py`` 做一处小改动：``Responder`` 原本是
严格同步的（现有测试都只传入普通函数）。而真实的圆桌成员需要一次 LLM
调用，那是异步的 —— ``_PanelistExecutor.run()`` 现在会在响应者结果可等待时
await 它，因此本模式可以传入一个返回协程的闭包，而
``workflows/group_chat.py`` 无需了解任何关于 MAF ``Agent`` 的东西。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

from orchestrator.events import OrchestrationEvent, adapt_workflow_event
from shared.grounding.ledger import reset_grounding_ledger

from .base import ModeCapabilities, RunContext

_PANEL_PROMPTS = {
    "value": (
        "You are the value/pricing panelist on a purchase-decision round-table. "
        "Given the question and what's been said so far, give a short (2-3 sentence) "
        "take from a price-and-value angle only. Don't repeat prior speakers."
    ),
    "quality": (
        "You are the quality/reviews panelist on a purchase-decision round-table. "
        "Given the question and what's been said so far, give a short (2-3 sentence) "
        "take from a build-quality-and-reviews angle only. Don't repeat prior speakers."
    ),
}


def _format_transcript(transcript: list[dict[str, str]]) -> str:
    if not transcript:
        return "(no prior turns)"
    return "\n".join(f"{turn['speaker']}: {turn['text']}" for turn in transcript)


def _make_agent_responder(panel_name: str, instructions: str) -> Callable[[str, list[dict[str, str]]], Awaitable[str]]:
    async def _respond(question: str, transcript: list[dict[str, str]]) -> str:
        from agent_framework import Agent

        from shared.factory import get_chat_client
        from shared.middleware import build_specialist_middleware

        # 圆桌成员是自由形式的 LLM 评述、没有挂任何工具，但它们此前完全
        # 没有中间件 —— 意味着没有 PII 脱敏、没有注入检测，且（在此之前）
        # 对自身输出没有事实核验（grounding）检查。这里用的是其他所有智能体
        # 都在用的同一个接线点；由于没有工具调用需要记录，
        # StepRecorderMiddleware/GroundingLedgerMiddleware 在此为空操作。
        agent = Agent(
            client=get_chat_client(),
            instructions=instructions,
            name=panel_name,
            middleware=build_specialist_middleware(),
        )
        prompt = f"Question: {question}\n\nTranscript so far:\n{_format_transcript(transcript)}"
        response = await agent.run(prompt)
        return response.text or f"({panel_name} had nothing to add)"

    return _respond


class GroupChatMode:
    name = "group-chat"
    label = "群聊（圆桌辩论）"
    description = (
        "MAF 顺序工作流：具名圆桌成员围绕一份共享记录轮流发言 —— "
        "每位发言前都能看到此前的发言 —— 最后由主持人综合出结论。"
        "与工具路由（单次专业智能体调用）和移交（控制权易手）都不同："
        "这里每位成员都会发言。"
    )
    capabilities = ModeCapabilities(streams=True, supports_hitl=False, supports_checkpoints=False, is_graph=True)

    def __init__(self, panelists: list[tuple[str, Callable]] | None = None) -> None:
        self._panelists = panelists

    def _resolve_panelists(self) -> list[tuple[str, Callable]]:
        if self._panelists is not None:
            return self._panelists
        return [(name, _make_agent_responder(name, instructions)) for name, instructions in _PANEL_PROMPTS.items()]

    async def run(self, message: str, ctx: RunContext) -> AsyncIterator[OrchestrationEvent]:
        from workflows.group_chat import GroupChatState, GroupChatWorkflow

        reset_grounding_ledger()

        panelists = self._resolve_panelists()
        maf_workflow = GroupChatWorkflow(panelists=panelists)._build()
        state = GroupChatState(question=message)

        final_state = state
        async for event in maf_workflow.run(state, stream=True):
            adapted = adapt_workflow_event(event)
            if adapted is not None:
                yield adapted
            if getattr(event, "type", None) == "output":
                data = getattr(event, "data", None)
                if isinstance(data, GroupChatState):
                    final_state = data

        # 这里没有 "grounding" 键：每位圆桌成员自己的 agent.run() 调用仍会
        # 针对其本轮文本执行 GroundingVerificationMiddleware（见上方
        # _make_agent_responder），但那份按轮的报告并没有回传到
        # workflows/group_chat.py 的记录条目（只有 {"speaker", "text"}）
        # 或 GroupChatState 中。要把它呈现在 UI 里，需要扩展那个数据模型，
        # 而不是改事实核验（grounding）代码。
        agents_involved = [name for name, _ in panelists] + ["moderator"]
        yield OrchestrationEvent(
            kind="run_completed",
            payload={
                "text": final_state.verdict,
                "agents_involved": agents_involved,
                "transcript": final_state.transcript,
            },
        )

    def graph_mermaid(self) -> str | None:
        names = [name for name, _ in self._resolve_panelists()]
        if not names:
            return None
        node_ids = [f"panelist_{n}[{n}]" for n in names]
        edges = [f"  {a} --> {b}" for a, b in zip(node_ids, node_ids[1:])]
        edges.append(f"  {node_ids[-1]} --> moderator")
        return "graph LR\n" + "\n".join(edges)
