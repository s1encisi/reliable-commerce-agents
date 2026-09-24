"""
MAF v1 — 第 03 章：流式输出与多轮对话（Python）

一个示例讲两个概念：
- 流式：迭代 `agent.run(..., stream=True)`，边到边打印 token。
- 多轮：在多次 `.run()` 调用之间复用同一个 AgentSession，让 LLM 看到完整
  的对话上下文。

交互模式：
    python tutorials/03-streaming-and-multiturn/python/main.py

一次性传入问题（不进入交互）：
    python tutorials/03-streaming-and-multiturn/python/main.py "What's Python?" "How old is it?"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, AgentSession  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

INSTRUCTIONS = "You are a concise assistant. Keep answers to one short paragraph."

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"


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
    return Agent(client or _default_client(), instructions=INSTRUCTIONS, name="chat-agent")


async def stream_answer(
    agent: Agent,
    question: str,
    session: AgentSession,
) -> list[str]:
    """
    流式输出智能体的回答。边到边打印每个分片，并返回分片列表，好让调用方
    验证流式确实发生了。

    ``agent.run(..., stream=True)`` 驱动的是 MAF 的流式函数调用循环，它通过
    chat client 的 ``ResponseStream`` 为每个流式回合收尾。``ReplayChatClient``
    （见 tutorials/_shared/replay_client.py）接的是与真实客户端相同的收尾器
    （``BaseChatClient._build_response_stream``），因此回放模式是走与其它所有
    提供方相同的路径正确流式的 —— 这里不需要任何回退分支。
    """
    chunks: list[str] = []
    async for update in agent.run(question, stream=True, session=session):
        if update.text:
            chunks.append(update.text)
            print(update.text, end="", flush=True)
    print()
    return chunks


async def chat(agent: Agent, questions: list[str]) -> list[list[str]]:
    """在同一个会话上跑一段脚本化的多轮对话；返回每一轮的分片列表。"""
    session = agent.create_session()
    all_chunks: list[list[str]] = []
    for q in questions:
        print(f"\n问：{q}")
        print("答：", end="", flush=True)
        chunks = await stream_answer(agent, q, session)
        all_chunks.append(chunks)
    return all_chunks


async def main() -> None:
    agent = build_agent()

    if len(sys.argv) > 1:
        await chat(agent, sys.argv[1:])
        return

    # 交互式 REPL
    print("多轮对话（输入空行退出）。")
    session = agent.create_session()
    while True:
        try:
            q = input("\n问：").strip()
        except EOFError:
            break
        if not q:
            break
        print("答：", end="", flush=True)
        await stream_answer(agent, q, session)


if __name__ == "__main__":
    asyncio.run(main())
