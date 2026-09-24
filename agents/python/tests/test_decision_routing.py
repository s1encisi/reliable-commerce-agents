"""决策契约、上下文保留、只读保护与关闭／影子／短路径回归。"""

import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.events import OrchestrationEvent
from orchestrator.modes.base import RunContext
from orchestrator.modes.decision_router import DecisionRouterMode
from shared.context_pipeline import ContextOverflowError, build_context
from shared.execution_policy import ReadOnlyToolMiddleware, current_execution_policy
from shared.jev.async_client import Decision, DecisionError, parse_decision
from shared.stream_queue import BoundedStreamQueue


def test_context_keeps_early_constraints_without_promoting_roles():
    history = [{"role": "user", "content": "预算不能超过500元，不要入耳式"}]
    history += [{"role": "assistant", "content": "无关信息" * 500} for _ in range(60)]
    context = build_context("还要黑色", history, max_bytes=1200)
    assert context.constraints == ["预算不能超过500元，不要入耳式"]
    assert len(context.history) < 60
    assert all(m["role"] != "system" for m in context.messages())
    with pytest.raises(ContextOverflowError):
        build_context("必须" + "甲" * 1000, [], max_bytes=50)


def test_jev_rejects_unknown_nonfinite_and_missing_fields():
    good = {
        "model": "jev-1.13.0",
        "answers": {
            "route": {"type": "choice", "choice": "a", "probabilities": {"a": 0.9, "defer": 0.1}, "confidence": 0.8}
        },
    }
    assert parse_decision(good, {"a", "defer"}, 1).usage is None
    good["answers"]["route"]["confidence"] = float("nan")
    with pytest.raises(DecisionError):
        parse_decision(good, {"a", "defer"}, 1)


async def test_readonly_policy_blocks_unknown_and_write_tool():
    token = current_execution_policy.set("read_only")
    called = []

    async def execute():
        called.append(True)

    try:
        for name in ["initiate_return", "store_memory", "new_unknown_tool"]:
            ctx = SimpleNamespace(function=SimpleNamespace(name=name), result=None)
            await ReadOnlyToolMiddleware().process(ctx, execute)
            assert ctx.result["error_code"] == "READ_ONLY_POLICY"
        assert called == []
        await ReadOnlyToolMiddleware().process(
            SimpleNamespace(function=SimpleNamespace(name="get_user_orders")), execute
        )
        assert called == [True]
    finally:
        current_execution_policy.reset(token)


async def test_queue_applies_backpressure_and_cancel_cleans_up():
    queue = BoundedStreamQueue(1, 100)
    await queue.put(("text", "a"))
    task = asyncio.create_task(queue.put(("text", "b")))
    await asyncio.sleep(0.01)
    assert not task.done()
    assert await queue.get() == ("text", "a")
    await asyncio.wait_for(task, 0.2)
    assert await queue.get() == ("text", "b")
    assert queue.buffered_bytes == 0
    with pytest.raises(ValueError):
        await queue.put("x" * 1000)


@pytest.mark.parametrize("mode", ["off", "shadow", "active"])
async def test_mode_switch_and_real_dispatch_contract(monkeypatch, mode):
    import orchestrator.modes.decision_router as router

    monkeypatch.setattr(router.settings, "DECISION_ROUTING_MODE", mode)
    monkeypatch.setattr(router.settings, "DECISION_MIN_PROBABILITY", 0.8)
    monkeypatch.setattr(router.settings, "AGENT_REGISTRY", '{"product-discovery":"http://pd:8081"}')
    calls = []

    async def decide(*args):
        calls.append("decision")
        return Decision("product-discovery", {"product-discovery": 0.9, "defer": 0.1}, 0.8, "jev-1.13.0", 1, None)

    async def dispatch(*args):
        calls.append("specialist")
        return {"response": "已查询", "steps": []}

    async def fallback(*args):
        await asyncio.sleep(0.01)
        calls.append("fallback")
        yield OrchestrationEvent(kind="run_completed", payload={"text": "原路径", "agents_involved": []})

    monkeypatch.setattr(router, "decide_route", decide)
    monkeypatch.setattr(router, "dispatch_readonly", dispatch)
    monkeypatch.setattr(router.ToolRouterMode, "run", fallback)
    events = [e async for e in DecisionRouterMode().run("查询耳机", RunContext())]
    assert len(events) == 1
    assert ("specialist" in calls) == (mode == "active")
    assert ("decision" in calls) == (mode != "off")
    assert events[0].payload["decision"]["mode"] == mode
