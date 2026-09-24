"""
MAF v1 —— 第 27 章：把智能体作为工具（Python）

通过 `Agent.as_tool(...)` 把一个范围清晰的单一用途「商品查询」Agent 包装成
FunctionTool，并把它交给一个「协调者」智能体自己的工具集。没有网络跳转，
没有交接网 —— 只是把一个 Agent 以任何普通 `@tool` 装饰函数相同的方式
呈现给另一个智能体。

运行：
    source agents/.venv/bin/activate
    python tutorials/27-agent-as-tool/python/main.py "Look up the Wireless Headphones, \
        then tell me the price after a 20% discount."
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

from agent_framework import Agent, FunctionTool, tool  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from pydantic import Field  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

# ─────────────────── 内存商品目录 ──────────────────

CATALOG: dict[str, dict] = {
    "wireless headphones": {"sku": "SKU-1001", "price": 149.99, "category": "Electronics", "stock": 42},
    "running shoes": {"sku": "SKU-2044", "price": 89.50, "category": "Sports", "stock": 17},
    "coffee maker": {"sku": "SKU-3310", "price": 64.00, "category": "Home", "stock": 0},
    "yoga mat": {"sku": "SKU-4477", "price": 24.99, "category": "Sports", "stock": 120},
}

PRODUCT_LOOKUP_INSTRUCTIONS = (
    "You are a product-lookup specialist. When asked about a product, call the "
    "`search_catalog` tool with the product name and report back its price, "
    "category, and stock level in one short sentence. Do not answer anything else."
)

COORDINATOR_INSTRUCTIONS = (
    "You are a shopping assistant coordinator. When the user asks about a product, "
    "call the `product_lookup` tool with a short task description to get its details. "
    "If the user also asks about a discount, call the `calculate_discount` tool with "
    "the price you got back and the requested percentage, then combine both results "
    "into one final answer. Never guess a price yourself — always use the tools."
)

DEFAULT_QUESTION = "Look up the Wireless Headphones, then tell me the price after a 20% discount."

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"


# 商品查询智能体自己的工具 —— 一个普通的 MAF 工具，没有任何特殊之处。
@tool(name="search_catalog", description="Look up a product in the catalog by name.")
def search_catalog(
    name: Annotated[str, Field(description="The product name to look up, e.g. 'Wireless Headphones'.")],
) -> str:
    item = CATALOG.get(name.lower().strip())
    if item is None:
        return f"No catalog entry for '{name}'."
    return (
        f"{name.title()}: ${item['price']:.2f}, category {item['category']}, "
        f"{item['stock']} in stock."
    )


# 一个协调者可以直接调用的普通本地工具 —— 在被包装的智能体已经回答、
# 并把控制权交回之后使用。
@tool(name="calculate_discount", description="Compute a price after a percentage discount.")
def calculate_discount(
    price: Annotated[float, Field(description="The original price.")],
    percent: Annotated[float, Field(description="The discount percentage, e.g. 20 for 20%.")],
) -> str:
    discounted = price * (1 - percent / 100)
    return f"${discounted:.2f} (after {percent:.0f}% off ${price:.2f})"


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
        # Phase 9：改用任何 OpenAI 兼容端点（GitHub Models、OpenRouter、
        # vLLM、LM Studio、Ollama），而不是 api.openai.com —— 见
        # tutorials/00-setup/README.md 的「没有付费 API 密钥？」小节。
        base_url=os.environ.get("LLM_BASE_URL") or None,
    )


def build_product_lookup_agent(client: object | None = None) -> Agent:
    """那个范围清晰的小型专业智能体，即将被包装成工具。"""
    return Agent(
        client or _default_client(),
        instructions=PRODUCT_LOOKUP_INSTRUCTIONS,
        name="product-lookup-agent",
        description="Looks up product price, category, and stock in the catalog.",
        tools=[search_catalog],
    )


def build_agent(client: object | None = None) -> Agent:
    """协调者 —— 本章的 ask()/main() 直接驱动的那个智能体。

    构建商品查询智能体，用 `.as_tool()` 包装它，并把得到的 FunctionTool
    交给协调者自己的 tools=[...]，与一个普通本地工具并列。两个智能体共用
    一个 chat client，因此演示只需要一套 LLM 提供方/凭据。
    """
    resolved_client = client or _default_client()
    product_lookup_agent = build_product_lookup_agent(resolved_client)
    product_lookup_tool: FunctionTool = product_lookup_agent.as_tool(
        name="product_lookup",
        description="Delegate a product question to the product-lookup specialist agent.",
        arg_name="task",
    )
    return Agent(
        resolved_client,
        instructions=COORDINATOR_INSTRUCTIONS,
        name="coordinator-agent",
        tools=[product_lookup_tool, calculate_discount],
    )


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


async def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    agent = build_agent()
    answer = await ask(agent, question)
    print(f"Q: {question}")
    print(f"A: {answer}")


if __name__ == "__main__":
    asyncio.run(main())
