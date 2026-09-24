"""
MAF v1 — 第 28 章：反思与评审（Python）

反思 / 评审循环（reflection / critic-loop）模式：一个智能体产出草稿，
第二个智能体（评审者）按明确命名的评分项打分并给出具体反馈；若未达标，
起草智能体依据反馈改写，评审者再次打分。如此往复，直到草稿通过，或
撞上 `MAX_ITERATIONS` 这道硬上限。

两个智能体，两种角色：

1. `build_draft_agent()` —— 撰写（并改写）一段简短的商品描述。
2. `build_critic_agent()` —— 按三条固定评分项给草稿打分
   （是否提到价格、是否提到功能、是否遵守字数上限），返回严格、
   可解析的判定，外加一行反馈。

本系列其他章节要么是单次 LLM 调用，要么是 MAF 自己驱动并限定边界的
工具调用循环。本章是第一次由 *本仓库自己的代码* 驱动多轮循环、且没有
框架强制的边界 —— `MAX_ITERATIONS` 是唯一挡在无限 token 账单之前的东西。
参见模块级常量 `MAX_ITERATIONS` 以及 README.md 的「常见坑」一节。

不涉及 pgvector、Postgres、A2A —— 本仓库最接近的单遍类比见
agents/python/review_sentiment/tools.py::draft_seller_response，
那里也说明了它为什么不是本章所讲的模式。

运行：
    source agents/.venv/bin/activate
    python tutorials/28-reflection-and-critique/python/main.py
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import re
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"

# 草稿 -> 评审 -> 改写 循环的硬上限。没有它，一个永远不说 PASS 的评审者
# （评分标准过严、模型不稳定、约束本身根本无法满足）会让循环无限转下去，
# 每一轮都白白烧掉一次起草调用和一次评审调用。见 README.md 的「常见坑」。
MAX_ITERATIONS = 3
WORD_LIMIT = 40

DRAFT_INSTRUCTIONS = (
    "You write short e-commerce product descriptions. Follow the price, feature, and "
    "word-limit constraints given in the prompt exactly — do not round the price and do not "
    "invent features not listed. Return only the description text, no preamble, no quotes."
)

CRITIC_INSTRUCTIONS = (
    "You are a strict copy editor grading a product description against three named criteria: "
    "PRICE (does it mention the exact price given), FEATURE (does it mention at least one of "
    "the listed features), LENGTH (is it at or under the given word limit). "
    "Respond in EXACTLY this format, one line per criterion, nothing before or after it:\n"
    "PRICE: PASS or FAIL\n"
    "FEATURE: PASS or FAIL\n"
    "LENGTH: PASS or FAIL\n"
    "FEEDBACK: one sentence covering every FAIL, or 'none' if all three pass\n"
    "Grade exactly what the text says — do not soften a FAIL into a PASS to be polite."
)


# ─────────────────────────── 领域模型 ───────────────────────────


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    price: float
    features: list[str]


DEFAULT_PRODUCT = Product(
    id="P010",
    name="Aurora Desk Lamp",
    price=39.99,
    features=["adjustable color temperature", "USB-C charging port", "touch dimmer"],
)


def draft_prompt(product: Product) -> str:
    return (
        f"Write a product description for '{product.name}'. "
        f"Price: ${product.price:.2f}. Features: {', '.join(product.features)}. "
        f"Keep it to {WORD_LIMIT} words or fewer."
    )


def critic_prompt(product: Product, draft: str) -> str:
    return (
        f"Product: {product.name}\n"
        f"Price: ${product.price:.2f}\n"
        f"Features: {', '.join(product.features)}\n"
        f"Word limit: {WORD_LIMIT}\n\n"
        f"Description to grade:\n{draft}\n\n"
        "Grade it against the PRICE, FEATURE, and LENGTH criteria."
    )


def revise_prompt(product: Product, draft: str, critique: CritiqueResult) -> str:
    return (
        f"Revise this product description for '{product.name}' to fix the critic's feedback. "
        f"Return only the revised description, no preamble.\n\n"
        f"Previous draft:\n{draft}\n\n"
        f"Critic feedback: {critique.feedback}\n\n"
        f"Reminder — price: ${product.price:.2f}, features: {', '.join(product.features)}, "
        f"word limit: {WORD_LIMIT} words."
    )


# ─────────────────────────── 评审结果解析 ───────────────────────────
# 评审者是第二次 LLM 调用，不是框架魔法 —— MAF 对反思循环没有既定主张。
# 循环本身以及「把评审者的自由文本解析成循环可分支的结果」都由本模块负责。

_CRITERION_RE = re.compile(r"^\s*(PRICE|FEATURE|LENGTH)\s*:\s*(PASS|FAIL)", re.IGNORECASE | re.MULTILINE)
_FEEDBACK_RE = re.compile(r"^\s*FEEDBACK\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class CritiqueResult:
    price_ok: bool
    feature_ok: bool
    length_ok: bool
    feedback: str

    @property
    def passed(self) -> bool:
        return self.price_ok and self.feature_ok and self.length_ok


def parse_critique(text: str) -> CritiqueResult:
    """把评审者固定格式的响应解析成 `CritiqueResult`。

    评审者漏写的任何评分项一律按 FAIL 处理，而不是 PASS —— 没有明确说
    PASS 的评审者不配拿到 PASS。这样循环是安全的（它会继续改写，最终
    撞上 MAX_ITERATIONS），而不是把「解析不出来」悄悄当成「够好了」。
    """
    verdicts = {m.group(1).upper(): m.group(2).upper() == "PASS" for m in _CRITERION_RE.finditer(text)}
    feedback_match = _FEEDBACK_RE.search(text)
    feedback = feedback_match.group(1).strip() if feedback_match else ""
    return CritiqueResult(
        price_ok=verdicts.get("PRICE", False),
        feature_ok=verdicts.get("FEATURE", False),
        length_ok=verdicts.get("LENGTH", False),
        feedback=feedback,
    )


# ─────────────────────────── 反思循环 ───────────────────────────


@dataclass(frozen=True)
class Iteration:
    number: int
    draft: str
    critique: CritiqueResult


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


async def run_reflection_loop(
    draft_agent: Agent,
    critic_agent: Agent,
    product: Product,
    *,
    max_iterations: int = MAX_ITERATIONS,
) -> list[Iteration]:
    """草稿 -> 评审 -> 改写 -> 评审 -> …… 最多 `max_iterations` 轮。

    按顺序返回每一轮的草稿与评审结果，让调用方（main() 或测试）能看到
    完整轨迹，而不只是最终答案。一旦某次评审通过就提前结束；否则即使
    最后一次评审仍未通过，也会在 `max_iterations` 次评审后停止 ——
    这正是本章「常见坑」一节所讲的那道硬上限。
    """
    iterations: list[Iteration] = []
    draft = await ask(draft_agent, draft_prompt(product))
    for number in range(1, max_iterations + 1):
        critique_text = await ask(critic_agent, critic_prompt(product, draft))
        critique = parse_critique(critique_text)
        iterations.append(Iteration(number=number, draft=draft, critique=critique))
        if critique.passed or number == max_iterations:
            break
        draft = await ask(draft_agent, revise_prompt(product, draft, critique))
    return iterations


# ─────────────────────────── 客户端与智能体装配 ───────────────────────────


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


def build_draft_agent(client: object | None = None) -> Agent:
    return Agent(
        client or _default_client(),
        instructions=DRAFT_INSTRUCTIONS,
        name="draft-agent",
    )


def build_critic_agent(client: object | None = None) -> Agent:
    return Agent(
        client or _default_client(),
        instructions=CRITIC_INSTRUCTIONS,
        name="critic-agent",
    )


# ─────────────────────────── main ───────────────────────────


def _format_verdict(critique: CritiqueResult) -> str:
    def flag(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    return f"PRICE={flag(critique.price_ok)} FEATURE={flag(critique.feature_ok)} LENGTH={flag(critique.length_ok)}"


async def main() -> None:
    product = DEFAULT_PRODUCT
    draft_agent = build_draft_agent()
    critic_agent = build_critic_agent()

    iterations = await run_reflection_loop(draft_agent, critic_agent, product)

    print(f"Product: {product.name} (${product.price:.2f})")
    print(f"Criteria: mentions price, mentions a feature, <= {WORD_LIMIT} words\n")

    for iteration in iterations:
        print(f"--- Iteration {iteration.number}/{MAX_ITERATIONS} ---")
        print(f"Draft: {iteration.draft}")
        print(f"Critic: {_format_verdict(iteration.critique)}")
        if iteration.critique.feedback and iteration.critique.feedback.lower() != "none":
            print(f"Feedback: {iteration.critique.feedback}")
        print(f"Result: {'PASS' if iteration.critique.passed else 'FAIL'}\n")

    final = iterations[-1]
    if final.critique.passed:
        print(f"Passed after {len(iterations)} iteration(s). Final description:\n{final.draft}")
    else:
        print(f"MAX_ITERATIONS ({MAX_ITERATIONS}) reached without a pass. Last draft kept:\n{final.draft}")


if __name__ == "__main__":
    asyncio.run(main())
