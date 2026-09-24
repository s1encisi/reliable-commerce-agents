"""退货与换货顺序工作流及 HITL 测试。

替换业务工具，真实执行暂停和 response_handler 恢复。首次事件流
必须消费完再恢复，否则工作流仍标为运行中，第二次 run 会失败。
"""

from __future__ import annotations

from typing import Any

import pytest

from shared.config import settings
from workflows.return_replace import (
    ReturnAndReplaceWorkflow,
    ReturnApprovalRequest,
    WorkflowState,
)

# ─────────────────────── Tool stubs ───────────────────────


async def _eligible(order_id: str) -> dict[str, Any]:
    return {"eligible": True}


async def _not_eligible(order_id: str) -> dict[str, Any]:
    return {"eligible": False, "reason": "Past 30-day return window"}


async def _initiate_ok(order_id: str, reason: str, refund_method: str) -> dict[str, Any]:
    return {"return_id": "ret-99", "refund_amount": 120.0}


async def _search_ok(max_price: float, min_rating: float, limit: int) -> list[dict[str, Any]]:
    return [{"id": "p-1", "name": "Replacement A"}, {"id": "p-2", "name": "Replacement B"}]


async def _tier_gold() -> dict[str, Any]:
    return {"tier": "gold", "discount_pct": 10.0}


async def _tier_none() -> dict[str, Any]:
    return {"tier": "bronze", "discount_pct": 0.0}


TOOLS_HAPPY: dict[str, Any] = {
    "check_return_eligibility": _eligible,
    "initiate_return": _initiate_ok,
    "search_products": _search_ok,
    "get_loyalty_tier": _tier_gold,
}


@pytest.mark.asyncio
async def test_invalid_success_payload_is_not_announced_as_completed() -> None:
    async def invalid(**kwargs: Any) -> dict[str, Any]:
        return {"return_id": "unverified", "refund_amount": "corrupt"}

    result = await ReturnAndReplaceWorkflow({**TOOLS_HAPPY, "initiate_return": invalid}).execute(
        WorkflowState(user_email="a@b.com", order_id="o1", order_total=10, reason="wrong")
    )
    assert result.outcome == "UNKNOWN"
    assert result.return_id is None
    assert "finalize" not in result.completed_steps


@pytest.mark.asyncio
async def test_invalid_eligibility_type_stops_before_any_write() -> None:
    async def invalid(**kwargs: Any) -> dict[str, Any]:
        return {"eligible": "yes"}

    result = await ReturnAndReplaceWorkflow({**TOOLS_HAPPY, "check_return_eligibility": invalid}).execute(
        WorkflowState(user_email="a@b.com", order_id="o1", order_total=10, reason="wrong")
    )
    assert result.outcome == "FAILED_FINAL"
    assert result.return_id is None
    assert "initiate_return" not in result.completed_steps


# ─────────────────────── Happy path (no HITL) ─────────────


@pytest.mark.asyncio
async def test_low_value_return_completes_without_hitl() -> None:
    state = WorkflowState(user_email="a@b.com", order_id="o1", order_total=50.0, reason="wrong size")
    result = await ReturnAndReplaceWorkflow(TOOLS_HAPPY).execute(state)

    assert result.hitl_requested is False
    assert result.hitl_approved is True
    assert result.return_id == "ret-99"
    assert result.refund_amount == 120.0
    assert len(result.replacement_products) == 2
    assert result.applied_discount == {"tier": "gold", "discount_pct": 10.0}
    assert "finalize" in result.completed_steps
    assert result.errors == []


@pytest.mark.asyncio
async def test_bronze_tier_means_no_discount_applied() -> None:
    tools = {**TOOLS_HAPPY, "get_loyalty_tier": _tier_none}
    state = WorkflowState(user_email="a@b.com", order_id="o2", order_total=50.0)
    result = await ReturnAndReplaceWorkflow(tools).execute(state)

    assert result.applied_discount is None
    assert "apply_discount" in result.completed_steps


# ─────────────────────── Eligibility rejection ───────────


@pytest.mark.asyncio
async def test_ineligible_order_short_circuits() -> None:
    tools = {**TOOLS_HAPPY, "check_return_eligibility": _not_eligible}
    state = WorkflowState(user_email="a@b.com", order_id="o3", order_total=50.0)
    result = await ReturnAndReplaceWorkflow(tools).execute(state)

    assert result.return_eligible is False
    assert "Past 30-day return window" in result.errors
    assert result.return_id is None
    assert "initiate_return" not in result.completed_steps


# ─────────────────────── HITL — high-value orders ─────────


@pytest.mark.asyncio
async def test_high_value_return_pauses_for_approval() -> None:
    high = settings.RETURN_HITL_THRESHOLD + 100.0
    state = WorkflowState(user_email="a@b.com", order_id="o4", order_total=high)
    result = await ReturnAndReplaceWorkflow(TOOLS_HAPPY).execute(state)

    assert result.hitl_requested is True, "HITL gate must trigger for high-value orders"
    # 工作流已暂停，finalize 尚未执行。
    assert "finalize" not in result.completed_steps
    assert result.hitl_approved is None


