"""
MAF v1 — 第 26 章：智能体评估（Python）

一个极小的独立评估循环：把若干 {prompt, expected_facts} 案例，针对一个
运行在内存商品目录之上的小型电商问答智能体跑一遍，每个案例用两种方式打分 ——
一个确定性的「期望事实是否出现」检查，和一个结构化输出的评审桩。
最后打印一张通过 / 未通过的记分卡。

本章的演示智能体刻意做成玩具规模；它所对照的真实评估框架是
`agents/python/evals/harness.py`，后者把案例跑过真实的生产代码路径
（`orchestrator.modes` / 专家的 A2A 入口），而不是一个手搓的循环 ——
这一区别为何在本章重要，见 README。

运行：
    source agents/.venv/bin/activate
    python tutorials/26-evals/python/main.py
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Annotated, Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, tool  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

INSTRUCTIONS = (
    "You are a shopping assistant for a small electronics store. "
    "When the user asks about a product's price, stock, or availability, call the "
    "`search_catalog` tool with the product name and answer using the exact numbers it returns. "
    "Never guess a price or stock count. For anything else, answer directly in one short sentence."
)

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"

# ─────────────────── 玩具级目录 + 工具 ──────────────────

CATALOG: dict[str, dict[str, Any]] = {
    "wireless mouse": {"price": 24.99, "stock": 42},
    "mechanical keyboard": {"price": 89.99, "stock": 15},
    "usb-c hub": {"price": 34.50, "stock": 0},
    "noise-cancelling headphones": {"price": 149.99, "stock": 8},
    "portable charger": {"price": 19.99, "stock": 120},
}


@tool(name="search_catalog", description="Look up the price and stock count for a product in the catalog by name.")
def search_catalog(
    product_name: Annotated[str, Field(description="The product name to look up, e.g. 'Wireless Mouse'.")],
) -> str:
    item = CATALOG.get(product_name.strip().lower())
    if item is None:
        return f"No catalog entry for '{product_name}'."
    availability = "in stock" if item["stock"] > 0 else "out of stock"
    return f"{product_name.title()}: ${item['price']:.2f}, {item['stock']} units ({availability})."


# ─────────────────── 评估案例 ──────────────────


@dataclass
class EvalCase:
    case_id: str
    prompt: str
    # 正确回答中必须出现（忽略大小写）的子串。这就是一个好的评估案例所需的
    # 「可核查事实」—— 不是「听起来合理吗」，而是脚本能 grep 的一个具体字符串。
    expected_facts: list[str]


EVAL_CASES: list[EvalCase] = [
    EvalCase("mouse-price", "How much does the Wireless Mouse cost?", ["24.99"]),
    EvalCase("keyboard-stock", "How many Mechanical Keyboards are in stock?", ["15"]),
    EvalCase("hub-out-of-stock", "Is the USB-C Hub in stock?", ["out of stock"]),
    EvalCase("headphones-price", "What does the Noise-Cancelling Headphones cost?", ["149.99"]),
    EvalCase(
        "charger-price-and-stock",
        "Give me the price and stock count for the Portable Charger.",
        ["19.99", "120"],
    ),
]


# ─────────────────── 打分：确定性档 ──────────────────


@dataclass
class DeterministicResult:
    score: float
    found: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def score_deterministic(response_text: str, expected_facts: list[str]) -> DeterministicResult:
    """廉价、精确、对 CI 友好：每条期望事实是否真的出现在响应里？

    与真实的 `evals/scorers/db_groundedness.py` 形状相同 —— 通过 / 总数之比，
    由机械检查算出，不调用 LLM，没有歧义。它只能检查机械上可检查的东西
    （一个价格字符串、一个库存数字）—— 对于数字周围的文字写得好不好，
    它什么也说不了。
    """
    lowered = response_text.lower()
    found = [fact for fact in expected_facts if fact.lower() in lowered]
    missing = [fact for fact in expected_facts if fact not in found]
    score = len(found) / len(expected_facts) if expected_facts else 1.0
    return DeterministicResult(score=score, found=found, missing=missing)


# ─────────────────── 打分：LLM 评审档（桩） ──────────────────


class JudgeVerdict(BaseModel):
    """与真实的 `evals/scorers/llm_judge.py::JudgeVerdict` 同样的结构化输出形状
    （score、reasoning、failure_mode）—— 评审者的响应会被解析进这个 Pydantic 模型，
    而不是得到一段自由文本评分。
    """

    score: float
    reasoning: str
    failure_mode: str | None = None


def judge_response_stub(prompt: str, response_text: str, expected_facts: list[str]) -> JudgeVerdict:
    """替代第二次 LLM 调用，用于评判相关性与完整性。

    真实的 `evals/scorers/llm_judge.py::judge_response()`（第 57 行）会把问题、
    期望字段与响应发给第二个模型，再解析回一个 `JudgeVerdict`。对于夹具固定、
    以回放为主的教学演示而言，每个评估案例都额外花一次真实 LLM 调用
    （还是在智能体自身那次调用之外）并不划算，所以这个桩用一个廉价的启发式
    复现同样的输出*形状* —— 带 reasoning 字符串的结构化判定 —— 而不调用模型。
    把这个函数的函数体换成真实的 `judge.run(...)` 调用，评估循环里其它任何地方
    都不用改。
    """
    covered = sum(1 for fact in expected_facts if fact.lower() in response_text.lower())
    total = len(expected_facts) or 1
    score = covered / total
    if score == 1.0:
        reasoning = "响应覆盖了每一条期望事实。"
        failure_mode = None
    elif score == 0.0:
        reasoning = "响应未覆盖任何期望事实。"
        failure_mode = "missing_field"
    else:
        reasoning = f"响应覆盖了 {covered}/{total} 条期望事实。"
        failure_mode = "partial_coverage"
    return JudgeVerdict(score=score, reasoning=reasoning, failure_mode=failure_mode)


# ─────────────────── 客户端 / 智能体接线（与各章形状相同） ──────────────────


def _default_client() -> OpenAIChatClient | OpenAIChatCompletionClient | ReplayChatClient:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "replay":
        return ReplayChatClient(
            fixtures_dir=FIXTURES_DIR,
            record=os.environ.get("RECORD", "").lower() in ("1", "true", "yes"),
            record_provider=os.environ.get("REPLAY_RECORD_PROVIDER", "openai"),
        )
    if provider == "azure":
        return OpenAIChatCompletionClient(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"),
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
    return OpenAIChatClient(
        model=os.environ.get("LLM_MODEL", "gpt-4.1"),
        api_key=os.environ["OPENAI_API_KEY"],
        # Phase 9：可指向任何兼容 OpenAI 的端点（GitHub Models、OpenRouter、
        # vLLM、LM Studio、Ollama），而不必是 api.openai.com —— 见
        # tutorials/00-setup/README.md 的「没有付费 API key？」一节。
        base_url=os.environ.get("LLM_BASE_URL") or None,
    )


def build_agent(client: object | None = None) -> Agent:
    return Agent(
        client or _default_client(),
        instructions=INSTRUCTIONS,
        name="catalog-eval-agent",
        tools=[search_catalog],
    )


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


# ─────────────────── 评估循环 + 记分卡 ──────────────────


async def run_eval_suite(agent: Agent) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in EVAL_CASES:
        answer = await ask(agent, case.prompt)
        det = score_deterministic(answer, case.expected_facts)
        judge = judge_response_stub(case.prompt, answer, case.expected_facts)
        results.append(
            {
                "case_id": case.case_id,
                "prompt": case.prompt,
                "answer": answer,
                "det_score": det.score,
                "det_missing": det.missing,
                "judge_score": judge.score,
                "judge_reasoning": judge.reasoning,
            }
        )
    return results


def print_scorecard(results: list[dict[str, Any]]) -> None:
    # 列宽按显示宽度调过：CJK 字符占两列，所以「案例」用 :<24 才能与
    # ASCII 表头原本的 26 列对齐。
    print(f"{'案例':<24}{'确定性':<12}{'评审':<6}备注")
    print("-" * 80)
    for r in results:
        notes = f"缺失：{r['det_missing']}" if r["det_missing"] else r["judge_reasoning"]
        print(f"{r['case_id']:<26}{r['det_score']:<15.2f}{r['judge_score']:<8.2f}{notes}")
    print("-" * 80)
    passed = sum(1 for r in results if r["det_score"] == 1.0)
    print(f"{passed}/{len(results)} 个案例完全有据（确定性得分 == 1.0）")


async def main() -> None:
    agent = build_agent()
    results = await run_eval_suite(agent)
    print_scorecard(results)


if __name__ == "__main__":
    asyncio.run(main())
