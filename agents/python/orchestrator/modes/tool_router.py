"""默认模式：编排器 LLM 每轮调用 ``call_specialist_agent``。

它包装的正是本模块出现之前 ``orchestrator/routes/chat.py`` 的 ``chat()``
所直接做的事 —— ``create_orchestrator_agent()`` + ``_run_agent_native()``
—— 行为完全不变。这次抽取正是为了让 ``handoff``（或后续步骤中的工作流）
能在同一个 ``run()`` 契约下被替换进来。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from orchestrator.events import OrchestrationEvent, adapt_step
from shared.agent_observability import get_steps, reset_steps
from shared.grounding.ledger import reset_grounding_ledger

from .base import ModeCapabilities, RunContext


class ToolRouterMode:
    name = "tool"
    label = "工具路由"
    description = "编排器 LLM 调用 call_specialist_agent 路由到某个专业智能体。单跳：每轮一个专业智能体，由模型决定。"
    capabilities = ModeCapabilities(
        streams=True,
        supports_hitl=True,  # 经由 shared/hitl.py 的 FunctionMiddleware，而非工作流内
        supports_checkpoints=False,
        is_graph=False,
    )

    async def run(self, message: str, ctx: RunContext) -> AsyncIterator[OrchestrationEvent]:
        from orchestrator.agent import create_orchestrator_agent
        from shared.agent_host import _run_agent_native

        agent = create_orchestrator_agent()
        reset_steps()
        reset_grounding_ledger()

        run_metadata: dict = {}
        response_text = await _run_agent_native(agent, message, history=ctx.history, metadata_box=run_metadata)

        steps = get_steps()
        for step in steps:
            step.setdefault("agent", "orchestrator")
            yield adapt_step(step)

        agents_involved = list(dict.fromkeys(["orchestrator", *(s.get("agent", "orchestrator") for s in steps)]))
        yield OrchestrationEvent(
            kind="run_completed",
            payload={
                "text": response_text,
                "agents_involved": agents_involved,
                "steps": steps,
                "grounding": run_metadata.get("grounding"),
                "usage": run_metadata.get("_maf_usage"),
            },
        )

    def graph_mermaid(self) -> str | None:
        return None
