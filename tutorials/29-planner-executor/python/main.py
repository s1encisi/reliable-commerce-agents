"""
MAF v1 — 第 29 章：规划器-执行器（Python）

先把用户请求分解成一份有序计划（由「规划器」智能体输出 Pydantic 结构化
结果），再用「执行器」智能体逐步顺序执行 —— 某一步可能是商品目录检索，
也可能是对前面结果的推理。计划会在任何一步执行之前先打印出来，随后每
执行一步就打印该步结果。

与路由器 / 工具模式（第 02、12 章及以后 / 本仓库自有的「tool」编排模式）
对比：那里由 LLM 反应式地一次决定一个工具调用，没有预先计划。这里整份
计划在开始前就已确定且可检视 —— 更可预测、更易审批或估算成本，但对某
一步的意外结果适应性较差，除非你加入重规划（本章未实现 —— 见「常见坑」）。

运行：
    python tutorials/29-planner-executor/python/main.py \
        "help me put together a birthday gift for someone who likes photography under $200"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from typing import Annotated

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, AgentSession, InMemoryHistoryProvider, tool  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

DEFAULT_REQUEST = "Help me put together a birthday gift for someone who likes photography, under $200."

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"

PLANNER_INSTRUCTIONS = (
    "You are a planning assistant for an e-commerce store. Given a shopping request, "
    "decompose it into a short ordered list of concrete steps needed to satisfy it — "
    "typically: search the catalog for relevant products, narrow results by a constraint "
    "such as price, pick the best candidates, and summarize a recommendation. "
    "For any step that should search the product catalog, set `query` to the search text "
    "for that step. For steps that only reason over results gathered by earlier steps "
    "(filtering, picking, summarizing), leave `query` null. "
    "Respond with the structured plan only — do not execute any step yourself."
)

EXECUTOR_INSTRUCTIONS = (
    "You are an execution assistant for an e-commerce store, running one step of an "
    "already-approved plan at a time. Each user message names the step to perform right "
    "now. If the step needs to search the catalog, call the `search_products` tool. "
    "Otherwise reason directly over the product results already visible earlier in this "
    "conversation. Keep your answer to a few sentences and stay focused on this one step."
)

# 玩具级内存目录。刻意自包含 —— 本章不导入第 24 章的 RAG 目录，
# 以保持两章彼此独立。
_CATALOG: list[dict[str, object]] = [
    {
        "name": "Compact Mirrorless Camera",
        "category": "photography",
        "price": 189.0,
        "description": "Beginner-friendly mirrorless camera with a kit lens.",
    },
    {
        "name": "50mm Prime Lens",
        "category": "photography",
        "price": 129.0,
        "description": "Fast prime lens for portraits and low light.",
    },
    {
        "name": "Travel Camera Tripod",
        "category": "photography",
        "price": 39.0,
        "description": "Lightweight aluminum tripod, folds to 16 inches.",
    },
    {
        "name": "Padded Camera Strap",
        "category": "photography",
        "price": 19.0,
        "description": "Padded leather camera strap with quick-release buckles.",
    },
    {
        "name": "Professional Studio Light Kit",
        "category": "photography",
        "price": 349.0,
        "description": "Two-softbox studio lighting kit for indoor shoots.",
    },
    {
        "name": "Wireless Noise-Canceling Headphones",
        "category": "audio",
        "price": 179.0,
        "description": "Over-ear headphones with active noise cancellation.",
    },
    {
        "name": "Espresso Machine",
        "category": "kitchen",
        "price": 249.0,
        "description": "Semi-automatic espresso machine with a steam wand.",
    },
]


@tool(
    name="search_products",
    description="Search the toy product catalog by keyword, optionally capped by an inclusive max price.",
)
def search_products(
    query: Annotated[str, Field(description="Free-text search, matched against name/category/description.")],
    max_price: Annotated[float | None, Field(description="Optional inclusive price ceiling in USD.")] = None,
) -> str:
    terms = query.lower().split()
    matches = [
        item
        for item in _CATALOG
        if any(t in f"{item['name']} {item['category']} {item['description']}".lower() for t in terms)
        and (max_price is None or item["price"] <= max_price)  # type: ignore[operator]
    ]
    if not matches:
        cap = f" under ${max_price:.0f}" if max_price is not None else ""
        return f"No products found for '{query}'{cap}."
    return "\n".join(f"- {m['name']} (${m['price']:.0f}): {m['description']}" for m in matches)


# ─────────────────── 计划：结构化输出，而非自由文本 ──────────


class PlanStep(BaseModel):
    step: int = Field(description="1-based order of this step in the plan.")
    action: str = Field(description="Short human-readable description of what this step accomplishes.")
    query: str | None = Field(
        default=None,
        description="Catalog search text for this step, or null if the step only reasons over prior results.",
    )


class Plan(BaseModel):
    goal: str = Field(description="One-sentence restatement of what the user wants overall.")
    steps: list[PlanStep] = Field(description="Ordered steps that together satisfy the goal.")


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


def build_planner_agent(client: object | None = None) -> Agent:
    """规划器：不带工具，只输出结构化的 `Plan`。"""
    return Agent(
        client or _default_client(),
        instructions=PLANNER_INSTRUCTIONS,
        name="planner-agent",
    )


def build_executor_agent(client: object | None = None) -> Agent:
    """执行器：一次执行一步，带目录检索工具与共享会话。"""
    return Agent(
        client or _default_client(),
        instructions=EXECUTOR_INSTRUCTIONS,
        name="executor-agent",
        tools=[search_products],
        context_providers=[InMemoryHistoryProvider()],
    )


async def make_plan(planner: Agent, request: str) -> Plan:
    """向规划器索取结构化的 Plan。若模型未返回可解析的 JSON 则抛错。"""
    response = await planner.run(request, options={"response_format": Plan})
    plan = response.value
    if plan is None:
        raise ValueError(f"planner did not return a parseable plan; raw text: {response.text!r}")
    return plan


async def run_step(executor: Agent, session: AgentSession, step: PlanStep) -> str:
    """只执行一个计划步骤。所有步骤共享同一个会话，因此第 N 步能看到第 N-1 步的结果。"""
    if step.query:
        prompt = f"Step {step.step}: {step.action} Use search_products with query={step.query!r}."
    else:
        prompt = f"Step {step.step}: {step.action}"
    response = await executor.run(prompt, session=session)
    return response.text


async def run_plan(request: str) -> tuple[Plan, list[str]]:
    """先把整个请求规划好，再按顺序执行每一步。返回 (plan, 每步结果)。"""
    planner = build_planner_agent()
    executor = build_executor_agent()
    plan = await make_plan(planner, request)
    session = executor.create_session()
    results = [await run_step(executor, session, step) for step in plan.steps]
    return plan, results


async def main() -> None:
    request = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REQUEST
    print(f"Request: {request}\n")
    plan, results = await run_plan(request)
    print(f"Plan: {plan.goal}")
    for step, result in zip(plan.steps, results, strict=True):
        print(f"\n{step.step}. {step.action}")
        print(f"   -> {result}")


if __name__ == "__main__":
    asyncio.run(main())
