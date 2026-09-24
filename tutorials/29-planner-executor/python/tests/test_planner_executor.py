"""
第 29 章 —— 规划器-执行器：测试。

- 单元测试直接检验目录检索工具与 Plan/PlanStep 模型（不涉及 LLM）。
- 装配测试检验两个智能体是否装配正确（不发起 LLM 调用）。
- 回放测试回放已提交的夹具，覆盖完整的「先规划后执行」流程。
- 集成测试访问真实 LLM，端到端断言规划器 + 执行器的行为。
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
    Plan,
    PlanStep,
    build_executor_agent,
    build_planner_agent,
    make_plan,
    run_plan,
    search_products,
)

GIFT_REQUEST = "Help me put together a birthday gift for someone who likes photography, under $200."

# ─────────────────── 工具函数单元测试（不涉及 LLM） ──────────────────


def test_search_products_matches_by_keyword() -> None:
    result = search_products.func("photography")
    assert "Compact Mirrorless Camera" in result
    assert "50mm Prime Lens" in result


def test_search_products_applies_price_ceiling() -> None:
    result = search_products.func("photography", 50.0)
    assert "Travel Camera Tripod" in result
    assert "Professional Studio Light Kit" not in result  # 349 美元，超过上限


def test_search_products_handles_no_matches() -> None:
    result = search_products.func("skateboard")
    assert "No products found" in result


# ─────────────────── Plan / PlanStep 模型单元测试（不涉及 LLM） ──────────


def test_plan_step_defaults_query_to_none() -> None:
    step = PlanStep(step=1, action="Summarize the results")
    assert step.query is None


def test_plan_orders_steps() -> None:
    plan = Plan(
        goal="Find a photography gift under $200",
        steps=[
            PlanStep(step=1, action="Search for photography products", query="photography"),
            PlanStep(step=2, action="Filter by price"),
            PlanStep(step=3, action="Summarize a recommendation"),
        ],
    )
    assert [s.step for s in plan.steps] == [1, 2, 3]
    assert plan.steps[0].query == "photography"
    assert plan.steps[1].query is None


# ─────────────────── 智能体装配（不发起 LLM 调用） ────────────────────


def test_executor_agent_has_search_products_tool_registered() -> None:
    agent = build_executor_agent(client=object())  # 该 client 不会被调用，这里只看结构
    tool_names = [getattr(t, "name", None) for t in agent.default_options.get("tools") or []]
    assert "search_products" in tool_names


def test_planner_agent_builds_without_tools() -> None:
    agent = build_planner_agent(client=object())
    tools = agent.default_options.get("tools")
    assert not tools  # 规划器只产出结构化的 Plan —— 它自己不调用工具


# ─────────────────── 回放测试（无需凭据，可在 CI 中运行） ───────────


@pytest.mark.asyncio
async def test_replay_plans_and_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    """回放 tests/fixtures/replay/ —— 不走网络、不需凭据。

    曾针对真实 LLM 录制过一次（即下方的 test_real_llm_produces_ordered_plan，
    以 RECORD=true 运行），随后提交入库。一次规划器调用，加上每个计划步骤
    一次执行器调用，意味着会有多个夹具文件，而不是一个。
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"{FIXTURES_DIR} 中没有已录制的夹具 —— 请先以 RECORD=true 运行")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    plan, results = await run_plan(GIFT_REQUEST)
    assert plan.steps, "计划必须至少包含一个步骤"
    assert len(results) == len(plan.steps)
    assert all(results), "每一步都必须产出非空结果"


# ─────────────────── 真实 LLM 集成测试 ─────────────────────────


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
async def test_real_llm_produces_ordered_plan() -> None:
    """规划器应返回带多个、按顺序编号步骤的结构化 Plan。"""
    planner = build_planner_agent()
    plan = await make_plan(planner, GIFT_REQUEST)
    assert len(plan.steps) >= 2
    assert [s.step for s in plan.steps] == list(range(1, len(plan.steps) + 1))


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_real_llm_executes_every_step() -> None:
    """每个计划步骤都应产出非空的执行器结果，且其中包含目录数据。"""
    plan, results = await run_plan(GIFT_REQUEST)
    assert len(results) == len(plan.steps)
    assert all(r.strip() for r in results)
    # 至少有一个步骤的结果应当透出目录数据（一个美元金额）。
    assert any("$" in r for r in results)
