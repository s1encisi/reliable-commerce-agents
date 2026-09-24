"""
第 28 章 —— 反思与评审：测试。

- 单元测试直接检验 `parse_critique` 与各提示词构造函数 —— 不涉及 LLM。
- 智能体装配测试检验 `build_draft_agent` / `build_critic_agent` 是否产出
  名称与指令都正确的智能体。
- 回放测试回放已提交的夹具，覆盖整个 草稿 -> 评审 -> 改写 循环
  （若尚无夹具则优雅跳过）。
- 集成测试访问真实 LLM，缺少凭据时跳过。
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
    DEFAULT_PRODUCT,
    FIXTURES_DIR,
    MAX_ITERATIONS,
    WORD_LIMIT,
    CritiqueResult,
    Product,
    build_critic_agent,
    build_draft_agent,
    critic_prompt,
    draft_prompt,
    parse_critique,
    revise_prompt,
    run_reflection_loop,
)

# ─────────────────── parse_critique 单元测试（不涉及 LLM） ──────────────────


def test_parse_critique_all_pass() -> None:
    text = "PRICE: PASS\nFEATURE: PASS\nLENGTH: PASS\nFEEDBACK: none"
    critique = parse_critique(text)
    assert critique.passed
    assert critique.price_ok and critique.feature_ok and critique.length_ok
    assert critique.feedback.lower() == "none"


def test_parse_critique_some_fail() -> None:
    text = "PRICE: FAIL\nFEATURE: PASS\nLENGTH: FAIL\nFEEDBACK: missing the price and too long."
    critique = parse_critique(text)
    assert not critique.passed
    assert critique.price_ok is False
    assert critique.feature_ok is True
    assert critique.length_ok is False
    assert "missing the price" in critique.feedback


def test_parse_critique_is_case_insensitive() -> None:
    text = "price: pass\nfeature: pass\nlength: pass\nfeedback: none"
    assert parse_critique(text).passed


def test_parse_critique_treats_missing_criterion_as_fail() -> None:
    # 评审者的响应只提到三项评分中的两项 —— 被漏掉的那项绝不能默认为通过。
    text = "PRICE: PASS\nFEATURE: PASS\nFEEDBACK: forgot to grade length"
    critique = parse_critique(text)
    assert critique.price_ok is True
    assert critique.feature_ok is True
    assert critique.length_ok is False
    assert not critique.passed


def test_parse_critique_handles_completely_unparseable_text() -> None:
    critique = parse_critique("This description looks pretty good to me!")
    assert not critique.passed
    assert critique.feedback == ""


# ─────────────────── 提示词构造函数单元测试（不涉及 LLM） ──────────────────


def test_draft_prompt_includes_price_features_and_word_limit() -> None:
    prompt = draft_prompt(DEFAULT_PRODUCT)
    assert f"${DEFAULT_PRODUCT.price:.2f}" in prompt
    assert DEFAULT_PRODUCT.features[0] in prompt
    assert str(WORD_LIMIT) in prompt


def test_critic_prompt_includes_the_draft_text() -> None:
    prompt = critic_prompt(DEFAULT_PRODUCT, "Some draft text.")
    assert "Some draft text." in prompt
    assert DEFAULT_PRODUCT.name in prompt


def test_revise_prompt_folds_in_critic_feedback() -> None:
    critique = CritiqueResult(price_ok=False, feature_ok=True, length_ok=True, feedback="add the price")
    prompt = revise_prompt(DEFAULT_PRODUCT, "Old draft.", critique)
    assert "add the price" in prompt
    assert "Old draft." in prompt


# ─────────────────── 智能体装配（不发起 LLM 调用） ──────────────────


def test_draft_agent_is_named_and_instructed() -> None:
    agent = build_draft_agent(client=object())  # 该 client 不会被调用，这里只看结构
    assert agent.name == "draft-agent"
    assert "product description" in agent.default_options.get("instructions", "").lower()


def test_critic_agent_is_named_and_instructed() -> None:
    agent = build_critic_agent(client=object())
    assert agent.name == "critic-agent"
    instructions = agent.default_options.get("instructions", "")
    assert "PRICE" in instructions and "FEATURE" in instructions and "LENGTH" in instructions


def test_run_reflection_loop_respects_max_iterations_cap() -> None:
    # 一对假智能体，其中评审者永不通过 —— 以此证明循环确实会在
    # MAX_ITERATIONS 处停下，而不是无限空转，这正是本章存在的意义所在。
    # 不涉及真实 LLM：两个假对象都只是带异步 `run()` 的普通对象。
    class _Response:
        def __init__(self, text: str) -> None:
            self.text = text

    class _FakeDraftAgent:
        async def run(self, _prompt: str) -> _Response:
            return _Response("A description that never satisfies the critic.")

    class _FakeCriticAgent:
        async def run(self, _prompt: str) -> _Response:
            return _Response("PRICE: FAIL\nFEATURE: FAIL\nLENGTH: FAIL\nFEEDBACK: still wrong.")

    import asyncio

    iterations = asyncio.run(
        run_reflection_loop(_FakeDraftAgent(), _FakeCriticAgent(), DEFAULT_PRODUCT, max_iterations=MAX_ITERATIONS)
    )
    assert len(iterations) == MAX_ITERATIONS
    assert all(not it.critique.passed for it in iterations)
    assert [it.number for it in iterations] == list(range(1, MAX_ITERATIONS + 1))


def test_run_reflection_loop_stops_early_on_first_pass() -> None:
    class _Response:
        def __init__(self, text: str) -> None:
            self.text = text

    class _FakeDraftAgent:
        async def run(self, _prompt: str) -> _Response:
            return _Response("A perfectly compliant description.")

    class _FakeCriticAgent:
        async def run(self, _prompt: str) -> _Response:
            return _Response("PRICE: PASS\nFEATURE: PASS\nLENGTH: PASS\nFEEDBACK: none")

    import asyncio

    iterations = asyncio.run(
        run_reflection_loop(_FakeDraftAgent(), _FakeCriticAgent(), DEFAULT_PRODUCT, max_iterations=MAX_ITERATIONS)
    )
    assert len(iterations) == 1
    assert iterations[0].critique.passed


def test_default_product_has_expected_shape() -> None:
    assert isinstance(DEFAULT_PRODUCT, Product)
    assert DEFAULT_PRODUCT.price > 0
    assert len(DEFAULT_PRODUCT.features) >= 1


# ─────────────────── 回放测试（无需凭据，可在 CI 中运行） ────


@pytest.mark.asyncio
async def test_replay_reflection_loop_produces_a_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """回放 tests/fixtures/replay/ —— 不走网络、不需凭据。

    曾针对真实 LLM 录制过一次（即下方的 test_real_llm_reflection_loop_runs，
    以 RECORD=true 运行），随后提交入库。覆盖整个循环，而不只是一次调用 ——
    一段通过的草稿可能需要一轮 LLM（起草）加一轮评审，或者若录制的评审者
    判第一版草稿不通过，则各需要若干轮。
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"{FIXTURES_DIR} 中没有已录制的夹具 —— 请先以 RECORD=true 运行")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    draft_agent = build_draft_agent()
    critic_agent = build_critic_agent()
    iterations = await run_reflection_loop(draft_agent, critic_agent, DEFAULT_PRODUCT)
    assert len(iterations) >= 1
    assert len(iterations) <= MAX_ITERATIONS
    # 每一轮录制结果都必须带有真实草稿和可解析的评审结果。
    for iteration in iterations:
        assert iteration.draft.strip()
        assert isinstance(iteration.critique, CritiqueResult)


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
async def test_real_llm_reflection_loop_runs() -> None:
    """循环必须终止（通过或撞上上限），且绝不超过 MAX_ITERATIONS。"""
    draft_agent = build_draft_agent()
    critic_agent = build_critic_agent()
    iterations = await run_reflection_loop(draft_agent, critic_agent, DEFAULT_PRODUCT)
    assert 1 <= len(iterations) <= MAX_ITERATIONS
    assert iterations[-1].critique.passed or len(iterations) == MAX_ITERATIONS


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_real_llm_final_draft_mentions_price_when_passed() -> None:
    """当循环报告通过时，最终草稿应当确实包含价格。"""
    draft_agent = build_draft_agent()
    critic_agent = build_critic_agent()
    iterations = await run_reflection_loop(draft_agent, critic_agent, DEFAULT_PRODUCT)
    final = iterations[-1]
    if final.critique.passed:
        assert f"{DEFAULT_PRODUCT.price:.2f}" in final.draft
