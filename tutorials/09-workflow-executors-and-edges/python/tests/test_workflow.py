"""
第 09 章 —— 工作流执行器与边：测试。

不涉及 LLM —— 工作流逻辑是确定性的，因此我们可以精确断言事件流与最终输出。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import build_workflow, run  # noqa: E402


@pytest.mark.asyncio
async def test_happy_path_pipeline_returns_normalized_logged_output() -> None:
    outputs = await run(" ord-8842 ")
    assert outputs == ["ORDER LOGGED: ORD-8842"]


@pytest.mark.asyncio
async def test_empty_order_id_short_circuits_at_validate_executor() -> None:
    outputs = await run("")
    assert outputs == ["[rejected: empty order id]"]


@pytest.mark.asyncio
async def test_whitespace_only_order_id_treated_as_empty() -> None:
    outputs = await run("   ")
    assert outputs == ["[rejected: empty order id]"]


@pytest.mark.asyncio
async def test_workflow_wires_executors_and_edges() -> None:
    workflow = build_workflow()
    executors = workflow.get_executors_list()
    ids = {getattr(e, "id", None) for e in executors}
    assert {"normalize-order", "validate-order", "log-order"} <= ids


@pytest.mark.asyncio
async def test_event_stream_reports_executor_invocations_in_order() -> None:
    workflow = build_workflow()
    # 通过把异步生成器耗尽，把每一个事件都实体化出来。
    stream = workflow.run("ord-1234", stream=True)
    events = [event async for event in stream]
    invoked = [
        getattr(e, "executor_id", "")
        for e in events
        if getattr(e, "type", None) == "executor_invoked"
    ]
    assert invoked.index("normalize-order") < invoked.index("validate-order")
    assert invoked.index("validate-order") < invoked.index("log-order")
