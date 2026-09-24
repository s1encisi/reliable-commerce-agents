"""
第 23 章 —— A2A 协议：测试。

- 单元测试直接演练订单查询函数与 A2A 传输（agent-card、/message:send、
  /message:stream）—— 不涉及 LLM，因为传输才是本章讲授的概念。
- 集成测试访问真实 LLM，并断言它会调用专业智能体工具。
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
    AGENT_CARD,
    FIXTURES_DIR,
    _lookup_order,
    ask,
    build_agent,
    call_order_specialist,
    demo_fetch_agent_card,
    demo_stream_call,
)

# ─────────────────── 订单查询单元测试（无 LLM、无 HTTP） ─────


def test_lookup_order_returns_known_status() -> None:
    result = _lookup_order("What's the status of ORD-1001?")
    assert "Shipped" in result


def test_lookup_order_handles_unknown_order() -> None:
    result = _lookup_order("What's the status of ORD-9999?")
    assert "No order found" in result


def test_lookup_order_requires_order_id() -> None:
    result = _lookup_order("How's my order doing?")
    assert "No order id found" in result


def test_lookup_order_is_case_insensitive() -> None:
    assert _lookup_order("ord-1001") == _lookup_order("ORD-1001")


# ─────────────────── A2A 传输单元测试（无 LLM） ──────────────
# 这些测试经由 httpx 的 ASGITransport 演练真实的 Starlette 应用 ——
# 真实的路由/JSON/SSE，无 LLM，无网络套接字。原因见 README。


@pytest.mark.asyncio
async def test_agent_card_endpoint_returns_identity() -> None:
    card = await demo_fetch_agent_card()
    assert card == AGENT_CARD
    assert card["name"] == "order-lookup"


@pytest.mark.asyncio
async def test_call_order_specialist_tool_hits_message_send() -> None:
    # @tool 通过 .func 暴露原始协程函数 —— 与第 02 章
    # get_product_price.func(...) 相同的解包方式。
    result = await call_order_specialist.func("What's the status of ORD-1002?")
    assert "Processing" in result


@pytest.mark.asyncio
async def test_message_stream_emits_done_sentinel() -> None:
    chunks = await demo_stream_call("What's the status of ORD-1003?")
    assert len(chunks) == 1
    assert "Delivered" in chunks[0]


@pytest.mark.asyncio
async def test_message_stream_raises_on_error_sentinel() -> None:
    with pytest.raises(RuntimeError, match=r"\[ERROR"):
        await demo_stream_call("")


# ─────────────────── 智能体接线 ────────────────────────────────


def test_agent_has_specialist_tool_registered() -> None:
    agent = build_agent(client=object())  # client 不会被调用；我们只检查结构
    tool_names = [getattr(t, "name", None) for t in agent.default_options.get("tools") or []]
    assert "call_order_specialist" in tool_names


# ─────────────────── 回放测试（无需凭据，可在 CI 中运行） ────


@pytest.mark.asyncio
async def test_replay_calls_order_specialist(monkeypatch: pytest.MonkeyPatch) -> None:
    """播放 tests/fixtures/replay/ —— 不访问真实 LLM，也不需要凭据。

    （对本地 Starlette 专业智能体的进程内 A2A 调用仍然会发生 ——
    那不是 LLM，而是上面那些单元测试直接演练的同一个本地传输。）

    针对真实 LLM 录制过一次（即下面以 RECORD=true 运行的
    test_real_llm_calls_order_specialist），然后提交进仓库。
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"no recorded fixtures in {FIXTURES_DIR} — run with RECORD=true first")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    agent = build_agent()
    answer = await ask(agent, "What's the status of order ORD-1001?")
    lowered = answer.lower()
    assert "shipped" in lowered or "2026-08-22" in lowered, f"expected order-status data in the answer, got: {answer!r}"


# ─────────────────── 真实 LLM 集成测试 ────────────────


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
async def test_real_llm_calls_order_specialist() -> None:
    """LLM 应当看到该工具，并在被问及订单时使用它。"""
    agent = build_agent()
    answer = await ask(agent, "What's the status of order ORD-1001?")
    lowered = answer.lower()
    assert "shipped" in lowered or "2026-08-22" in lowered, f"expected order-status data in the answer, got: {answer!r}"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_skips_tool_for_unrelated_question() -> None:
    """对于无关问题，订单查询工具绝不应出现在回答中。"""
    agent = build_agent()
    answer = await ask(agent, "What is the capital of France? Answer with only the city name.")
    assert "paris" in answer.lower()
    # 预置的订单字符串绝不能渗进一个与订单无关的回答里。
    assert "shipped" not in answer.lower()
