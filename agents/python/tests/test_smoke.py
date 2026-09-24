"""验证共享测试夹具本身能工作的最小冒烟测试。"""

from __future__ import annotations

import pytest


def test_sample_env(sample_env: dict[str, str]) -> None:
    assert sample_env["LLM_PROVIDER"] == "openai"
    assert sample_env["LLM_MODEL"] == "gpt-4.1"
    assert sample_env["OTEL_ENABLED"] == "false"
    assert len(sample_env["JWT_SECRET"]) >= 32


def test_fake_chat_client_round_trip(fake_chat_client) -> None:
    """客户端替身确定性地排队、取出响应并记录输入。"""
    fake_chat_client.enqueue("first", "second")
    assert fake_chat_client.call_count == 0


@pytest.mark.asyncio
async def test_fake_chat_client_async_flow(fake_chat_client) -> None:
    fake_chat_client.enqueue("hello from fake")
    result = await fake_chat_client.complete([{"role": "user", "content": "hi"}])
    assert result == "hello from fake"
    assert fake_chat_client.call_count == 1
    assert fake_chat_client.received_prompts[0][0]["content"] == "hi"


@pytest.mark.asyncio
async def test_fake_chat_client_raises_without_queue(fake_chat_client) -> None:
    with pytest.raises(RuntimeError, match="no enqueued responses"):
        await fake_chat_client.complete([{"role": "user", "content": "hi"}])
