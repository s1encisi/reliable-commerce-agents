"""只读保护拦下写入后，聊天可回到已有审批路径；任务不能伪完成。"""

from orchestrator.events import OrchestrationEvent
from orchestrator.modes.base import RunContext
from orchestrator.modes.decision_router import DecisionRouterMode
from shared.jev.async_client import Decision
from shared.task_state import successful_receipt


async def test_write_intent_returns_to_original_approval_path(monkeypatch):
    import orchestrator.modes.decision_router as mod

    monkeypatch.setattr(mod.settings, "DECISION_ROUTING_MODE", "active")
    monkeypatch.setattr(mod.settings, "DECISION_CONTEXT_ENABLED", False)
    monkeypatch.setattr(mod.settings, "DECISION_MIN_PROBABILITY", 0.8)
    monkeypatch.setattr(mod.settings, "AGENT_REGISTRY", '{"order-management":"http://orders"}')
    calls = []

    async def decide(*args):
        return Decision("order-management", {"order-management": 0.99, "defer": 0.01}, 0.95, "jev-1.13.0", 1, None)

    async def readonly(*args):
        calls.append("read_only")
        return {"response": "需要原业务流程", "requires_original_route": True, "policy_denials": ["initiate_return"]}

    async def original(*args):
        calls.append("original")
        yield OrchestrationEvent(
            kind="run_completed", payload={"text": "等待审批", "agents_involved": ["order-management"]}
        )

    monkeypatch.setattr(mod, "decide_route", decide)
    monkeypatch.setattr(mod, "dispatch_readonly", readonly)
    monkeypatch.setattr(mod.ToolRouterMode, "run", original)
    events = [event async for event in DecisionRouterMode().run("申请退货", RunContext())]
    assert calls == ["read_only", "original"]
    assert events[-1].payload["text"] == "等待审批"
    assert events[-1].payload["decision"]["fallback_reason"] == "requires_original_route"


def test_denied_write_is_not_completed_because_a_query_succeeded():
    assert not successful_receipt(
        {"requires_original_route": True, "steps": [{"status": "success", "result_confirmed": True}]},
        "successful_tool_result",
    )
