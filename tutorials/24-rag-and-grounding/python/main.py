"""
MAF v1 — 第 24 章：检索与事实核验（Python）

两个机制，刻意分开讲：

1. 检索 —— `search_products` 是智能体调用的工具，用来读取真实数据
   （一个极小的内存商品目录），而不是依赖模型训练数据对商品「记得」的
   内容。朴素的子串匹配 —— 重点不在于搜索质量，而在于检索这件事本身
   存在。
2. 事实核验 —— `verify_claims()` 在模型作答*之后*运行。它抽取答案中声称的
   商品 id / 价格，并拿它们与同一份目录核对。检索只保证模型有机会接触到
   事实；核验则是另一步，用来检查模型的文字是否真的复述了事实。

不涉及 pgvector、Postgres —— 本章在玩具规模上对照的生产版本见
`agents/python/product_discovery/tools.py`（semantic_search）与
`agents/python/shared/grounding/verifier.py`（verify_claims）。

运行：
    source agents/.venv/bin/activate
    python tutorials/24-rag-and-grounding/python/main.py "Do you have noise-cancelling headphones?"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import re
import sys
from dataclasses import dataclass, field
from typing import Annotated

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, tool  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from pydantic import Field  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

INSTRUCTIONS = (
    "You are a shopping assistant for a small store. "
    "When the user asks about products, call the `search_products` tool — never answer "
    "from memory. When you mention a product in your answer, always include its exact "
    "product id (e.g. 'P001') and its exact price, copied verbatim from the tool result, "
    "not rounded or paraphrased. For other questions, answer directly in one short sentence."
)
DEFAULT_QUESTION = "Do you have any noise-cancelling headphones? What's the price and product id?"

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"

# ─────────────────────── 「知识库」 ───────────────────────
# 几个 Python dict，用来替代真实的商品表。生产环境使用 Postgres + pgvector
# （agents/python/product_discovery/tools.py）；本章所讲的机制 —— 先一个
# 检索工具，再一步核验 —— 并不依赖那是一个真实数据库。
CATALOG: list[dict] = [
    {"id": "P001", "name": "Wireless Noise-Cancelling Headphones", "price": 129.99, "category": "Electronics"},
    {"id": "P002", "name": "Stainless Steel Water Bottle", "price": 24.50, "category": "Home"},
    {"id": "P003", "name": "Organic Cotton Hoodie", "price": 54.00, "category": "Clothing"},
    {"id": "P004", "name": "Bluetooth Portable Speaker", "price": 39.99, "category": "Electronics"},
    {"id": "P005", "name": "Yoga Mat with Carry Strap", "price": 19.95, "category": "Sports"},
]


# ─────────────────────────── 检索 ───────────────────────────


@tool(
    name="search_products",
    description="Search the product catalog by keyword. Returns matching products with id, name, and price.",
)
def search_products(
    query: Annotated[
        str, Field(description="Keyword(s) to match against product name or category, e.g. 'headphones'.")
    ],
) -> list[dict]:
    # 在 name + category 上做朴素子串匹配 —— 不排序、不用向量。
    # 真实检索质量不是本章的重点；重点是有一个检索工具存在，
    # 而不是让模型凭记忆猜。
    words = [w for w in query.lower().split() if w]
    matches = []
    for product in CATALOG:
        haystack = f"{product['name']} {product['category']}".lower()
        if any(word in haystack for word in words):
            matches.append(product)
    return matches


# ─────────────────────────── 事实核验 ───────────────────────────
# 在玩具规模上对照 agents/python/shared/grounding/verifier.py::verify_claims()：
# 数据库比对（这里是目录比对）+ 一致性检查。生产版本的「账本」层
# （本轮工具调用已经露出的事实，在查数据库之前先免费核对一遍）在此省略 ——
# 这里唯一的一份内存目录*就是*数据库，所以没有更廉价的东西可以先查。

_ID_RE = re.compile(r"\bP0\d{2}\b")
_PRICE_RE = re.compile(r"\$(\d+(?:\.\d{1,2})?)")
_PRICE_TOLERANCE = 0.01


@dataclass(frozen=True)
class ProductClaim:
    id: str
    price: float | None


@dataclass(frozen=True)
class ClaimVerdict:
    identifier: str
    status: str  # 核验状态：verified、price_mismatch、not_found。
    detail: str | None = None


@dataclass
class GroundingReport:
    verdicts: list[ClaimVerdict] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        return len(self.verdicts)

    @property
    def verified_count(self) -> int:
        return sum(1 for v in self.verdicts if v.status == "verified")


def extract_claims(answer: str) -> list[ProductClaim]:
    """抽取答案中声称的每一个商品 id，以及就近出现的一个价格（若有）。

    刻意写得很笨：真实的断言抽取器（agents/python/shared/grounding/
    extractor.py）解析的是结构化卡片载荷，而不是用正则处理自由文本。
    这里足够演示问题的*形状* —— 模型的文字可能与工具实际返回的内容发生偏离。
    """
    claims: list[ProductClaim] = []
    for match in _ID_RE.finditer(answer):
        window = answer[match.end() : match.end() + 40]
        price_match = _PRICE_RE.search(window)
        price = float(price_match.group(1)) if price_match else None
        claims.append(ProductClaim(id=match.group(0), price=price))
    return claims


def verify_claims(claims: list[ProductClaim], catalog: list[dict] | None = None) -> GroundingReport:
    """拿每个声称的 id / 价格去目录（事实来源）里核对。

    这正是单靠检索给不了你的一步：`search_products` 只保证模型*看到过*真实
    数据。没有任何东西能阻止模型在最后一句里引用错误的 id，或把价格四舍五入。
    本函数在事后把这道缺口补上。
    """
    catalog_by_id = {p["id"]: p for p in (catalog or CATALOG)}
    verdicts: list[ClaimVerdict] = []
    for claim in claims:
        product = catalog_by_id.get(claim.id)
        if product is None:
            verdicts.append(ClaimVerdict(claim.id, "not_found", "目录中没有这个 id 的商品"))
            continue
        if claim.price is not None and abs(claim.price - product["price"]) >= _PRICE_TOLERANCE:
            detail = f"目录价格为 ${product['price']:.2f}，而非 ${claim.price:.2f}"
            verdicts.append(ClaimVerdict(claim.id, "price_mismatch", detail))
            continue
        verdicts.append(ClaimVerdict(claim.id, "verified"))
    return GroundingReport(verdicts=verdicts)


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
        name="grounded-shopping-agent",
        tools=[search_products],
    )


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


async def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    agent = build_agent()
    answer = await ask(agent, question)
    print(f"问：{question}")
    print(f"答：{answer}")

    report = verify_claims(extract_claims(answer))
    print(f"事实核验：{report.verified_count}/{report.total_count} 条断言通过")
    for verdict in report.verdicts:
        if verdict.status != "verified":
            print(f"  ! {verdict.identifier}: {verdict.status} ({verdict.detail})")


if __name__ == "__main__":
    asyncio.run(main())
