"""
第 01 章 —— 你的第一个智能体：测试。

三种模式：
- 单元测试使用一个预置回答的 BaseChatClient 子类 —— 不调用 LLM，随处可跑。
- 回放测试通过 LLM_PROVIDER=replay 回放一份已提交的夹具
  （tests/fixtures/replay/）—— 无需凭据，可在 CI 中运行。
- 集成测试用仓库根目录 .env 里的密钥访问真实 LLM，以便对照真实模型
  （而不只是夹具）验证。缺少 OPENAI_API_KEY（或 Azure 对应变量）时跳过。
  上面那份回放测试所用的夹具也正是这样录制的 —— 见 main.py 的模块文档字符串。
"""

from __future__ import annotations

import os
import pathlib
import sys
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any

import pytest

# 在触碰 agent_framework 之前先引导 MAF 并加载仓库根目录的 .env。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from agent_framework import Agent, BaseChatClient, Message  # noqa: E402
from agent_framework._types import ChatResponse, ChatResponseUpdate, ResponseStream  # noqa: E402
from main import FIXTURES_DIR, INSTRUCTIONS, _default_client, ask, build_agent  # noqa: E402


class CannedChatClient(BaseChatClient):
    """仅供测试使用的 chat client：返回预置回答并记录输入。"""

    def __init__(self, *canned: str) -> None:
        super().__init__()
        self._responses = list(canned)
        self.calls: list[tuple[Sequence[Message], Mapping[str, Any]]] = []

    def _inner_get_response(  # type: ignore[override]
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Awaitable[ChatResponse] | ResponseStream[ChatResponseUpdate, ChatResponse]:
        self.calls.append((list(messages), dict(options)))
        if not self._responses:
            raise AssertionError("CannedChatClient ran out of responses")
        text = self._responses.pop(0)
        assistant = Message(role="assistant", contents=[text])

        async def _return() -> ChatResponse:
            return ChatResponse(messages=[assistant])

        return _return()


# ───────────────────── 单元测试（不涉及 LLM） ──────────────────────


def test_build_agent_uses_instructions() -> None:
    client = CannedChatClient("Paris.")
    agent = build_agent(client=client)
    assert isinstance(agent, Agent)
    # MAF 把系统指令存在 default_options 里，而不是作为顶层属性。
    assert agent.default_options["instructions"] == INSTRUCTIONS
    assert agent.name == "first-agent"


def test_default_client_honors_llm_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 9：正是 LLM_BASE_URL 让 LLM_PROVIDER=openai 可以指向任何兼容
    OpenAI 的端点（Ollama、LM Studio、vLLM、OpenRouter、GitHub Models），
    而不必是 api.openai.com。每个教程章节都共用这同一个 `_default_client()`
    形状 —— 这里是代表性测试；其余 22 章的 main.py 用的是完全相同的构造。
    """
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")
    monkeypatch.setenv("LLM_MODEL", "qwen2.5:14b")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")

    client = _default_client()

    from agent_framework.openai import OpenAIChatClient

    assert isinstance(client, OpenAIChatClient)
    assert client.base_url == "http://localhost:11434/v1"


def test_default_client_defaults_to_no_base_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)

    client = _default_client()

    assert not client.base_url


@pytest.mark.asyncio
async def test_ask_returns_canned_answer() -> None:
    client = CannedChatClient("Paris.")
    agent = build_agent(client=client)
    answer = await ask(agent, "What is the capital of France?")
    assert answer == "Paris."
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_system_instructions_reach_chat_client() -> None:
    client = CannedChatClient("Canberra.")
    agent = build_agent(client=client)
    await ask(agent, "What is the capital of Australia?")

    assert client.calls, "期望至少有一次对 chat client 的调用"
    sent_messages, sent_options = client.calls[0]
    # MAF 可以把指令放在 ChatOptions 里，也可以作为消息列表中的一条 system 消息。
    options_instructions = sent_options.get("instructions", "") if isinstance(sent_options, Mapping) else ""
    system_texts = [m.text for m in sent_messages if str(m.role).lower() == "system"]
    combined = options_instructions + " " + " ".join(system_texts)
    assert INSTRUCTIONS in combined, (
        f"缺少系统指令 —— options={options_instructions!r} messages={system_texts!r}"
    )


@pytest.mark.asyncio
async def test_user_question_reaches_chat_client() -> None:
    client = CannedChatClient("Ottawa.")
    agent = build_agent(client=client)
    await ask(agent, "What is the capital of Canada?")

    messages, _ = client.calls[0]
    sent = [m.text for m in messages if str(m.role).lower() == "user"]
    assert sent == ["What is the capital of Canada?"]


@pytest.mark.asyncio
async def test_run_out_of_canned_responses_raises() -> None:
    client = CannedChatClient()  # 没有预置回答
    agent = build_agent(client=client)
    with pytest.raises(AssertionError, match="ran out of responses"):
        await ask(agent, "nothing to say")


# ─────────────────── 回放测试（无需凭据，可在 CI 中运行） ────


@pytest.mark.asyncio
async def test_replay_answers_capital_of_france(monkeypatch: pytest.MonkeyPatch) -> None:
    """回放 tests/fixtures/replay/ —— 不走网络、不需凭据。

    曾针对真实 LLM 录制过一次（即下方的 test_real_llm_answers_capital_of_france，
    以 RECORD=true 运行），随后提交入库。正是它让本章那条「真实 LLM」断言能在
    每个 PR 的 CI 中运行。
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"{FIXTURES_DIR} 中没有已录制的夹具 —— 请先以 RECORD=true 运行")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    agent = build_agent()
    answer = await ask(agent, "What is the capital of France? Answer with the city name only.")
    assert "paris" in answer.lower(), f"期望答案中出现 Paris，实际为：{answer!r}"


# ─────────────────── 集成测试（访问真实 LLM） ────────────────


def _llm_available() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        key = os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        return bool(key and endpoint)
    key = os.environ.get("OPENAI_API_KEY", "")
    return bool(key) and not key.startswith("sk-your-")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_real_llm_answers_capital_of_france() -> None:
    """访问真实 LLM，以证明整条链路端到端可用。"""
    agent = build_agent()
    answer = await ask(agent, "What is the capital of France? Answer with the city name only.")
    assert "paris" in answer.lower(), f"期望答案中出现 Paris，实际为：{answer!r}"
