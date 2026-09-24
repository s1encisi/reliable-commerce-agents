"""
MAF v1 — 第 06 章：中间件（Python）

三种中间件，在一次智能体运行中各自被观测或被改写：
- AgentMiddleware：记录每一次智能体调用。
- FunctionMiddleware：校验工具参数；拒绝一个已知的非法取值。
- ChatMiddleware：在 LLM 看到之前，把形似信用卡号的字符串脱敏掉。

运行：
    python tutorials/06-middleware/python/main.py "What's the weather in Paris?"
    python tutorials/06-middleware/python/main.py "My card is 4111-1111-1111-1111"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import re
import sys
from collections.abc import Awaitable, Callable
from typing import Annotated

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, tool  # noqa: E402
from agent_framework._middleware import (  # noqa: E402
    AgentContext,
    AgentMiddleware,
    ChatContext,
    ChatMiddleware,
    FunctionInvocationContext,
    FunctionMiddleware,
)
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from pydantic import Field  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

INSTRUCTIONS = (
    "You are a helpful assistant. "
    "When the user asks about weather in a city, call get_weather. "
    "Keep answers to one short sentence."
)

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"

# ChatMiddleware 使用的模式 —— 匹配看起来像卡号的 4 位数字分组。
_CARD_RE = re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b")


# ─────────────── 工具 ───────────────


@tool(name="get_weather", description="Look up the current weather for a city.")
def get_weather(
    city: Annotated[str, Field(description="The city to look up, e.g. 'Paris'.")],
) -> str:
    canned = {
        "paris": "Sunny, 18°C.",
        "london": "Overcast, 12°C.",
        "tokyo": "Rain, 15°C.",
    }
    return canned.get(city.lower(), f"No weather data for {city}.")


# ─────────────── 中间件 ───────────────


class LoggingAgentMiddleware(AgentMiddleware):
    """观测每一次智能体运行。填充 `events`，好让测试断言顺序。"""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        self.events.append("agent:before")
        await call_next()
        self.events.append("agent:after")


class ArgValidatorMiddleware(FunctionMiddleware):
    """把某个预置的禁用城市拦下来，作为业务规则校验的替身。"""

    FORBIDDEN_CITY = "Atlantis"

    def __init__(self) -> None:
        self.invocations: list[str] = []
        self.blocked: list[str] = []

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        city = context.arguments.get("city", "") if isinstance(context.arguments, dict) else ""
        self.invocations.append(city)
        if city.lower() == self.FORBIDDEN_CITY.lower():
            self.blocked.append(city)
            # 短路：设置一个预置的拒绝结果，跳过真实的工具调用。
            context.result = "Refused: that city isn't supported."
            return
        await call_next()


class PiiRedactionChatMiddleware(ChatMiddleware):
    """在发往模型的外发用户消息中，遮蔽形似信用卡号的数字。"""

    def __init__(self) -> None:
        self.redactions = 0

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        for message in context.messages:
            for i, content in enumerate(message.contents):
                text = getattr(content, "text", None)
                if not text:
                    continue
                redacted, count = _CARD_RE.subn("[REDACTED-CARD]", text)
                if count:
                    self.redactions += count
                    # 就地替换内容文本。
                    content.text = redacted  # type: ignore[attr-defined]
        await call_next()


# ─────────────── 客户端与智能体工厂 ───────────────


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


def build_agent(
    logger: LoggingAgentMiddleware,
    validator: ArgValidatorMiddleware,
    redactor: PiiRedactionChatMiddleware,
    client: object | None = None,
) -> Agent:
    return Agent(
        client or _default_client(),
        instructions=INSTRUCTIONS,
        name="middleware-agent",
        tools=[get_weather],
        middleware=[logger, validator, redactor],
    )


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


async def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else "What's the weather in Paris?"

    logger = LoggingAgentMiddleware()
    validator = ArgValidatorMiddleware()
    redactor = PiiRedactionChatMiddleware()
    agent = build_agent(logger, validator, redactor)

    answer = await ask(agent, question)
    print(f"问：{question}")
    print(f"答：{answer}")
    print()
    print(f"智能体事件：    {logger.events}")
    print(f"工具调用：      {validator.invocations}")
    print(f"工具被拦截：    {validator.blocked}")
    print(f"PII 脱敏次数：  {redactor.redactions}")


if __name__ == "__main__":
    asyncio.run(main())
