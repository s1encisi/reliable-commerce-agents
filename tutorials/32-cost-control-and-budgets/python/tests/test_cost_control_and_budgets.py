"""
第 32 章 —— 成本控制与预算：测试。

- 单元测试直接检验工具函数与 `CostBudgetChatMiddleware`（不涉及 LLM）——
  其形态乃至部分断言都对应真实的 `agents/python/tests/test_cost_budget.py`，
  只是针对本章这个简化版、非 ContextVar 的实现。
- 回放测试回放一份已提交的夹具（不走网络、不需凭据）。
- 集成测试访问真实 LLM，缺少可用凭据时跳过。
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
    BUDGET_REFUSAL_MESSAGE,
    FIXTURES_DIR,
    CostBudgetChatMiddleware,
    ask,
    build_agent,
    estimate_cost_usd,
    get_product_price,
)

# ─────────────────── 工具单元测试（不涉及 LLM） ──────────────────


def test_price_tool_returns_canned_data() -> None:
    result = get_product_price.func("P-100")  # @tool 通过 .func 暴露原函数
    assert result == "$129.99"


def test_price_tool_handles_unknown_product() -> None:
    result = get_product_price.func("P-999")
    assert "No price found" in result


def test_price_tool_is_case_insensitive() -> None:
    assert get_product_price.func("p-100") == get_product_price.func("P-100")


def test_agent_has_price_tool_and_budget_middleware_registered() -> None:
    mw = CostBudgetChatMiddleware(budget_usd=0.01)
    agent = build_agent(mw, client=object())  # 该 client 不会被调用，这里只看结构
    tool_names = [getattr(t, "name", None) for t in agent.default_options.get("tools") or []]
    assert "get_product_price" in tool_names
    assert mw in (agent.middleware or [])


# ─────────────────── 中间件单元测试（不涉及 LLM） ─────────────


class _FakeResponse:
    """鸭子类型的 ChatResponse：这里只有 `usage_details` 和 `.text` 有意义。"""

    def __init__(self, tokens_in: int, tokens_out: int) -> None:
        self.usage_details = {"input_token_count": tokens_in, "output_token_count": tokens_out}
        self.text = "ok"


class _FakeChatContext:
    def __init__(self) -> None:
        self.result: object | None = None


async def _run_turn(mw: CostBudgetChatMiddleware, ctx: _FakeChatContext, response: _FakeResponse) -> dict:
    calls = {"count": 0}

    async def _call_next() -> None:
        calls["count"] += 1
        ctx.result = response

    await mw.process(ctx, _call_next)
    return calls


async def test_cost_accumulates_across_turns() -> None:
    mw = CostBudgetChatMiddleware(budget_usd=1.0, mode="observe")
    ctx = _FakeChatContext()

    await _run_turn(mw, ctx, _FakeResponse(1000, 1000))
    await _run_turn(mw, ctx, _FakeResponse(500, 500))

    expected = estimate_cost_usd(1000, 1000) + estimate_cost_usd(500, 500)
    assert mw.total_cost_usd == pytest.approx(expected)
    assert mw.turns_recorded == 2


async def test_observe_mode_never_blocks_even_over_budget() -> None:
    mw = CostBudgetChatMiddleware(budget_usd=0.000001, mode="observe")  # 小到微不足道
    ctx = _FakeChatContext()

    for _ in range(5):
        calls = await _run_turn(mw, ctx, _FakeResponse(1000, 1000))
        assert calls["count"] == 1

    assert mw.blocked == 0
    assert mw.turns_recorded == 5
    assert mw.total_cost_usd > mw.budget_usd


async def test_off_mode_skips_tracking_entirely() -> None:
    mw = CostBudgetChatMiddleware(budget_usd=0.000001, mode="off")
    ctx = _FakeChatContext()

    calls = await _run_turn(mw, ctx, _FakeResponse(1000, 1000))

    assert calls["count"] == 1, "off 模式仍必须放行 —— 它只是不追踪"
    assert mw.total_cost_usd == 0.0
    assert mw.turns_recorded == 0


async def test_enforce_mode_blocks_once_ceiling_exceeded() -> None:
    per_turn = estimate_cost_usd(1000, 1000)
    # 预算刚好够一轮的量（第 1 轮之后的运行总计 == 预算，因此尚未「超过」），
    # 但不够第二轮的（第 2 轮之后的运行总计 > 预算，因此第 3 轮被拒绝）。
    mw = CostBudgetChatMiddleware(budget_usd=per_turn, mode="enforce")
    ctx = _FakeChatContext()

    first_calls = await _run_turn(mw, ctx, _FakeResponse(1000, 1000))
    assert first_calls["count"] == 1, "第一轮必须放行 —— 此时还没花任何钱"
    assert mw.blocked == 0

    second_calls = await _run_turn(mw, ctx, _FakeResponse(1000, 1000))
    assert second_calls["count"] == 1, "运行总计（== 预算）尚未「超过」"
    assert mw.blocked == 0

    third_calls = await _run_turn(mw, ctx, _FakeResponse(1000, 1000))
    assert third_calls["count"] == 0, "第三轮必须在 call_next() 之前就被拒绝"
    assert mw.blocked == 1
    assert ctx.result is not None
    assert BUDGET_REFUSAL_MESSAGE in ctx.result.text


async def test_enforce_mode_without_budget_headroom_never_lets_a_free_turn_through_twice() -> None:
    """预算为 0 且尚未有任何花费时，仍会且只会放行第一轮（0 不大于 0），
    此后每一轮都被拦截 —— 这正是真实中间件所记录的「滞后一轮」权衡。"""
    mw = CostBudgetChatMiddleware(budget_usd=0.0, mode="enforce")
    ctx = _FakeChatContext()

    first_calls = await _run_turn(mw, ctx, _FakeResponse(10, 10))
    assert first_calls["count"] == 1

    second_calls = await _run_turn(mw, ctx, _FakeResponse(10, 10))
    assert second_calls["count"] == 0
    assert mw.blocked == 1


# ─────────────────── 回放测试（无需凭据，可在 CI 中运行） ────


@pytest.mark.asyncio
async def test_replay_invokes_price_tool_and_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """回放 tests/fixtures/replay/ —— 不走网络、不需凭据。

    曾针对真实的 Azure OpenAI 录制过一次（RECORD=true），随后提交入库。

    注意：`ReplayChatClient` 刻意跳过 `ChatMiddlewareLayer`（见 main.py 的
    模块文档字符串以及 `tutorials/_shared/replay_client.py`），因此在回放
    模式下 `CostBudgetChatMiddleware.process()` 永远不会执行 —— 这里只能
    证明工具调用往返被正确回放。预算上限本身只有在真实 LLM 下才可观察
    （见下方的 `@pytest.mark.integration` 测试），这与第 06 章对其 chat
    中间件所采用的划分方式一致。
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"{FIXTURES_DIR} 中没有已录制的夹具 —— 请先以 RECORD=true 运行")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    mw = CostBudgetChatMiddleware(budget_usd=0.0015, mode="enforce")
    agent = build_agent(mw)
    answer = await ask(agent, "What's the price of product P-100?")
    assert "129.99" in answer


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
async def test_real_llm_accumulates_cost_and_eventually_refuses() -> None:
    """面对真实 LLM：在极小的预算下问足够多的问题，必须触发 enforce 模式。"""
    mw = CostBudgetChatMiddleware(budget_usd=0.0015, mode="enforce")
    agent = build_agent(mw)
    questions = [
        "What's the price of product P-100?",
        "What's the price of product P-200?",
        "What's the price of product P-300?",
    ]
    answers = [await ask(agent, q) for q in questions]

    assert mw.turns_recorded >= 1
    assert mw.total_cost_usd > 0.0
    # 在如此小的预算下，后面至少有一个回答应当是被拒绝，而不是一次真实的价格查询。
    assert mw.blocked >= 1 or any(BUDGET_REFUSAL_MESSAGE in a for a in answers)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_real_llm_observe_mode_never_blocks() -> None:
    """observe 模式无论超预算多少，都绝不能拒绝任何一轮。"""
    mw = CostBudgetChatMiddleware(budget_usd=0.0000001, mode="observe")
    agent = build_agent(mw)
    answer = await ask(agent, "What's the price of product P-100?")

    assert BUDGET_REFUSAL_MESSAGE not in answer
    assert mw.blocked == 0
    assert mw.total_cost_usd > mw.budget_usd
