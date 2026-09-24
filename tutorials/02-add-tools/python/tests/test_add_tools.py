"""
第 02 章 —— 添加工具：测试。

- 单元测试直接检验工具函数（不涉及 LLM）。
- 集成测试访问真实 LLM，并断言工具调用的行为。
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
from main import FIXTURES_DIR, ask, build_agent, get_product_price  # noqa: E402

# ─────────────────── 工具函数单元测试 ──────────────────


def test_product_price_tool_returns_canned_data() -> None:
    result = get_product_price.func("SKU-001")  # @tool 通过 __wrapped__ 暴露原函数
    assert "79.99" in result and "Wireless Mouse" in result


def test_product_price_tool_handles_unknown_sku() -> None:
    result = get_product_price.func("SKU-999")
    assert "No pricing data" in result


def test_product_price_tool_is_case_insensitive() -> None:
    assert get_product_price.func("sku-001") == get_product_price.func("SKU-001")


def test_agent_has_product_price_tool_registered() -> None:
    agent = build_agent(client=object())  # 该 client 不会被调用，这里只看结构
    tool_names = [getattr(t, "name", None) for t in agent.default_options.get("tools") or []]
    assert "get_product_price" in tool_names


# ─────────────────── 回放测试（无需凭据，可在 CI 中运行） ────


@pytest.mark.asyncio
async def test_replay_invokes_product_price_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """回放 tests/fixtures/replay/ —— 不走网络、不需凭据。

    曾针对真实 LLM 录制过一次（即下方的
    test_real_llm_invokes_product_price_tool，以 RECORD=true 运行），随后提交入库。
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"{FIXTURES_DIR} 中没有已录制的夹具 —— 请先以 RECORD=true 运行")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    agent = build_agent()
    answer = await ask(agent, "What's the price of SKU-001?")
    lowered = answer.lower()
    assert "79.99" in lowered or "wireless mouse" in lowered, (
        f"期望答案中出现商品价格工具的数据，实际为：{answer!r}"
    )


# ─────────────────── 真实 LLM 集成测试 ────────────────


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
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_real_llm_invokes_product_price_tool() -> None:
    """当被问到商品价格时，LLM 应当看到该工具并使用它。"""
    agent = build_agent()
    answer = await ask(agent, "What's the price of SKU-001?")
    # 预置回答里埋了 "79.99" / "Wireless Mouse" —— 若 LLM 调用了工具，其中之一会出现。
    lowered = answer.lower()
    assert "79.99" in lowered or "wireless mouse" in lowered, (
        f"期望答案中出现商品价格工具的数据，实际为：{answer!r}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_real_llm_skips_tool_for_unrelated_question() -> None:
    """对于地理类问题，商品价格工具绝不能出现在答案里。"""
    agent = build_agent()
    answer = await ask(agent, "What is the capital of France? Answer with only the city name.")
    assert "paris" in answer.lower()
    # 预置的价格字符串绝不能渗进一个与价格无关的答案。
    assert "79.99" not in answer.lower()
