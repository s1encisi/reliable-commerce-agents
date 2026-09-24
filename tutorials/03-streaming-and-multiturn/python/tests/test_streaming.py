"""
第 03 章 —— 流式输出与多轮对话：测试。

单元测试使用一个支持流式的 CannedChatClient，它一次产出一段文本分片；
集成测试访问真实 LLM。
"""

from __future__ import annotations

import os
import pathlib
import sys
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from agent_framework import BaseChatClient, Content, Message  # noqa: E402
from agent_framework._types import ChatResponse, ChatResponseUpdate, ResponseStream  # noqa: E402
from main import FIXTURES_DIR, build_agent, chat, stream_answer  # noqa: E402


class StreamingCannedClient(BaseChatClient):
    """把每条预置回答拆成 3 段文本分片产出，好让测试能对流式做断言。"""

    def __init__(self, *canned: str) -> None:
        super().__init__()
        self._responses = list(canned)
        self.call_count = 0
        self.conversation_lengths: list[int] = []

    def _inner_get_response(  # type: ignore[override]
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Awaitable[ChatResponse] | ResponseStream[ChatResponseUpdate, ChatResponse]:
        self.call_count += 1
        self.conversation_lengths.append(len(list(messages)))
        if not self._responses:
            raise AssertionError("StreamingCannedClient ran out of responses")
        text = self._responses.pop(0)

        if stream:
            parts = _split_in_three(text)

            async def _gen():
                for p in parts:
                    yield ChatResponseUpdate(contents=[Content(type="text", text=p)])

            return ResponseStream(
                _gen(),
                finalizer=lambda updates: ChatResponse(
                    messages=[
                        Message(
                            role="assistant",
                            contents=[Content(type="text", text="".join(u.text for u in updates if u.text))],
                        )
                    ]
                ),
            )

        async def _return() -> ChatResponse:
            return ChatResponse(messages=[Message(role="assistant", contents=[Content(type="text", text=text)])])

        return _return()


def _split_in_three(s: str) -> list[str]:
    if len(s) < 3:
        return [s]
    a = len(s) // 3
    b = 2 * len(s) // 3
    return [s[:a], s[a:b], s[b:]]


# ─────────── 单元测试（打桩的流式） ───────────


@pytest.mark.asyncio
async def test_stream_yields_multiple_chunks() -> None:
    client = StreamingCannedClient("Hello world, this is a longer response.")
    agent = build_agent(client=client)
    session = agent.create_session()
    chunks = await stream_answer(agent, "hi", session)
    # 按我们三分法的拆分，期望得到三个非空分片。
    assert len([c for c in chunks if c]) >= 2


@pytest.mark.asyncio
async def test_multiturn_reuses_session() -> None:
    client = StreamingCannedClient("First answer", "Second answer")
    agent = build_agent(client=client)
    await chat(agent, ["First question?", "Follow-up?"])
    # 两轮：第二轮的对话应当比第一轮更长。
    assert client.call_count == 2
    assert client.conversation_lengths[1] > client.conversation_lengths[0]


@pytest.mark.asyncio
async def test_streamed_chunks_combine_to_full_text() -> None:
    client = StreamingCannedClient("abcdefghij")
    agent = build_agent(client=client)
    session = agent.create_session()
    chunks = await stream_answer(agent, "hi", session)
    assert "".join(chunks) == "abcdefghij"


# ─────────── 回放测试（无需凭据，可在 CI 中运行） ───────────


@pytest.mark.asyncio
async def test_replay_multiturn_preserves_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """回放 tests/fixtures/replay/ —— 不走网络、不需凭据。

    曾针对真实 LLM 录制过一次（即下方的
    test_real_llm_multiturn_preserves_context，以 RECORD=true 运行），
    随后提交入库。因为是两轮，会涉及两份夹具 —— 每轮的消息历史各一份。
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"{FIXTURES_DIR} 中没有已录制的夹具 —— 请先以 RECORD=true 运行")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    agent = build_agent()
    per_turn = await chat(
        agent,
        [
            "What is Python in one line?",
            "What year was it first released? Answer with the year only.",
        ],
    )
    second_answer = "".join(per_turn[1])
    assert "1991" in second_answer, f"期望追问的回答中出现 1991，实际为：{second_answer!r}"


# ─────────── 集成测试（真实 LLM） ───────────


def _llm_available() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        return bool(
            os.environ.get("AZURE_OPENAI_ENDPOINT")
            and (os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))
        )
    key = os.environ.get("OPENAI_API_KEY", "")
    return bool(key) and not key.startswith("sk-your-")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_real_llm_multiturn_preserves_context() -> None:
    """第二轮必须能把「它」指代回第一轮的 Python。"""
    agent = build_agent()
    per_turn = await chat(
        agent,
        [
            "What is Python in one line?",
            "What year was it first released? Answer with the year only.",
        ],
    )
    second_answer = "".join(per_turn[1])
    assert "1991" in second_answer, f"期望追问的回答中出现 1991，实际为：{second_answer!r}"
