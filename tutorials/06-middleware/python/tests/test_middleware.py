"""
第 06 章 —— 中间件：测试。

仅集成测试（中间件链与 MAF 的调用机制紧耦合；把它打桩掉就失去了意义）。
每个测试都通过 .env 访问真实的 Azure OpenAI。
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import (  # noqa: E402
    FIXTURES_DIR,
    ArgValidatorMiddleware,
    LoggingAgentMiddleware,
    PiiRedactionChatMiddleware,
    ask,
    build_agent,
)

# ─────────────────── 回放测试（无需凭据，可在 CI 中运行） ────


@pytest.mark.asyncio
async def test_replay_agent_and_function_middleware_observe_weather_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回放 tests/fixtures/replay/ —— 不走网络、不需凭据。

    曾针对真实 LLM 录制过一次（以 RECORD=true 运行），随后提交入库。对应
    test_agent_middleware_observes_every_run 与
    test_function_middleware_intercepts_tool_calls，三者共用同一个
    "What's the weather in Paris?" 输入。
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"{FIXTURES_DIR} 中没有已录制的夹具 —— 请先以 RECORD=true 运行")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    logger = LoggingAgentMiddleware()
    validator = ArgValidatorMiddleware()
    agent = build_agent(logger, validator, PiiRedactionChatMiddleware())
    await ask(agent, "What's the weather in Paris?")
    assert logger.events == ["agent:before", "agent:after"]
    assert "Paris" in validator.invocations


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
async def test_agent_middleware_observes_every_run() -> None:
    logger = LoggingAgentMiddleware()
    agent = build_agent(logger, ArgValidatorMiddleware(), PiiRedactionChatMiddleware())
    await ask(agent, "What's the weather in Paris?")
    assert logger.events == ["agent:before", "agent:after"]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_function_middleware_intercepts_tool_calls() -> None:
    validator = ArgValidatorMiddleware()
    agent = build_agent(LoggingAgentMiddleware(), validator, PiiRedactionChatMiddleware())
    await ask(agent, "What's the weather in Paris?")
    assert "Paris" in validator.invocations


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_function_middleware_short_circuits_forbidden_city() -> None:
    validator = ArgValidatorMiddleware()
    agent = build_agent(LoggingAgentMiddleware(), validator, PiiRedactionChatMiddleware())
    answer = await ask(agent, "What's the weather in Atlantis?")
    assert "Atlantis" in validator.invocations
    assert validator.blocked == ["Atlantis"]
    # 拒绝消息（或它的一句自然语言改写）应当浮现出来。
    assert any(token in answer.lower() for token in ("refused", "can't", "cannot", "not supported", "no weather"))


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_chat_middleware_redacts_card_number_before_llm() -> None:
    redactor = PiiRedactionChatMiddleware()
    agent = build_agent(LoggingAgentMiddleware(), ArgValidatorMiddleware(), redactor)
    await ask(agent, "My card is 4111-1111-1111-1111. What's the weather in Paris?")
    assert redactor.redactions >= 1, "期望卡号被脱敏"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_middleware_do_not_leak_between_runs() -> None:
    logger1 = LoggingAgentMiddleware()
    logger2 = LoggingAgentMiddleware()
    agent1 = build_agent(logger1, ArgValidatorMiddleware(), PiiRedactionChatMiddleware())
    agent2 = build_agent(logger2, ArgValidatorMiddleware(), PiiRedactionChatMiddleware())

    await ask(agent1, "What's the weather in Paris?")
    await ask(agent2, "What's the weather in Tokyo?")

    assert logger1.events == ["agent:before", "agent:after"]
    assert logger2.events == ["agent:before", "agent:after"]
