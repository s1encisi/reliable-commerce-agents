"""编排事件适配测试。

用两条真实 MAF 生产工作流的事件流验证字段形态和类型分派，
避免手工事件替身遗漏框架约束。
"""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator.events import OrchestrationEvent, _jsonable, adapt_step, adapt_workflow_event
from shared.config import settings
from tests.test_pre_purchase_workflow import _price_good, _sentiment_ok, _shipping_fast, _stock_ok
from tests.test_return_replace_workflow import TOOLS_HAPPY
from workflows.pre_purchase import PrePurchaseWorkflow, ResearchState
from workflows.return_replace import ReturnAndReplaceWorkflow, WorkflowState


async def _collect_adapted(workflow, run_input: Any) -> list[OrchestrationEvent]:
    adapted: list[OrchestrationEvent] = []
    async for event in workflow.run(run_input, stream=True):
        out = adapt_workflow_event(event)
        if out is not None:
            adapted.append(out)
    return adapted


# ─────────────────────── adapt_workflow_event: sequential + HITL ───────────────────────


@pytest.mark.asyncio
async def test_low_value_return_produces_node_enter_exit_pairs_and_run_started() -> None:
    wf = ReturnAndReplaceWorkflow(TOOLS_HAPPY)._build_maf_workflow()
    state = WorkflowState(user_email="a@b.com", order_id="o1", order_total=50.0)
    events = await _collect_adapted(wf, state)

    kinds = [e.kind for e in events]
    assert kinds[0] == "run_started"
    assert "node_enter" in kinds
    assert "node_exit" in kinds

    enter_ids = {e.node_id for e in events if e.kind == "node_enter"}
    assert {"check-eligibility", "initiate-return", "search-replacements", "hitl-gate", "finalize"} <= enter_ids


@pytest.mark.asyncio
async def test_silent_event_types_are_dropped_not_forwarded() -> None:
    """started、status 和 superstep 事件不应变成无意义界面帧。

    这些事件缺少可展示的执行器和数据，适配器应返回 None。
    """
    wf = ReturnAndReplaceWorkflow(TOOLS_HAPPY)._build_maf_workflow()
    state = WorkflowState(user_email="a@b.com", order_id="o1", order_total=50.0)

    raw_types = []
    adapted_count = 0
    async for event in wf.run(state, stream=True):
        raw_types.append(event.type)
        if adapt_workflow_event(event) is not None:
            adapted_count += 1

    assert "status" in raw_types or "superstep_started" in raw_types, "test workflow should exercise silent types"
    assert adapted_count < len(raw_types), "silent types must be filtered, not forwarded 1:1"


@pytest.mark.asyncio
async def test_high_value_return_produces_request_info_event_with_resume_fields() -> None:
    high = settings.RETURN_HITL_THRESHOLD + 100.0
    wf = ReturnAndReplaceWorkflow(TOOLS_HAPPY)._build_maf_workflow()
    state = WorkflowState(user_email="a@b.com", order_id="o1", order_total=high)
    events = await _collect_adapted(wf, state)

    request_info_events = [e for e in events if e.kind == "request_info"]
    assert len(request_info_events) == 1
    evt = request_info_events[0]
    assert evt.node_id == "hitl-gate", "node_id must be source_executor_id, not the (unset) executor_id"
    assert evt.payload["request_id"], "resume token must be present for a later /resume call"
    assert evt.payload["request_type"] == "ReturnApprovalRequest"
    assert evt.payload["response_type"] == "bool"
    assert evt.payload["data"]["order_total"] == high


# ─────────────────────── adapt_workflow_event: fan-out/fan-in ───────────────────────


@pytest.mark.asyncio
async def test_fan_out_fan_in_produces_multiple_concurrent_node_enters() -> None:
    tools = {
        "analyze_sentiment": _sentiment_ok,
        "check_stock": _stock_ok,
        "get_price_history": _price_good,
        "estimate_shipping": _shipping_fast,
    }
    wf = PrePurchaseWorkflow(tools)._build_maf_workflow()
    events = await _collect_adapted(wf, ResearchState(product_id="sku-1"))

    enter_ids = [e.node_id for e in events if e.kind == "node_enter"]
    # 三个调研分支都必须出现，
    # 证明适配器未合并或丢失并发调用事件。
    assert len(enter_ids) >= 3
    assert len(set(enter_ids)) == len(enter_ids), "fan-out node ids must be distinct, not deduplicated away"


# ─────────────────────── adapt_step ───────────────────────


def test_adapt_step_maps_tool_call_fields() -> None:
    step = {
        "tool_name": "search_products",
        "tool_input": {"query": "headphones"},
        "tool_output": {"count": 3},
        "status": "success",
        "duration_ms": 42,
        "agent": "product-discovery",
    }
    event = adapt_step(step)
    assert event.kind == "tool_call"
    assert event.node_id == "search_products"
    assert event.agent == "product-discovery"
    assert event.payload["tool_input"] == {"query": "headphones"}
    assert event.payload["tool_output"] == {"count": 3}
    assert event.payload["status"] == "success"
    assert event.payload["duration_ms"] == 42


def test_adapt_step_handles_missing_agent_key() -> None:
    """在路由补充 agent 字段之前记录的步骤，也不能导致异常。"""
    step = {"tool_name": "check_stock", "tool_input": {}, "tool_output": None, "status": "success", "duration_ms": 5}
    event = adapt_step(step)
    assert event.agent is None


# ─────────────────────── _jsonable ───────────────────────


def test_jsonable_handles_dataclass_pydantic_and_plain_values() -> None:
    from workflows.return_replace import ReturnApprovalRequest

    req = ReturnApprovalRequest(
        order_id="o1",
        order_total=100.0,
        refund_amount=20.0,
        replacement_count=1,
        user_email="buyer@example.test",
        reason="Wrong size",
        approval={"policy_version": "returns-v1"},
    )
    result = _jsonable(req)
    assert result == {
        "order_id": "o1",
        "order_total": 100.0,
        "refund_amount": 20.0,
        "replacement_count": 1,
        "user_email": "buyer@example.test",
        "reason": "Wrong size",
        "approval": {"policy_version": "returns-v1"},
        "operation_id": None,
    }
    assert _jsonable(None) is None
    assert _jsonable("text") == "text"
    assert _jsonable([1, "a", None]) == [1, "a", None]
    assert _jsonable({"k": 1}) == {"k": 1}


def test_jsonable_falls_back_to_str_for_unknown_objects() -> None:
    class Unknown:
        def __str__(self) -> str:
            return "unknown-repr"

    assert _jsonable(Unknown()) == "unknown-repr"


# ─────────────────────── OrchestrationEvent model ───────────────────────


def test_orchestration_event_has_a_timestamp_by_default() -> None:
    event = OrchestrationEvent(kind="run_started")
    assert event.ts_ms > 0
    assert event.node_id is None
    assert event.payload == {}
