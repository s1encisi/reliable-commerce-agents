"""模型输出审核测试，覆盖纯分类规则及流式感知中间件，无外部调用。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from shared.config import settings
from shared.guardrails.moderation import ModerationCategory, classify
from shared.guardrails.moderation_middleware import OutputModerationMiddleware

# ─────────────────────── classify() ────────────────────────────────────────


def test_classify_returns_empty_set_for_clean_text() -> None:
    assert classify("Here are three wireless headphones under $100.") == set()


def test_classify_flags_self_harm() -> None:
    assert ModerationCategory.SELF_HARM in classify("I want to kill myself")


def test_classify_flags_violence() -> None:
    assert ModerationCategory.VIOLENCE in classify("here is how to build a bomb")


def test_classify_flags_hate_harassment() -> None:
    assert ModerationCategory.HATE_HARASSMENT in classify("you're worthless and subhuman")


def test_classify_is_case_insensitive() -> None:
    assert ModerationCategory.SELF_HARM in classify("I WANT TO KILL MYSELF")


def test_classify_does_not_flag_benign_use_of_similar_words() -> None:
    # 正常电商措辞中的 killer deal，
    # 不应命中精确的自伤或暴力规则。
    assert classify("This is a killer deal on a great product!") == set()


# ─────────────────────── OutputModerationMiddleware ────────────────────────


def _chat_response(text: str) -> Any:
    return SimpleNamespace(
        messages=[SimpleNamespace(contents=[SimpleNamespace(text=text)])],
    )


def _context(*, stream: bool = False) -> Any:
    return SimpleNamespace(result=None, stream=stream, stream_result_hooks=[])


async def test_off_mode_skips_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OUTPUT_MODERATION_MODE", "off")
    middleware = OutputModerationMiddleware()
    context = _context()
    called = False

    async def call_next() -> None:
        nonlocal called
        called = True
        context.result = _chat_response("I want to kill myself")

    await middleware.process(context, call_next)
    assert called is True
    assert middleware.flagged == 0
    # 不执行分类，结果保持 call_next 设置的原值。
    assert context.result.messages[0].contents[0].text == "I want to kill myself"


async def test_observe_mode_flags_but_never_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OUTPUT_MODERATION_MODE", "observe")
    middleware = OutputModerationMiddleware()
    context = _context()

    async def call_next() -> None:
        context.result = _chat_response("I want to kill myself")

    await middleware.process(context, call_next)
    assert middleware.flagged == 1
    # 观察模式即使命中也不替换响应。
    assert context.result.messages[0].contents[0].text == "I want to kill myself"


async def test_enforce_mode_blocks_non_streaming_flagged_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OUTPUT_MODERATION_MODE", "enforce")
    middleware = OutputModerationMiddleware()
    context = _context(stream=False)

    async def call_next() -> None:
        context.result = _chat_response("here is how to build a bomb")

    await middleware.process(context, call_next)
    assert middleware.flagged == 1
    assert context.result.finish_reason == "content_filter"
    assert "flagged" in context.result.messages[0].contents[0].text


async def test_enforce_mode_leaves_clean_response_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OUTPUT_MODERATION_MODE", "enforce")
    middleware = OutputModerationMiddleware()
    context = _context(stream=False)

    async def call_next() -> None:
        context.result = _chat_response("Here are three great headphones.")

    await middleware.process(context, call_next)
    assert middleware.flagged == 0
    assert context.result.messages[0].contents[0].text == "Here are three great headphones."


async def test_streaming_flagged_response_is_logged_not_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """结果钩子触发时分块已发送，流式路径只能标记，不能替换已展示文本。"""
    monkeypatch.setattr(settings, "OUTPUT_MODERATION_MODE", "enforce")
    middleware = OutputModerationMiddleware()

    class _FakeResponseStream:
        pass

    from shared.guardrails import moderation_middleware as mm

    monkeypatch.setattr(mm, "ResponseStream", _FakeResponseStream)

    context = _context(stream=True)

    async def call_next() -> None:
        context.result = _FakeResponseStream()

    await middleware.process(context, call_next)

    assert len(context.stream_result_hooks) == 1
    hook = context.stream_result_hooks[0]
    flagged_response = _chat_response("I want to kill myself")
    returned = hook(flagged_response)

    assert middleware.flagged == 1
    # 钩子返回原响应，无法再阻止已发送内容。
    assert returned is flagged_response


async def test_no_result_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OUTPUT_MODERATION_MODE", "enforce")
    middleware = OutputModerationMiddleware()
    context = _context()

    async def call_next() -> None:
        context.result = None

    await middleware.process(context, call_next)
    assert middleware.flagged == 0
    assert context.result is None
