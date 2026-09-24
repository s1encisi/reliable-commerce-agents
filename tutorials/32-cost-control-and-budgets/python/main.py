"""
MAF v1 — 第 32 章：成本控制与预算（Python）

一个 `ChatMiddleware`，累计追踪一次运行中每一轮 LLM 调用的预估美元成本，
一旦越过配置的上限，就拒绝开始*下一轮* —— 这是
`agents/python/shared/guardrails/cost_budget_middleware.py` 里
`CostBudgetMiddleware` 的玩具级替身。同样是两档姿态（`observe` 从不拦截，
`enforce` 会拦截），同样的短路机制（把 `context.result` 设为一条拒绝响应并
跳过 `call_next()`），只是简化为普通实例属性而非 `ContextVar` —— 本脚本的
各轮是在单个进程里顺序执行的，而不是并发的 asyncio 任务、各自需要一份隔离
的运行总计。

`get_product_price` 是一个预置数据的工具（不做真实目录查询）。每个问题都会
触发一个两轮的工具调用循环（一轮模型调用工具，一轮它读取结果并作答）——
逐轮累计成本正是全部要点，所以单轮演示看不出任何有意思的东西。
`DEMO_BUDGET_USD_PER_RUN` 刻意设得极小（不到一美分），纯粹是为了让上限在
两三个简短的演示问题内就被触发，而不必跑一次很长、很贵的运行 —— 生产环境的
上限（`COST_BUDGET_USD_PER_RUN`）是按真实负载设定的，而不是按这个玩具的
量级。

关于回放模式的说明：`tutorials/_shared/replay_client.py` 里的
`ReplayChatClient` 刻意把 `FunctionInvocationLayer` 直接与 `BaseChatClient`
组合，跳过了 `ChatMiddlewareLayer`（见该模块自己的文档字符串）—— 回放客户端
只是为了正确回放一个工具调用夹具，并不需要它。这意味着在
`LLM_PROVIDER=replay` 下 `CostBudgetChatMiddleware.process()` 永远不会执行：
下方的回放测试只能证明工具调用往返被正确回放，而不能证明预算中间件真的触发
了。`[budget]` 打印与那条拒绝响应只有在面对真实 LLM
（`LLM_PROVIDER=azure` 或 `openai`）时才会出现 —— 第 06 章的 PII 脱敏
`ChatMiddleware` 有同样的局限，这也是为什么那一章的 chat-middleware 断言
同样是一个只在真实 LLM 下运行的测试。

运行：
    python tutorials/32-cost-control-and-budgets/python/main.py
    python tutorials/32-cost-control-and-budgets/python/main.py "What's the price of product P-100?"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from collections.abc import Awaitable, Callable
from typing import Annotated

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import (  # noqa: E402
    Agent,
    ChatContext,
    ChatMiddleware,
    ChatResponse,
    Message,
    tool,
)
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from pydantic import Field  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

INSTRUCTIONS = (
    "You are a shopping assistant. When the user asks about a product's price, call the "
    "`get_product_price` tool with the product ID and answer in one short sentence."
)
DEFAULT_QUESTIONS = [
    "What's the price of product P-100?",
    "What's the price of product P-200?",
    "What's the price of product P-300?",
]

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"

# 刻意设得极小 —— 不到一美分。真实的生产上限
# （COST_BUDGET_USD_PER_RUN）是按真实负载设定的（以美元计，而非美分）；
# 这个数字的存在只是为了在两三个简短的演示问题内就被触发，而不必花上
# 几百轮付费调用才能演示这个机制。
DEMO_BUDGET_USD_PER_RUN = 0.0015

BUDGET_REFUSAL_MESSAGE = (
    "This run has been stopped because it exceeded its configured cost budget. "
    "Start a new request, or raise the budget if this ceiling is too low."
)

# 简化的单模型定价 —— 每 1K token 的美元价。与生产环境
# shared/cost.py::_PRICING["gpt-4.1"] 的数字一致，因此本演示打印出的
# 金额是贴近现实的，而非编造。生产的定价表覆盖多个模型，并对无法识别的
# 模型优雅降级；这个玩具只需要教程 `.env` 所配置的那一个模型。
GPT_4_1_INPUT_PER_1K = 0.002
GPT_4_1_OUTPUT_PER_1K = 0.008


def estimate_cost_usd(tokens_in: int, tokens_out: int) -> float:
    """根据 token 数估算一轮调用的美元成本。简化自 shared/cost.py。"""
    return (tokens_in / 1000) * GPT_4_1_INPUT_PER_1K + (tokens_out / 1000) * GPT_4_1_OUTPUT_PER_1K


# ─────────────────── 工具 ───────────────────


@tool(name="get_product_price", description="Look up the current price for a product by ID.")
def get_product_price(
    product_id: Annotated[str, Field(description="The product ID to look up, e.g. 'P-100'.")],
) -> str:
    canned = {
        "p-100": "$129.99",
        "p-200": "$49.50",
        "p-300": "$899.00",
    }
    return canned.get(product_id.lower(), f"No price found for product {product_id}.")


# ─────────────────── 成本预算中间件 ───────────────────


class CostBudgetChatMiddleware(ChatMiddleware):
    """累计追踪每次运行的逐轮成本，并在 `enforce` 模式下给它封顶。

    `CostBudgetMiddleware` 的玩具级替身
    （`agents/python/shared/guardrails/cost_budget_middleware.py`）。两种
    模式，对应 `settings.COST_BUDGET_MODE`：

    - `observe` —— 累计并打印运行成本；从不拦截，即使已越过 `budget_usd`。
      这是生产环境的默认值。
    - `enforce` —— 同样累计，并在运行总计超过 `budget_usd` 后拒绝*下一轮*。
      在越过上限时已经在途的一轮绝不会被中途打断 —— 成本只有在某一轮
      完成之后（从其 `usage_details`）才可知，所以强制拦截必然滞后于实际
      超支一轮。这与真实中间件所记录的权衡完全一致。
    """

    def __init__(self, *, budget_usd: float, mode: str = "enforce") -> None:
        self.budget_usd = budget_usd
        self.mode = mode
        self.total_cost_usd = 0.0
        self.turns_recorded = 0
        self.blocked = 0

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        if self.mode == "off":
            await call_next()
            return

        if self.mode == "enforce" and self.total_cost_usd > self.budget_usd:
            self.blocked += 1
            print(
                f"  [budget] 已拒绝第 {self.turns_recorded + self.blocked} 轮 —— "
                f"运行总计 ${self.total_cost_usd:.4f} 已超过 ${self.budget_usd:.4f}"
            )
            # 短路：不调用 call_next() —— 一旦本次运行已超预算，
            # 就不再发起任何后续 LLM 调用。
            context.result = ChatResponse(
                messages=[Message(role="assistant", contents=[BUDGET_REFUSAL_MESSAGE])],
                finish_reason="length",
            )
            return

        await call_next()

        if context.result is None:
            return
        self._record(context.result)

    def _record(self, response: object) -> None:
        usage = getattr(response, "usage_details", None)
        if not usage:
            return  # 没有用量数据（例如夹具录制时未包含）—— 无从计价
        tokens_in = usage.get("input_token_count") or 0
        tokens_out = usage.get("output_token_count") or 0
        cost = estimate_cost_usd(tokens_in, tokens_out)
        self.total_cost_usd += cost
        self.turns_recorded += 1
        print(
            f"  [budget] 第 {self.turns_recorded} 轮：+${cost:.4f} "
            f"(in={tokens_in} out={tokens_out}) -> 运行总计 ${self.total_cost_usd:.4f}"
        )


# ─────────────────── 客户端与智能体工厂 ───────────────────


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


def build_agent(budget_middleware: CostBudgetChatMiddleware, client: object | None = None) -> Agent:
    return Agent(
        client or _default_client(),
        instructions=INSTRUCTIONS,
        name="cost-budget-agent",
        tools=[get_product_price],
        middleware=[budget_middleware],
    )


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


async def main() -> None:
    questions = sys.argv[1:] or DEFAULT_QUESTIONS

    budget_mw = CostBudgetChatMiddleware(budget_usd=DEMO_BUDGET_USD_PER_RUN, mode="enforce")
    agent = build_agent(budget_mw)

    print(f"预算：每次运行 ${budget_mw.budget_usd:.4f}（模式={budget_mw.mode}）\n")
    for question in questions:
        answer = await ask(agent, question)
        print(f"问：{question}")
        print(f"答：{answer}")
        print()

    print(f"已记录轮数：{budget_mw.turns_recorded}")
    print(f"已拦截轮数：{budget_mw.blocked}")
    print(f"运行总计：  ${budget_mw.total_cost_usd:.4f}（预算 ${budget_mw.budget_usd:.4f}）")


if __name__ == "__main__":
    asyncio.run(main())
