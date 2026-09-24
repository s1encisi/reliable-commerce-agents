"""客户端中途断开后，已经生成的助手文本仍需持久化。

Starlette 可能直接取消 SSE 生成器，早于应用自己的断连轮询，导致
末尾持久化代码未执行。测试先读取真实分块，再在暂停点注入
CancelledError，验证独立保存任务仍把部分答案写入真实 PostgreSQL。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from orchestrator.routes import chat as chat_module
from orchestrator.routes.chat import ChatRequest, chat_stream


class _NeverDisconnectsRequest:
    """只模拟 is_disconnected 接口；取消由测试直接注入，不自行触发断连。"""

    async def is_disconnected(self) -> bool:
        return False


async def _wait_for_persist_tasks_to_drain(timeout_s: float = 2.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while chat_module._PENDING_PERSIST_TASKS and loop.time() < deadline:
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_partial_response_is_persisted_after_mid_stream_cancellation(
    clean_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    import orchestrator.agent as orch_agent_mod

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)

    user_id = uuid.uuid4()
    await clean_db.execute(
        """INSERT INTO users (id, email, password_hash, name, role)
           VALUES ($1, $2, 'hash', 'Test User', 'customer')""",
        user_id,
        "disconnect-test@example.com",
    )

    partial_text = "Here's what I found so far about the Sony WH-1000XM5: "

    async def _fake_run_agent_native_stream(agent, message, history=None, metadata_box=None):
        yield partial_text
        # 真实断连由 finally 中的 agent_task.cancel() 取消任务，
        # 通常不会继续执行到此后，
        # 测试直接注入取消以稳定复现。
        await asyncio.sleep(30)
        yield "the rest of the answer, never generated because we disconnected first"

    monkeypatch.setattr("shared.agent_host._run_agent_native_stream", _fake_run_agent_native_stream)
    monkeypatch.setattr(orch_agent_mod, "create_orchestrator_agent", lambda: object())

    user = {"sub": "disconnect-test@example.com", "role": "customer", "user_id": str(user_id)}

    response = await chat_stream(
        ChatRequest(message="how much are the Sony headphones?", mode="tool"),
        _NeverDisconnectsRequest(),
        user=user,
    )

    agen = response.body_iterator
    first_chunk = await agen.__anext__()
    assert partial_text in first_chunk, f"expected the first real chunk on the wire, got: {first_chunk!r}"

    # 在这个精确暂停点模拟 Starlette 取消，
    # 覆盖应用自身断连轮询
    # 可能来不及处理的竞态。
    with pytest.raises(asyncio.CancelledError):
        await agen.athrow(asyncio.CancelledError())

    await _wait_for_persist_tasks_to_drain()

    row = await clean_db.fetchrow(
        "SELECT content FROM messages WHERE role = 'assistant' ORDER BY created_at DESC LIMIT 1"
    )
    assert row is not None, "the partial assistant response must still be persisted after a mid-stream disconnect"
    assert row["content"] == partial_text
