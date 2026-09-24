"""
第 10 章 —— 工作流事件与构建器：测试。

不涉及 LLM —— 所有断言都跑在事件流上。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import ProgressPayload, build_workflow, run_with_events  # noqa: E402


@pytest.mark.asyncio
async def test_progress_events_emit_in_order() -> None:
    progress, _ = await run_with_events("ord-8842")
    assert [p.step for p in progress] == ["normalize-order", "validate-order", "log-order"]
    assert [p.percent for p in progress] == [33, 66, 100]


@pytest.mark.asyncio
async def test_progress_events_carry_custom_payload() -> None:
    progress, _ = await run_with_events("ord-1")
    assert all(isinstance(p, ProgressPayload) for p in progress)


@pytest.mark.asyncio
async def test_short_circuit_stops_at_validate_before_log_progress() -> None:
    progress, outputs = await run_with_events("")
    steps = [p.step for p in progress]
    assert "normalize-order" in steps
    assert "validate-order" in steps
    assert "log-order" not in steps, "log-order must not run when validate short-circuits"
    assert outputs == ["[rejected: empty order id]"]


@pytest.mark.asyncio
async def test_workflow_output_accompanies_progress() -> None:
    progress, outputs = await run_with_events("hi")
    assert outputs == ["ORDER LOGGED: HI"]
    assert progress[-1].percent == 100


@pytest.mark.asyncio
async def test_event_stream_yields_incrementally() -> None:
    """进度事件应当在最终输出之前到达，而不是攒成一批。

    按载荷形状分桶，而不是按工作流的 type='output' /
    type='intermediate' 标签 —— 见 main.py 中 run_with_events 的文档字符串。
    """
    workflow = build_workflow()
    order: list[str] = []
    async for event in workflow.run("ord-stream-test", stream=True):
        etype = getattr(event, "type", None)
        if etype not in ("output", "intermediate"):
            continue
        data = getattr(event, "data", None)
        if isinstance(data, ProgressPayload):
            order.append(f"progress:{data.step}")
        else:
            order.append("output")
    # 至少，输出必须出现在最后一条进度事件之后。
    assert order[-1] == "output"
    assert order.index("progress:log-order") < order.index("output")
