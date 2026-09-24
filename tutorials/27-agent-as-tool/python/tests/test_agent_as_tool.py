"""
第 27 章 —— 把智能体作为工具：测试。

- 单元测试直接演练那两个普通工具函数（无 LLM）。
- 一个智能体接线测试确认协调者的 tools=[...] 中同时包含被包装的
  product-lookup FunctionTool 与那个普通本地工具。
- 一个回放测试播放已提交的夹具（若尚不存在则优雅跳过）。
- 集成测试访问真实 LLM，并断言协调者在被包装的智能体回答之后仍握有控制权 ——
  即它继续调用第二个工具并合并两个结果，而不是被包装的智能体接管发言权。
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import (  # noqa: E402
    FIXTURES_DIR,
    ask,
    build_agent,
    build_product_lookup_agent,
    calculate_discount,
    search_catalog,
)

# ─────────────────── 工具函数单元测试 ───────────────────


def test_search_catalog_returns_known_product() -> None:
    result = search_catalog.func("Wireless Headphones")  # @tool 通过 .func 暴露原始函数
    assert "149.99" in result and "Electronics" in result


def test_search_catalog_handles_unknown_product() -> None:
    result = search_catalog.func("Time Machine")
    assert "No catalog entry" in result


def test_calculate_discount_computes_expected_price() -> None:
    result = calculate_discount.func(149.99, 20)
    assert "119.99" in result


# ─────────────────── 智能体接线测试（不调用 LLM） ────────────


def test_coordinator_has_wrapped_agent_and_local_tool_registered() -> None:
    coordinator = build_agent(client=object())  # client 不会被调用；我们只检查结构
    tools = coordinator.default_options.get("tools") or []
    tool_names = [getattr(t, "name", None) for t in tools]
    assert "product_lookup" in tool_names
    assert "calculate_discount" in tool_names


def test_product_lookup_agent_has_search_catalog_registered() -> None:
    specialist = build_product_lookup_agent(client=object())
    tool_names = [getattr(t, "name", None) for t in specialist.default_options.get("tools") or []]
    assert "search_catalog" in tool_names


def test_as_tool_wraps_a_function_tool_not_the_raw_agent() -> None:
    """本章的全部要点：`.as_tool()` 返回的是一个 FunctionTool，
    一个普通的可调用能力 —— 而不是指向子智能体本身的活句柄。"""
    specialist = build_product_lookup_agent(client=object())
    wrapped = specialist.as_tool(name="product_lookup")
    assert wrapped.name == "product_lookup"
    assert hasattr(wrapped, "func") or callable(wrapped)


# ─────────────────── 回放测试（无需凭据，可在 CI 中运行） ───


@pytest.mark.asyncio
async def test_replay_coordinator_combines_lookup_and_discount(monkeypatch: pytest.MonkeyPatch) -> None:
    """播放 tests/fixtures/replay/ —— 无需网络，无需凭据。

    针对真实 LLM 录制过一次（即下面以 RECORD=true 运行的
    test_real_llm_coordinator_keeps_control），然后提交进仓库。
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"no recorded fixtures in {FIXTURES_DIR} — run with RECORD=true first")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    agent = build_agent()
    answer = await ask(agent, "Look up the Wireless Headphones, then tell me the price after a 20% discount.")
    lowered = answer.lower()
    assert "119.99" in lowered or "headphones" in lowered, f"expected combined answer, got: {answer!r}"


# ─────────────────── 真实 LLM 集成测试 ──────────────────


def _llm_available() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        return bool(
            os.environ.get("AZURE_OPENAI_ENDPOINT")
            and (os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))
        )
    key = os.environ.get("OPENAI_API_KEY", "")
    return bool(key) and not key.startswith("sk-your-")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_coordinator_keeps_control_after_wrapped_agent_answers() -> None:
    """协调者必须调用 product_lookup、拿回一个答案，然后继续自行调用
    calculate_discount —— 这证明控制权自动回到了协调者手中，而不是由
    子智能体接管了本轮。"""
    agent = build_agent()
    answer = await ask(agent, "Look up the Wireless Headphones, then tell me the price after a 20% discount.")
    lowered = answer.lower()
    assert "119.99" in lowered
    assert "headphone" in lowered


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_product_lookup_alone_reports_catalog_data() -> None:
    """只需被包装智能体的问题，仍然能经由它得到解答。"""
    agent = build_agent()
    answer = await ask(agent, "What's the stock level on the Coffee Maker?")
    lowered = answer.lower()
    assert "coffee maker" in lowered
    assert "0" in lowered or "out of stock" in lowered or "no stock" in lowered