@pytest.mark.asyncio
async def test_hitl_request_emits_expected_payload() -> None:
    """直接检查 request_info 中的 ReturnApprovalRequest。"""
    high = settings.RETURN_HITL_THRESHOLD + 100.0
    state = WorkflowState(user_email="a@b.com", order_id="o5", order_total=high)
    workflow = ReturnAndReplaceWorkflow(TOOLS_HAPPY)._build_maf_workflow()

    request_payloads: list[ReturnApprovalRequest] = []
    async for event in workflow.run(state, stream=True):
        if getattr(event, "type", None) == "request_info":
            data = getattr(event, "data", None)
            if isinstance(data, ReturnApprovalRequest):
                request_payloads.append(data)

    assert len(request_payloads) == 1
    payload = request_payloads[0]
    assert payload.order_id == "o5"
    assert payload.order_total == high
    # 审批位于所有写操作和替换查询之前，
    # 因此此处是申请金额估算，不是已创建退货的结果。
    assert payload.refund_amount == high
    assert payload.replacement_count == 0


# ─────────────────────── Threshold boundary ───────────────


@pytest.mark.asyncio
async def test_order_at_threshold_does_not_trigger_hitl() -> None:
    """只有严格超过阈值才暂停；恰好相等时不触发门控。"""
    state = WorkflowState(
        user_email="a@b.com",
        order_id="o6",
        order_total=settings.RETURN_HITL_THRESHOLD,
    )
    result = await ReturnAndReplaceWorkflow(TOOLS_HAPPY).execute(state)
    assert result.hitl_requested is False
    assert "finalize" in result.completed_steps


# ─────────────────────── Tool failure graceful ──────────────


@pytest.mark.asyncio
async def test_failing_initiate_return_surfaces_error_without_finalize() -> None:
    async def _boom(**_: Any) -> dict[str, Any]:
        raise RuntimeError("downstream unavailable")

    tools = {**TOOLS_HAPPY, "initiate_return": _boom}
    state = WorkflowState(user_email="a@b.com", order_id="o7", order_total=25.0)
    result = await ReturnAndReplaceWorkflow(tools).execute(state)

    assert any("could not be confirmed" in e for e in result.errors)
    assert result.outcome == "UNKNOWN"
    assert "finalize" not in result.completed_steps


# ─────────────────────── Workflow structure ─────────────────


def test_workflow_builder_wires_all_six_executors() -> None:
    wf = ReturnAndReplaceWorkflow(TOOLS_HAPPY)._build_maf_workflow()
    ids = {getattr(e, "id", None) for e in wf.get_executors_list()}
    assert {
        "check-eligibility",
        "initiate-return",
        "search-replacements",
        "hitl-gate",
        "apply-discount",
        "finalize",
    } <= ids


# ─────────────────────── HITL resume (response_handler) ─────


async def _pause_and_collect_request_id(workflow, state) -> str:
    """完整消费首次运行，返回待处理请求标识。

    提前放弃事件流会留下运行中标志，导致恢复失败。
    """
    request_id: str | None = None
    async for event in workflow.run(state, stream=True):
        if request_id is None and getattr(event, "type", None) == "request_info":
            request_id = getattr(event, "request_id", None)
    assert request_id, "expected a request_info event to pause the workflow"
    return request_id


@pytest.mark.asyncio
async def test_hitl_approval_resumes_and_finalizes() -> None:
    """批准的高金额退货恢复后经过折扣和收尾步骤。"""
    high = settings.RETURN_HITL_THRESHOLD + 100.0
    state = WorkflowState(user_email="a@b.com", order_id="o10", order_total=high)
    workflow = ReturnAndReplaceWorkflow(TOOLS_HAPPY)._build_maf_workflow()

    request_id = await _pause_and_collect_request_id(workflow, state)

    outputs: list[WorkflowState] = []
    async for event in workflow.run(responses={request_id: True}, stream=True):
        if getattr(event, "type", None) == "output":
            data = getattr(event, "data", None)
            if isinstance(data, WorkflowState):
                outputs.append(data)

    assert outputs, "resume must produce a terminal workflow state"
    final = outputs[-1]
    assert final.hitl_approved is True
    assert "finalize" in final.completed_steps
    assert not final.errors


@pytest.mark.asyncio
async def test_hitl_rejection_resumes_and_stops_before_finalize() -> None:
    """拒绝后记录决定，不执行收尾。"""
    high = settings.RETURN_HITL_THRESHOLD + 100.0
    state = WorkflowState(user_email="a@b.com", order_id="o11", order_total=high)
    workflow = ReturnAndReplaceWorkflow(TOOLS_HAPPY)._build_maf_workflow()

    request_id = await _pause_and_collect_request_id(workflow, state)

    outputs: list[WorkflowState] = []
    async for event in workflow.run(responses={request_id: False}, stream=True):
        if getattr(event, "type", None) == "output":
            data = getattr(event, "data", None)
            if isinstance(data, WorkflowState):
                outputs.append(data)

    assert outputs, "resume must produce a terminal workflow state"
    final = outputs[-1]
    assert final.hitl_approved is False
    assert "finalize" not in final.completed_steps
    assert any("rejected by reviewer" in e for e in final.errors)
