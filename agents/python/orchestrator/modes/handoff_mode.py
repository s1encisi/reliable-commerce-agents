"""``handoff`` 模式：在同一批专业智能体之上运行 MAF ``HandoffBuilder`` 网格。

包装 ``orchestrator/handoff.py::build_orchestrator_handoff_workflow`` ——
它早已构建完成、也早已有测试覆盖
（``tests/test_handoff_orchestration.py``），但此前从未能从实际请求触达。
本模块是第一个触达它的东西：``ORCHESTRATION_MODE=handoff`` 或按请求传入
``mode="handoff"``，现在真的会运行这个网格，而不是工具路由。

最终答案与参与者顺序的提取方式，参照
``tutorials/14-handoff-orchestration/python/main.py::ask()`` —— 那个教程是
读取处理权交接工作流事件流的经验证参考实现（已在 Phase 0 对照真实 Azure
调用确认）："output" 事件按执行器 id 携带增量的 ``AgentResponseUpdate``
文本，而最终答案是最后一位参与者完整拼装后的那一轮发言。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from orchestrator import handoff as handoff_module
from orchestrator.events import OrchestrationEvent, adapt_workflow_event

from .base import ModeCapabilities, RunContext


class HandoffMode:
    name = "handoff"
    label = "处理权交接网格"
    description = (
        "MAF HandoffBuilder 网格：编排器机械地把控制权交给某个专业智能体再交回，而不是由 LLM 每轮通过工具调用自行决定。"
    )
    capabilities = ModeCapabilities(
        streams=True,
        supports_hitl=False,  # shared/hitl.py 与工作流内门控都未接入这个网格
        supports_checkpoints=False,
        is_graph=True,
    )

    async def run(self, message: str, ctx: RunContext) -> AsyncIterator[OrchestrationEvent]:
        # 为了与工具路由保持一致性而透传 —— 处理权交接的专业智能体是
        # RemoteSpecialistChatClient 实例，目前并不读取这个 ContextVar
        # （它们只压平当前轮的提示词，见 shared/remote_agent.py），但设置它
        # 没有成本，还能让两种模式的请求准备过程完全一致。

        # 刻意使用模块限定访问（而非 `from orchestrator.handoff import ...`）
        # —— 测试会 monkeypatch orchestrator.handoff._load_registry
        # / .create_orchestrator_agent，而只有通过模块对象读取属性才会生效，
        # 在导入时绑定到本模块命名空间的名字则不会。
        known_participants = {"orchestrator", *handoff_module._load_registry().keys()}
        workflow = handoff_module.build_orchestrator_handoff_workflow()

        current_speaker: str | None = None
        turns: list[tuple[str, list[str]]] = []

        async for event in workflow.run(message, stream=True):
            etype = getattr(event, "type", None)

            if etype == "output":
                executor_id = getattr(event, "executor_id", None)
                if executor_id in known_participants:
                    if current_speaker != executor_id:
                        current_speaker = executor_id
                        turns.append((executor_id, []))
                    update = getattr(event, "data", None)
                    text = getattr(update, "text", None) if update is not None else None
                    if text:
                        turns[-1][1].append(text)

            adapted = adapt_workflow_event(event)
            if adapted is not None:
                yield adapted

        assembled = [(eid, "".join(parts).strip()) for eid, parts in turns if any(parts)]
        agents_involved = list(dict.fromkeys(eid for eid, _ in assembled)) or ["orchestrator"]
        final_text = assembled[-1][1] if assembled else ""

        # 这里没有 "grounding" 键（与 tool_router.py 不同）：每位参与者自己的
        # agent.run() 调用仍会执行 GroundingVerificationMiddleware，也仍会在
        # 持久化之前修正/剥离其最终响应，但上面的 final_text 是通过拼接
        # HandoffBuilder 工作流事件流中原始的 AgentResponseUpdate 分片组装
        # 而成的 —— 它从不暴露那些 additional_properties 携带报告的
        # 按参与者 AgentResponse。要在这个模式的 UI 里呈现该报告，需要修改
        # HandoffBuilder 的事件流，而不是改事实核验（grounding）。
        yield OrchestrationEvent(
            kind="run_completed",
            payload={"text": final_text, "agents_involved": agents_involved},
        )

    def graph_mermaid(self) -> str | None:
        return None
