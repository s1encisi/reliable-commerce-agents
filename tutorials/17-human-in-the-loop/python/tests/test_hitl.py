"""
第 17 章 —— 人在回路：测试。

无需 LLM——人在回路的管道是确定性的。
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import RefundApprovalRequest, build_workflow, run_with_response  # noqa: E402


@pytest.mark.asyncio
async def test_workflow_builds() -> None:
    assert build_workflow() is not None


@pytest.mark.asyncio
async def test_approved_refund_reports_approved() -> None:
    result = await run_with_response(order_id="ord-1001", amount=125.0, approved=True)
    assert "已批准" in result
    assert "ord-1001" in result
    assert "125" in result


@pytest.mark.asyncio
async def test_denied_refund_reports_denied() -> None:
    result = await run_with_response(order_id="ord-2002", amount=75.0, approved=False)
    assert "已驳回" in result
    assert "ord-2002" in result


@pytest.mark.asyncio
async def test_workflow_pauses_for_human_before_first_response() -> None:
    """首次运行应产出 request_info 事件并暂停，而不是直接完成。"""
    workflow = build_workflow()
    saw_request = False
    saw_output = False
    async for event in workflow.run(RefundApprovalRequest(order_id="ord-3003", amount=50.0), stream=True):
        etype = getattr(event, "type", None)
        if etype == "request_info":
            saw_request = True
        elif etype == "output":
            saw_output = True

    assert saw_request, "工作流必须向人请求信息"
    assert not saw_output, "工作流在收到响应之前绝不能产出 output"
