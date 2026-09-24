"""
MAF v1 — 第 05 章：上下文提供器（Python）

把逐请求的上下文注入智能体，而不必把它硬编码进系统提示词。演示
ContextProvider.before_run 钩子调用 context.extend_instructions(...) ——
这是 MAF 原生的动态上下文注入方式。

运行：
    python tutorials/05-context-providers/python/main.py
    # 使用默认用户（Alice）。也可以传入邮箱 / 姓名来切换：
    python tutorials/05-context-providers/python/main.py bob@example.com Bob gold
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, ContextProvider  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

INSTRUCTIONS = "You are a personal shopping assistant. Greet the user by name if you know it. Keep answers short."

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"


# ─────────────── 上下文提供器 ───────────────


class UserProfileProvider(ContextProvider):
    """把当前用户的档案作为额外指令，注入到每一次运行中。"""

    def __init__(self, *, email: str, name: str, loyalty_tier: str = "silver") -> None:
        super().__init__(source_id="user-profile")
        self.email = email
        self.name = name
        self.loyalty_tier = loyalty_tier

    async def before_run(
        self,
        *,
        agent: Any,
        session: Any,
        context: Any,
        state: dict[str, Any],
    ) -> None:
        """在 LLM 调用之前运行。我们扩展指令，让模型看到这位用户。"""
        context.extend_instructions(
            "user-profile",
            f"Current user: {self.name} ({self.email}). Loyalty tier: {self.loyalty_tier}.",
        )
        # 同时也放进共享状态，好让工具（第 02 章的模式）能读到它。
        state["user"] = {"email": self.email, "name": self.name, "loyalty_tier": self.loyalty_tier}


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


def build_agent(provider: ContextProvider, client: object | None = None) -> Agent:
    return Agent(
        client or _default_client(),
        instructions=INSTRUCTIONS,
        name="personalized-agent",
        context_providers=[provider],
    )


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


async def main() -> None:
    # 命令行参数：邮箱、姓名、会员等级（均为可选）
    email = sys.argv[1] if len(sys.argv) > 1 else "alice@example.com"
    name = sys.argv[2] if len(sys.argv) > 2 else "Alice"
    tier = sys.argv[3] if len(sys.argv) > 3 else "gold"

    provider = UserProfileProvider(email=email, name=name, loyalty_tier=tier)
    agent = build_agent(provider)

    answer = await ask(agent, "Greet me and tell me what tier I'm on.")
    print(f"答：{answer}")


if __name__ == "__main__":
    asyncio.run(main())
