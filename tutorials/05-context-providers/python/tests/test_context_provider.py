"""
第 05 章 —— 上下文提供器：测试。

单元测试直接驱动该提供器，并捕获最终送达 LLM 的内容。
集成测试证明真实的 Azure OpenAI 在注入上下文后能正确作答。
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
from main import FIXTURES_DIR, INSTRUCTIONS, UserProfileProvider, ask, build_agent  # noqa: E402


class CannedChatClient(BaseChatClient):
    """记录 options 与 messages 的 chat client，好让我们断言提供器注入了什么。"""

    def __init__(self, *canned: str) -> None:
        super().__init__()
        self._responses = list(canned)
        self.calls: list[tuple[list[Message], dict[str, Any]]] = []

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
            raise AssertionError("no canned responses")
        text = self._responses.pop(0)

        async def _return() -> ChatResponse:
            return ChatResponse(messages=[Message(role="assistant", contents=[Content(type="text", text=text)])])

        return _return()


# ─────────── 单元测试（不涉及 LLM） ───────────


@pytest.mark.asyncio
async def test_provider_injects_user_into_instructions() -> None:
    client = CannedChatClient("hi Alice")
    provider = UserProfileProvider(email="alice@example.com", name="Alice", loyalty_tier="gold")
    agent = build_agent(provider, client=client)

    await ask(agent, "hello")

    # 恰好一次 chat 调用。
    assert len(client.calls) == 1
    _, options = client.calls[0]
    instructions = options.get("instructions") or ""
    assert INSTRUCTIONS in instructions
    assert "Alice" in instructions
    assert "gold" in instructions
    assert "alice@example.com" in instructions


@pytest.mark.asyncio
async def test_provider_populates_state_dict() -> None:
    """该提供器写入 state 字典的条目，应当带上供工具使用的结构化用户数据。"""
    provider = UserProfileProvider(email="bob@example.com", name="Bob")
    state: dict[str, Any] = {}

    class FakeContext:
        def __init__(self) -> None:
            self.instructions: list[tuple[str, str]] = []

        def extend_instructions(self, source_id: str, text: str | Sequence[str]) -> None:
            if isinstance(text, str):
                self.instructions.append((source_id, text))

    ctx = FakeContext()
    await provider.before_run(agent=None, session=None, context=ctx, state=state)  # type: ignore[arg-type]

    assert state["user"]["email"] == "bob@example.com"
    assert state["user"]["name"] == "Bob"
    assert any("Bob" in text for _, text in ctx.instructions)


@pytest.mark.asyncio
async def test_multiple_users_see_different_context() -> None:
    """每位用户各自全新的提供器 + 智能体，上下文绝不能在他们之间泄漏。"""
    alice_client = CannedChatClient("hi alice")
    alice = build_agent(
        UserProfileProvider(email="alice@example.com", name="Alice", loyalty_tier="gold"),
        client=alice_client,
    )
    await ask(alice, "hello")

    bob_client = CannedChatClient("hi bob")
    bob = build_agent(
        UserProfileProvider(email="bob@example.com", name="Bob", loyalty_tier="silver"),
        client=bob_client,
    )
    await ask(bob, "hello")

    alice_instructions = alice_client.calls[0][1].get("instructions", "")
    bob_instructions = bob_client.calls[0][1].get("instructions", "")

    assert "Alice" in alice_instructions and "Bob" not in alice_instructions
    assert "Bob" in bob_instructions and "Alice" not in bob_instructions


# ─────────── 回放测试（无需凭据，可在 CI 中运行） ───────────


@pytest.mark.asyncio
async def test_replay_uses_injected_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """回放 tests/fixtures/replay/ —— 不走网络、不需凭据。

    曾针对真实 LLM 录制过一次（即下方的 test_real_llm_uses_injected_name，
    以 RECORD=true 运行），随后提交入库。
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"{FIXTURES_DIR} 中没有已录制的夹具 —— 请先以 RECORD=true 运行")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    agent = build_agent(UserProfileProvider(email="alice@example.com", name="Alice", loyalty_tier="gold"))
    answer = await ask(agent, "Greet me by name and tell me my loyalty tier.")
    lowered = answer.lower()
    assert "alice" in lowered, f"期望答案中出现 'alice'，实际为：{answer!r}"
    assert "gold" in lowered, f"期望答案中出现 'gold' 等级，实际为：{answer!r}"


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
async def test_real_llm_uses_injected_name() -> None:
    agent = build_agent(UserProfileProvider(email="alice@example.com", name="Alice", loyalty_tier="gold"))
    answer = await ask(agent, "Greet me by name and tell me my loyalty tier.")
    lowered = answer.lower()
    assert "alice" in lowered, f"期望答案中出现 'alice'，实际为：{answer!r}"
    assert "gold" in lowered, f"期望答案中出现 'gold' 等级，实际为：{answer!r}"
