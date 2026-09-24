"""
第 26 章 —— 智能体评估：测试。

- 单元测试直接检验目录工具与两个打分器（不涉及 LLM）。
- 智能体装配测试检验 `search_catalog` 已注册到智能体上。
- 回放测试回放已提交的夹具，覆盖完整的评估循环。
- 集成测试访问真实 LLM，并断言确定性打分的行为。
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
    EVAL_CASES,
    FIXTURES_DIR,
    ask,
    build_agent,
    judge_response_stub,
    run_eval_suite,
    score_deterministic,
    search_catalog,
)

# ─────────────────── 单元测试：目录工具（不涉及 LLM） ──────────────────


def test_search_catalog_returns_known_product() -> None:
    result = search_catalog.func("Wireless Mouse")  # @tool 通过 __wrapped__ 暴露原函数
    assert "24.99" in result and "42" in result and "in stock" in result


def test_search_catalog_flags_out_of_stock() -> None:
    result = search_catalog.func("USB-C Hub")
    assert "out of stock" in result


def test_search_catalog_handles_unknown_product() -> None:
    result = search_catalog.func("Quantum Toaster")
    assert "No catalog entry" in result


def test_search_catalog_is_case_insensitive() -> None:
    assert search_catalog.func("wireless mouse") == search_catalog.func("WIRELESS MOUSE")


# ─────────────────── 单元测试：确定性打分器（不涉及 LLM） ──────────────────


def test_score_deterministic_full_match() -> None:
    result = score_deterministic("The Wireless Mouse is $24.99.", ["24.99"])
    assert result.score == 1.0
    assert result.missing == []


def test_score_deterministic_partial_match() -> None:
    result = score_deterministic("It costs $19.99.", ["19.99", "120"])
    assert result.score == 0.5
    assert result.missing == ["120"]


def test_score_deterministic_no_match() -> None:
    result = score_deterministic("I'm not sure.", ["24.99"])
    assert result.score == 0.0
    assert result.missing == ["24.99"]


def test_score_deterministic_no_expected_facts_scores_perfect() -> None:
    # 一个没有可核查事实的案例并非「无据」—— 它只是什么都没断言。
    result = score_deterministic("Hello!", [])
    assert result.score == 1.0


# ─────────────────── 单元测试：评审桩（不涉及 LLM） ──────────────────


def test_judge_response_stub_full_coverage() -> None:
    verdict = judge_response_stub("q", "It costs $24.99.", ["24.99"])
    assert verdict.score == 1.0
    assert verdict.failure_mode is None


def test_judge_response_stub_zero_coverage() -> None:
    verdict = judge_response_stub("q", "No idea.", ["24.99"])
    assert verdict.score == 0.0
    assert verdict.failure_mode == "missing_field"


def test_judge_response_stub_partial_coverage() -> None:
    verdict = judge_response_stub("q", "It's $19.99.", ["19.99", "120"])
    assert verdict.score == 0.5
    assert verdict.failure_mode == "partial_coverage"


# ─────────────────── 智能体装配 ──────────────────


def test_agent_has_search_catalog_tool_registered() -> None:
    agent = build_agent(client=object())  # 该 client 不会被调用，这里只看结构
    tool_names = [getattr(t, "name", None) for t in agent.default_options.get("tools") or []]
    assert "search_catalog" in tool_names


def test_eval_cases_each_have_checkable_facts() -> None:
    # 好的评估案例 = 提示词 + 可核查事实，而不只是「听起来对不对」。
    for case in EVAL_CASES:
        assert case.prompt
        assert len(case.expected_facts) >= 1


# ─────────────────── 回放测试（无需凭据，可在 CI 中运行） ────


@pytest.mark.asyncio
async def test_replay_runs_full_eval_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    """回放 tests/fixtures/replay/ —— 不走网络、不需凭据。

    曾针对真实 LLM 录制过一次（即下方的 test_real_llm_scores_all_cases_grounded，
    以 RECORD=true 运行），随后提交入库。
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"{FIXTURES_DIR} 中没有已录制的夹具 —— 请先以 RECORD=true 运行")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    agent = build_agent()
    results = await run_eval_suite(agent)
    assert len(results) == len(EVAL_CASES)
    for r in results:
        assert 0.0 <= r["det_score"] <= 1.0


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
async def test_real_llm_scores_all_cases_grounded() -> None:
    """LLM 应当调用 search_catalog，并透出精确的价格 / 库存事实。"""
    agent = build_agent()
    results = await run_eval_suite(agent)
    failing = [r["case_id"] for r in results if r["det_score"] < 1.0]
    assert not failing, f"以下案例缺少期望事实：{failing}"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_real_llm_answers_unrelated_question_without_catalog_data() -> None:
    """一个没有任何东西可查的问题，不应泄漏预置的目录数字。"""
    agent = build_agent()
    answer = await ask(agent, "What is the capital of France? Answer with only the city name.")
    assert "paris" in answer.lower()
    assert "24.99" not in answer
