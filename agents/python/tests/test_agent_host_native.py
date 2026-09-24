"""MAF 原生执行路径测试。

覆盖历史消息转换、非流式答案、流式分块及可选真实模型集成。
真实 Azure/OpenAI 测试需要单独启用和配置，普通测试使用替身。
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

# 从仓库 .env 加载集成测试环境，
# 真实模型执行仍受相应测试条件控制。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from shared.agent_host import (  # noqa: E402
    _history_as_maf_messages,
    _rehydrate_history_from_session,
    _run_agent_native,
    _run_agent_native_stream,
)
from shared.context import current_user_email  # noqa: E402

# ─────────────────────── Pure helpers ───────────────────────


def test_history_builder_wraps_current_message_last() -> None:
    msgs = _history_as_maf_messages(
        history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        user_message="latest",
    )
    assert [str(m.role).lower() for m in msgs] == ["user", "assistant", "user"]
    assert msgs[-1].text == "latest"


def test_history_builder_accepts_none_history() -> None:
    msgs = _history_as_maf_messages(history=None, user_message="only")
    assert len(msgs) == 1
    assert msgs[0].text == "only"


def test_history_builder_skips_other_roles_and_empty_content() -> None:
    """过滤系统消息、工具消息和空载荷。"""
    msgs = _history_as_maf_messages(
        history=[
            {"role": "system", "content": "ignored"},
            {"role": "user", "content": ""},
            {"role": "user", "content": "kept"},
            {"role": "tool", "content": "ignored"},
        ],
        user_message="final",
    )
    assert [m.text for m in msgs] == ["kept", "final"]


# ─────────────────────── Session rehydration ─────────────


class _FakePool:
    def __init__(self, rows: list[dict] | None = None, raise_on_fetch: Exception | None = None) -> None:
        self._rows = rows or []
        self._raise = raise_on_fetch
        self.last_query: str | None = None
        self.last_args: tuple | None = None

    async def fetch(self, query: str, *args):
        self.last_query = query
        self.last_args = args
        if self._raise is not None:
            raise self._raise
        return self._rows


@pytest.mark.asyncio
async def test_rehydrate_returns_none_when_session_id_missing() -> None:
    assert await _rehydrate_history_from_session("") is None


@pytest.mark.asyncio
async def test_rehydrate_returns_none_without_a_caller_identity(monkeypatch, caplog) -> None:
    """缺少身份时不能读取用户历史，且必须记录日志。

    测试确保新的归属守卫不会再次产生无日志的静默短路。
    """
    fake_pool = _FakePool(rows=[{"role": "user", "content": "hello"}])
    monkeypatch.setattr("shared.db.get_pool", lambda: fake_pool)
    current_user_email.set("")

    with caplog.at_level("INFO"):
        assert await _rehydrate_history_from_session("11111111-1111-1111-1111-111111111111") is None

    assert fake_pool.last_query is None, "it must not even reach the database"
    assert "rehydrate_skipped" in caplog.text


@pytest.mark.asyncio
async def test_rehydrate_scopes_the_query_to_the_caller(monkeypatch) -> None:
    """会话标识来自请求头，查询不能只信任该标识。"""
    fake_pool = _FakePool(rows=[])
    monkeypatch.setattr("shared.db.get_pool", lambda: fake_pool)
    current_user_email.set("owner@example.com")

    await _rehydrate_history_from_session("11111111-1111-1111-1111-111111111111")

    assert "EXISTS" in (fake_pool.last_query or ""), "no ownership predicate in the query"
    assert fake_pool.last_args[-1] == "owner@example.com"


@pytest.mark.asyncio
async def test_rehydrate_reads_messages_by_conversation_id(monkeypatch) -> None:
    current_user_email.set("owner@example.com")
    rows = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi back"},
        {"role": "tool", "content": "ignored"},  # 过滤非 user/assistant 角色。
        {"role": "user", "content": ""},  # 过滤空内容。
        {"role": "user", "content": "still here"},
    ]
    fake_pool = _FakePool(rows=rows)
    monkeypatch.setattr("shared.db.get_pool", lambda: fake_pool)

    history = await _rehydrate_history_from_session("11111111-1111-1111-1111-111111111111")

    assert history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi back"},
        {"role": "user", "content": "still here"},
    ]
    # LIMIT 使用 $2 参数，不进行字符串插值。
    assert "LIMIT $2" in (fake_pool.last_query or "")
    assert fake_pool.last_args == ("11111111-1111-1111-1111-111111111111", 50, "owner@example.com")


@pytest.mark.asyncio
async def test_rehydrate_swallows_db_errors(monkeypatch) -> None:
    fake_pool = _FakePool(raise_on_fetch=RuntimeError("db down"))
    monkeypatch.setattr("shared.db.get_pool", lambda: fake_pool)
    # 有意设置身份，避免归属守卫提前返回 None，
    # 导致测试没有真正执行查询，
    # 也未覆盖目标异常路径却仍然通过。
    current_user_email.set("owner@example.com")
    assert await _rehydrate_history_from_session("any-id") is None


@pytest.mark.asyncio
async def test_rehydrate_swallows_missing_pool(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("pool not initialised")

    monkeypatch.setattr("shared.db.get_pool", _boom)
    current_user_email.set("owner@example.com")
    assert await _rehydrate_history_from_session("any-id") is None


# ─────────────────────── Native path (stubbed agent) ──────


class _FakeResponse:
    def __init__(self, text: str, additional_properties: dict | None = None) -> None:
        self.text = text
        self.additional_properties = additional_properties or {}


class _FakeStreamingUpdate:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeAgent:
    """仅提供辅助函数所用 run 签名的最小替身。"""

    def __init__(self, text: str = "stubbed-answer") -> None:
        self._text = text
        self.last_call_messages: list | None = None
        self.last_call_stream: bool | None = None
        self.last_options: dict | None = None

    def run(self, messages=None, *, stream: bool = False, options=None, **_kwargs):
        self.last_call_messages = list(messages or [])
        self.last_call_stream = stream
        self.last_options = options

        if stream:

            async def _gen():
                # 返回两个分块，验证增量输出。
                for piece in [self._text[: len(self._text) // 2], self._text[len(self._text) // 2 :]]:
                    yield _FakeStreamingUpdate(piece)

            return _gen()

        async def _return():
            return _FakeResponse(self._text)

        return _return()


@pytest.mark.asyncio
async def test_run_agent_native_returns_response_text() -> None:
    agent = _FakeAgent("Paris is the capital of France.")
    text = await _run_agent_native(agent, "What's the capital of France?")
    assert text == "Paris is the capital of France."


@pytest.mark.asyncio
async def test_run_agent_native_pins_temperature() -> None:
    """每次运行必须携带配置温度，避免隐式使用提供方默认值。"""
    from shared.config import settings

    agent = _FakeAgent("ok")
    await _run_agent_native(agent, "hi")
    assert agent.last_options is not None
    assert agent.last_options.get("temperature") == settings.LLM_TEMPERATURE


@pytest.mark.asyncio
async def test_run_agent_native_threads_history_into_messages() -> None:
    agent = _FakeAgent("ok")
    await _run_agent_native(
        agent,
        "latest",
        history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
    )
    assert agent.last_call_stream is False
    assert agent.last_call_messages is not None
    assert [m.text for m in agent.last_call_messages] == ["hi", "hello", "latest"]


@pytest.mark.asyncio
async def test_run_agent_native_fills_metadata_box_from_additional_properties() -> None:
    class _AgentWithMetadata:
        def run(self, messages=None, *, stream: bool = False, options=None, **_kwargs):
            async def _return():
                return _FakeResponse("ok", additional_properties={"grounding": {"verified": 1}})

            return _return()

    box: dict = {}
    await _run_agent_native(_AgentWithMetadata(), "hi", metadata_box=box)
    assert box == {"grounding": {"verified": 1}}


@pytest.mark.asyncio
async def test_run_agent_native_metadata_box_untouched_when_none_passed() -> None:
    # 调用方不读取元数据时也不能抛错。
    agent = _FakeAgent("ok")
    text = await _run_agent_native(agent, "hi")
    assert text == "ok"


@pytest.mark.asyncio
async def test_run_agent_native_stream_yields_all_chunks() -> None:
    agent = _FakeAgent("Paris is the capital of France.")
    pieces = [chunk async for chunk in _run_agent_native_stream(agent, "hi")]
    assert "".join(pieces) == "Paris is the capital of France."
    assert agent.last_call_stream is True


@pytest.mark.asyncio
async def test_run_agent_native_stream_skips_empty_updates() -> None:
    """提供方可能发送空增量，辅助函数必须过滤。"""

    class _AgentWithEmptyDeltas:
        def run(self, messages=None, *, stream: bool = False, options=None, **_kwargs):
            async def _gen():
                yield _FakeStreamingUpdate("")
                yield _FakeStreamingUpdate("real")
                yield _FakeStreamingUpdate(None)  # type: ignore[arg-type]

            return _gen()

    chunks = [c async for c in _run_agent_native_stream(_AgentWithEmptyDeltas(), "hi")]
    assert chunks == ["real"]


@pytest.mark.asyncio
async def test_run_agent_native_stream_fills_metadata_box_after_exhaustion() -> None:
    class _FakeResponseStream:
        """最小 ResponseStream 替身：支持异步迭代及结束后读取最终响应。"""

        def __init__(self, chunks: list[str], final: _FakeResponse) -> None:
            self._chunks = chunks
            self._final = final

        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for c in self._chunks:
                yield _FakeStreamingUpdate(c)

        async def get_final_response(self) -> _FakeResponse:
            return self._final

    class _AgentWithStreamingMetadata:
        def run(self, messages=None, *, stream: bool = False, options=None, **_kwargs):
            return _FakeResponseStream(
                ["hi"],
                _FakeResponse("hi", additional_properties={"grounding": {"verified": 2}}),
            )

    box: dict = {}
    chunks = [c async for c in _run_agent_native_stream(_AgentWithStreamingMetadata(), "hi", metadata_box=box)]
    assert chunks == ["hi"]
    assert box == {"grounding": {"verified": 2}}


@pytest.mark.asyncio
async def test_run_agent_native_stream_metadata_box_skipped_when_stream_has_no_finalizer() -> None:
    # 普通异步生成器没有 get_final_response，也不能抛错。
    # 此前的 _FakeAgent 测试返回的是裸生成器，
    # 而非真实 MAF ResponseStream。
    agent = _FakeAgent("ok")
    box: dict = {}
    chunks = [c async for c in _run_agent_native_stream(agent, "hi", metadata_box=box)]
    assert "".join(chunks) == "ok"
    assert box == {}


# ─────────────────────── Live LLM parity ───────────────────


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
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_native_path_against_real_llm() -> None:
    """可选真实模型测试：验证原生路径在 Azure/OpenAI 上能够生成合理回答。"""
    from agent_framework import Agent

    from shared.factory import get_chat_client

    agent = Agent(
        get_chat_client(),
        instructions="You are a concise geography assistant. Keep answers to one short sentence.",
        name="native-test-agent",
    )
    answer = await _run_agent_native(agent, "What is the capital of France?")
    assert "paris" in answer.lower(), f"expected Paris in answer, got {answer!r}"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_native_path_streams_real_llm_output() -> None:
    from agent_framework import Agent

    from shared.factory import get_chat_client

    agent = Agent(
        get_chat_client(),
        instructions="You are a concise assistant. Keep answers to one short sentence.",
        name="native-stream-agent",
    )
    pieces = [chunk async for chunk in _run_agent_native_stream(agent, "Say 'hi'.")]
    assert pieces, "expected at least one streaming update"
    combined = "".join(pieces).lower()
    assert "hi" in combined
