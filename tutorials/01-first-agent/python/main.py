"""
MAF v1 — 第 01 章：你的第一个智能体（Python）

用最少的代码，把一个 Microsoft Agent Framework 智能体接到 OpenAI
（或 Azure OpenAI）上，并向它提一个问题。

在仓库根目录、激活共享的 agents 虚拟环境后运行：

    source agents/.venv/bin/activate
    python tutorials/01-first-agent/python/main.py

或覆盖默认问题：

    python tutorials/01-first-agent/python/main.py "Why is the sky blue?"

环境变量：
    从仓库根目录的 .env 读取 OPENAI_API_KEY（或 Azure 相关变量）与 LLM_MODEL。
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

# 必须在任何 agent_framework 导入之前完成引导。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

import os  # noqa: E402

from agent_framework import Agent  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

INSTRUCTIONS = "You are a concise geography assistant. Keep answers to one short sentence."
DEFAULT_QUESTION = "What is the capital of France?"

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"


def _default_client() -> OpenAIChatClient | OpenAIChatCompletionClient | ReplayChatClient:
    """根据环境变量构建 chat client。会遵循 LLM_PROVIDER。

    - OpenAI（公有云）：使用基于 Responses API 的 OpenAIChatClient。
    - Azure OpenAI：使用 OpenAIChatCompletionClient（Chat Completions API）。
      并非每个 Azure 部署都开放 Responses API，因此默认走 Chat Completions，
      好让本章在各 Azure 区域都可移植。
    - replay：回放已录制的夹具，无需任何凭据。设置 RECORD=true 可针对
      REPLAY_RECORD_PROVIDER（默认 "openai"）录制一份新的，而不是在夹具
      缺失时直接报错。
    """
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
    """构建智能体。可选传入一个预先构建好的 client 供测试使用。"""
    return Agent(client or _default_client(), instructions=INSTRUCTIONS, name="first-agent")


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


async def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    agent = build_agent()
    answer = await ask(agent, question)
    print(f"问：{question}")
    print(f"答：{answer}")


if __name__ == "__main__":
    asyncio.run(main())
