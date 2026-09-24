"""群聊编排模式测试。

注入同步和异步讨论者验证事件流与完成契约；另检查默认生产配置
会构建预期参与者，但不实际调用模型。
"""

from __future__ import annotations

import pytest

from orchestrator.modes.base import RunContext
from orchestrator.modes.group_chat_mode import GroupChatMode, _format_transcript


def _value(_q: str, _t: list[dict[str, str]]) -> str:
    return "Great price for the feature set."


async def _quality(_q: str, transcript: list[dict[str, str]]) -> str:
    return f"Saw {len(transcript)} prior turn(s); build quality is excellent."


@pytest.mark.asyncio
async def test_group_chat_mode_runs_panelists_in_order_then_moderator() -> None:
    mode = GroupChatMode(panelists=[("value", _value), ("quality", _quality)])
    events = [e async for e in mode.run("Is the Sony WH-1000XM5 worth it?", RunContext(history=[]))]

    final = events[-1]
    assert final.kind == "run_completed"
    assert final.payload["agents_involved"] == ["value", "quality", "moderator"]
    assert [t["speaker"] for t in final.payload["transcript"]] == ["value", "quality"]
    assert "Saw 1 prior turn" in final.payload["transcript"][1]["text"]
    assert final.payload["text"]  # 主持人结论非空。


@pytest.mark.asyncio
async def test_group_chat_mode_supports_async_panelists() -> None:
    """真实讨论者调用模型是异步的，Responder 必须等待异步结果。"""
    mode = GroupChatMode(panelists=[("quality", _quality)])
    events = [e async for e in mode.run("worth it?", RunContext(history=[]))]

    final = events[-1]
    assert final.payload["transcript"][0]["text"] == "Saw 0 prior turn(s); build quality is excellent."


@pytest.mark.asyncio
async def test_group_chat_mode_emits_node_events() -> None:
    mode = GroupChatMode(panelists=[("value", _value)])
    events = [e async for e in mode.run("worth it?", RunContext(history=[]))]

    assert any(e.kind == "node_enter" for e in events)
    assert any(e.kind == "node_exit" for e in events)


def test_group_chat_mode_graph_mermaid_reflects_panel_order() -> None:
    mode = GroupChatMode(panelists=[("value", _value), ("quality", _quality)])
    graph = mode.graph_mermaid()
    assert graph is not None
    assert "panelist_value" in graph
    assert "panelist_quality" in graph
    assert "moderator" in graph
    # 边列表中 value 应先于 quality，发言顺序有意义。
    assert graph.index("panelist_value") < graph.index("panelist_quality")


def test_group_chat_mode_default_panel_has_value_and_quality_names() -> None:
    """只检查默认生产接线，不调用需要模型服务的参与者。"""
    mode = GroupChatMode()
    panelists = mode._resolve_panelists()
    names = [name for name, _ in panelists]
    assert names == ["value", "quality"]
    assert all(callable(responder) for _, responder in panelists)


def test_format_transcript_handles_empty_and_populated() -> None:
    assert _format_transcript([]) == "(no prior turns)"
    formatted = _format_transcript([{"speaker": "value", "text": "cheap"}])
    assert formatted == "value: cheap"
