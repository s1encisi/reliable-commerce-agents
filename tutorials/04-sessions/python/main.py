"""
MAF v1 — 第 04 章：会话与记忆（Python）

在两次进程运行之间把 AgentSession 持久化到磁盘。演示：
  - InMemoryHistoryProvider 把对话存进会话状态。
  - session.to_dict() / AgentSession.from_dict() 用于磁盘持久化。
  - 保存下来的会话在另一个进程里重新加载后，仍带着之前的轮次。

用法：
    # 第 1 轮写入 session.json：
    python tutorials/04-sessions/python/main.py save "Remember: I want to buy SKU-4471."
    # 第 2 轮读取 session.json 并追问：
    python tutorials/04-sessions/python/main.py load "What did I say I wanted to buy?"
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, AgentSession, InMemoryHistoryProvider  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

INSTRUCTIONS = "You are a helpful assistant. Keep answers short."
SESSION_FILE = pathlib.Path(__file__).resolve().parent / "session.json"

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
    return Agent(
        client or _default_client(),
        instructions=INSTRUCTIONS,
        name="stateful-agent",
        # InMemoryHistoryProvider 让 AgentSession 成为对话的载体。
        context_providers=[InMemoryHistoryProvider()],
    )


async def ask_and_save(agent: Agent, question: str, path: pathlib.Path) -> str:
    """在全新或已加载的会话上跑一轮，然后把会话持久化到磁盘。"""
    session = _load_or_new(agent, path)
    response = await agent.run(question, session=session)
    _save(session, path)
    return response.text


def _load_or_new(agent: Agent, path: pathlib.Path) -> AgentSession:
    if path.exists():
        data = json.loads(path.read_text())
        return AgentSession.from_dict(data)
    return agent.create_session()


def _save(session: AgentSession, path: pathlib.Path) -> None:
    path.write_text(json.dumps(session.to_dict(), indent=2, default=str))


async def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "save"
    question = sys.argv[2] if len(sys.argv) > 2 else "Hello!"

    if mode == "reset":
        if SESSION_FILE.exists():
            SESSION_FILE.unlink()
        print("会话已清除。")
        return

    agent = build_agent()
    answer = await ask_and_save(agent, question, SESSION_FILE)
    print(f"问：{question}")
    print(f"答：{answer}")
    print(f"（会话已持久化到 {SESSION_FILE.name}）")


if __name__ == "__main__":
    asyncio.run(main())
