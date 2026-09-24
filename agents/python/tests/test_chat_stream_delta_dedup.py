"""工具模式不能把专业智能体的实时预览重复保存为最终答案。

旧路径把专业智能体 delta 和编排器重述一起累加，导致正文和卡片
重复。这里驱动真实聊天流与 PostgreSQL，断言实际 messages.content，
不只检查内存 SSE 帧。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from orchestrator.routes import optional_auth, router
from shared.context import current_stream_queue, current_user_email, current_user_role


@pytest.mark.asyncio
async def test_specialist_delta_preview_is_not_persisted_into_the_final_message(
    clean_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    import orchestrator.agent as orch_agent_mod

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)

    user_id = uuid.uuid4()
    await clean_db.execute(
        """INSERT INTO users (id, email, password_hash, name, role)
           VALUES ($1, $2, 'hash', 'Test User', 'customer')""",
        user_id,
        "delta-dedup@example.com",
    )

    async def _fake_auth():
        current_user_email.set("delta-dedup@example.com")
        current_user_role.set("customer")
        return {"sub": "delta-dedup@example.com", "role": "customer", "user_id": str(user_id)}

    # 模拟专业智能体工具调用尚未结束时，
    # 它自己的文本已经被推入共享队列。
    # 增量内容原样包含卡片围栏，
    # 与真实专业智能体转发方式一致。随后，
    # 编排器自己的模型流会生成
    # 措辞不同但包含相同卡片的最终文本，
    # 符合其提示词中的卡片透传约定。
    specialist_text = (
        "The Sony WH-1000XM5 is $79.99.\n\n```product\n"
        '{"id":"11111111-1111-1111-1111-111111111111","name":"Sony WH-1000XM5","price":79.99}\n```'
    )
    orchestrator_text = (
        "Here's what I found: the Sony WH-1000XM5 headphones are available for $79.99.\n\n```product\n"
        '{"id":"11111111-1111-1111-1111-111111111111","name":"Sony WH-1000XM5","price":79.99}\n```'
    )

    async def _fake_run_agent_native_stream(agent, message, history=None, metadata_box=None):
        queue = current_stream_queue.get()
        if queue is not None:
            queue.put_nowait(("delta", "product-discovery", specialist_text))
        yield orchestrator_text

    monkeypatch.setattr("shared.agent_host._run_agent_native_stream", _fake_run_agent_native_stream)
    monkeypatch.setattr(orch_agent_mod, "create_orchestrator_agent", lambda: object())

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[optional_auth] = _fake_auth

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        resp = await http.post(
            "/api/chat/stream",
            json={"message": "how much are the Sony headphones?", "mode": "tool"},
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 200
    body = resp.text

    # 实时 SSE 仍保留专业智能体预览，
    # 只消除重复持久化，不删除预览体验。
    assert "event: delta" in body
    assert specialist_text.splitlines()[0] in body  # 增量帧文本确实已发送。

    row = await clean_db.fetchrow(
        "SELECT content FROM messages WHERE role = 'assistant' ORDER BY created_at DESC LIMIT 1"
    )
    assert row is not None
    persisted = row["content"]

    assert persisted == orchestrator_text, (
        "the persisted message must be exactly the orchestrator's own text — "
        "no specialist delta preview merged in front of or behind it"
    )
    # 最终保存的卡片只能出现一次，
    # 不能分别从预览和编排器答案各保存一份。
    assert persisted.count("```product") == 1
