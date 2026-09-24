"""专业智能体必须收到当前会话的历史。

浏览器不发送会话头时，编排器应从业务会话建立 ContextVar，再将标识
传播到 A2A 请求；否则专业智能体会静默以空上下文处理追问。

手工设置头或 ContextVar 的单元测试无法发现断链，因此这里驱动真实
HTTP 请求，分别验证标识传播和实际历史恢复。
"""

from __future__ import annotations

import uuid

import pytest
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
)
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import orchestrator.agent as orch_mod
from orchestrator.agent import ORCHESTRATOR_TOOLS
from orchestrator.routes import optional_auth, router
from shared.context import current_user_email, current_user_role


def _text(text: str) -> ChatResponse:
    return ChatResponse(
        messages=[Message(role="assistant", contents=[Content.from_text(text=text)])],
        response_id=str(uuid.uuid4()),
        finish_reason="stop",
    )


class _RoutingClient(FunctionInvocationLayer, BaseChatClient):
    """第一轮调用专业智能体，第二轮回答，模拟真实工具流程。"""

    def __init__(self, specialist: str, forwarded: str) -> None:
        super().__init__()
        self._responses = [
            ChatResponse(
                messages=[
                    Message(
                        role="assistant",
                        contents=[
                            Content.from_function_call(
                                call_id="c1",
                                name="call_specialist_agent",
                                arguments={"agent_name": specialist, "message": forwarded},
                            )
                        ],
                    )
                ],
                response_id=str(uuid.uuid4()),
                finish_reason="tool_calls",
            ),
            _text("The Sony WH-1000XM5 lasts about 30 hours."),
        ]

    async def _next(self) -> ChatResponse:
        return self._responses.pop(0)

    def _inner_get_response(self, *, messages, stream: bool, options=None, **_):
        if stream:

            async def _gen():
                response = await self._next()
                for msg in response.messages:
                    yield ChatResponseUpdate(role=msg.role, contents=msg.contents, author_name=msg.author_name)

            return self._build_response_stream(_gen())
        return self._next()


def _capture_a2a() -> tuple[object, dict]:
    """拦截出站 A2A POST，记录请求头和请求体。"""
    from unittest.mock import AsyncMock, MagicMock

    resp = MagicMock()
    resp.json.return_value = {"response": "About 30 hours."}
    resp.raise_for_status = MagicMock()

    captured: dict = {}

    async def _post(url, *, json=None, headers=None, **_kw):
        captured["headers"] = headers or {}
        captured["json"] = json
        return resp

    instance = AsyncMock()
    instance.__aenter__.return_value = instance
    instance.__aexit__.return_value = None
    instance.post = _post
    return MagicMock(return_value=instance), captured


async def _seed_prior_turn(db, user_id: uuid.UUID, conversation_id: uuid.UUID, email: str) -> None:
    await db.execute(
        """INSERT INTO users (id, email, password_hash, name, role)
           VALUES ($1, $2, 'hash', 'Test User', 'customer')""",
        user_id,
        email,
    )
    await db.execute(
        "INSERT INTO conversations (id, user_id, title) VALUES ($1, $2, 'headphones')",
        conversation_id,
        user_id,
    )
    await db.execute(
        """INSERT INTO messages (conversation_id, role, content)
           VALUES ($1, 'user', 'show me noise cancelling headphones')""",
        conversation_id,
    )
    await db.execute(
        """INSERT INTO messages (conversation_id, role, content)
           VALUES ($1, 'assistant', 'The Sony WH-1000XM5 is a great option.')""",
        conversation_id,
    )


@pytest.mark.asyncio
async def test_specialist_call_carries_the_conversation_id_as_session_id(
    clean_db, monkeypatch: pytest.MonkeyPatch, sample_env: dict
) -> None:
    """浏览器请求必须转发真实会话标识。

    断言出站请求头，而非只检查 ContextVar；流式路径还跨越任务创建边界。
    """
    user_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    email = "issue9@example.com"
    await _seed_prior_turn(clean_db, user_id, conversation_id, email)

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})

    client = _RoutingClient("product-discovery", "battery life of the Sony WH-1000XM5")
    agent = Agent(client=client, instructions="test", name="orchestrator", tools=ORCHESTRATOR_TOOLS)
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: agent)

    async def _fake_auth():
        current_user_email.set(email)
        current_user_role.set("customer")
        return {"sub": email, "role": "customer", "user_id": str(user_id)}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[optional_auth] = _fake_auth

    mock_class, captured = _capture_a2a()
    monkeypatch.setattr("orchestrator.agent.httpx.AsyncClient", mock_class)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        resp = await http.post(
            "/api/chat",
            json={"message": "which one has the longest battery life?", "conversation_id": str(conversation_id)},
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 200
    assert captured, "the orchestrator never called a specialist"

    sent = captured["headers"].get("x-session-id", "")
    assert sent == str(conversation_id), (
        f"the specialist got x-session-id={sent!r}, so it cannot rehydrate anything. "
        "The browser never sends this header, so chat.py must set "
        "current_session_id from the conversation it just resolved."
    )


@pytest.mark.asyncio
async def test_specialist_rehydrates_the_prior_turn_from_that_session_id(
    clean_db, monkeypatch: pytest.MonkeyPatch, sample_env: dict
) -> None:
    """验证传播的标识确实能够恢复上一轮历史。

    仅证明标识到达或仅证明查询可用，都不足以保证完整追问链路。
    """
    from shared.agent_host import _rehydrate_history_from_session

    user_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    email = "issue9-rehydrate@example.com"
    await _seed_prior_turn(clean_db, user_id, conversation_id, email)
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    monkeypatch.setattr("shared.db.get_pool", lambda: clean_db)
    # 专业智能体从转发的 x-user-email 设置当前用户。
    current_user_email.set(email)

    history = await _rehydrate_history_from_session(str(conversation_id))

    assert history, "a real conversation id returned no history"
    assert [h["role"] for h in history] == ["user", "assistant"]
    assert "Sony WH-1000XM5" in history[-1]["content"]


@pytest.mark.asyncio
async def test_streaming_turn_also_carries_the_session_id(
    clean_db, monkeypatch: pytest.MonkeyPatch, sample_env: dict
) -> None:
    """流式任务创建后不能丢失会话标识。

    create_task 在创建时复制上下文；因此测试实际出站请求头，
    不只依赖端点设置过 ContextVar 这一事实。
    """
    user_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    email = "issue9-stream@example.com"
    await _seed_prior_turn(clean_db, user_id, conversation_id, email)

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})

    client = _RoutingClient("product-discovery", "battery life of the Sony WH-1000XM5")
    agent = Agent(client=client, instructions="test", name="orchestrator", tools=ORCHESTRATOR_TOOLS)
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: agent)

    async def _fake_auth():
        current_user_email.set(email)
        current_user_role.set("customer")
        return {"sub": email, "role": "customer", "user_id": str(user_id)}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[optional_auth] = _fake_auth

    # 流式调用使用 client.stream 而非 post，
    # 因此拦截 stream，记录后再令其失败，
    # 让调用退回阻塞路径，
    # 无需构造完整 SSE 内容；
    # 本测试只关心已捕获的请求头。
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}

    def _stream(_method, _url, *, json=None, headers=None, **_kw):
        captured["headers"] = headers or {}
        raise RuntimeError("recorded")

    instance = AsyncMock()
    instance.__aenter__.return_value = instance
    instance.__aexit__.return_value = None
    instance.stream = _stream
    instance.post = AsyncMock(side_effect=RuntimeError("recorded"))
    monkeypatch.setattr("orchestrator.agent.httpx.AsyncClient", MagicMock(return_value=instance))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        async with http.stream(
            "POST",
            "/api/chat/stream",
            json={"message": "which one has the longest battery life?", "conversation_id": str(conversation_id)},
            headers={"Authorization": "Bearer fake"},
        ) as resp:
            assert resp.status_code == 200
            async for _ in resp.aiter_lines():
                pass

    assert captured, "the orchestrator never called a specialist on the streaming path"
    assert captured["headers"].get("x-session-id", "") == str(conversation_id)


@pytest.mark.asyncio
async def test_assistant_turn_is_persisted_before_done_is_yielded(
    clean_db, monkeypatch: pytest.MonkeyPatch, sample_env: dict
) -> None:
    """[DONE] 到达后立即追问，也必须读到刚完成的助手消息。

    助手消息应在完成标记前持久化。测试断言服务端顺序，因为本地写入
    竞态常碰巧成功，而 ASGITransport 又会缓冲整个响应，客户端竞速
    无法可靠暴露该回归。
    """
    user_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    email = "issue9-durable@example.com"
    await _seed_prior_turn(clean_db, user_id, conversation_id, email)

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {})

    client = _RoutingClient("product-discovery", "unused")
    client._responses = [_text("About 30 hours on a full charge.")]
    agent = Agent(client=client, instructions="test", name="orchestrator")
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: agent)

    import orchestrator.routes.chat as chat_mod

    order: list[str] = []

    real_persist = chat_mod._persist_assistant_turn

    async def _recording_persist(**kwargs):
        await real_persist(**kwargs)
        order.append("persisted")

    monkeypatch.setattr(chat_mod, "_persist_assistant_turn", _recording_persist)

    real_streaming_response = chat_mod.StreamingResponse

    def _recording_response(content, **kwargs):
        async def _wrapped():
            async for chunk in content:
                if "[DONE]" in chunk:
                    order.append("done")
                yield chunk

        return real_streaming_response(_wrapped(), **kwargs)

    monkeypatch.setattr(chat_mod, "StreamingResponse", _recording_response)

    async def _fake_auth():
        current_user_email.set(email)
        current_user_role.set("customer")
        return {"sub": email, "role": "customer", "user_id": str(user_id)}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[optional_auth] = _fake_auth

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        async with http.stream(
            "POST",
            "/api/chat/stream",
            json={"message": "how long does its battery last?", "conversation_id": str(conversation_id)},
            headers={"Authorization": "Bearer fake"},
        ) as resp:
            assert resp.status_code == 200
            async for _ in resp.aiter_lines():
                pass

    assert order == ["persisted", "done"], f"expected the turn to be durable before [DONE] was yielded, got {order}"

    rows = await clean_db.fetch(
        """SELECT content FROM messages
           WHERE conversation_id = $1 AND role = 'assistant'
           ORDER BY created_at""",
        conversation_id,
    )
    assert [r["content"] for r in rows][-1] == "About 30 hours on a full charge."


@pytest.mark.asyncio
async def test_anonymous_caller_cannot_bind_someone_elses_conversation(
    clean_db, monkeypatch: pytest.MonkeyPatch, sample_env: dict
) -> None:
    """匿名请求不能把猜测的 UUID 绑定为他人的会话。

    body.conversation_id 来自客户端。匿名路径若直接转发它，专业智能体
    就可能读取其他用户的历史。入口绑定与历史恢复查询都需要归属校验；
    本测试覆盖入口侧的防护。
    """
    victim_id, victim_conversation = uuid.uuid4(), uuid.uuid4()
    await _seed_prior_turn(clean_db, victim_id, victim_conversation, "victim@example.com")

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})

    client = _RoutingClient("product-discovery", "anything")
    agent = Agent(client=client, instructions="test", name="orchestrator", tools=ORCHESTRATOR_TOOLS)
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: agent)

    async def _anon_auth():
        current_user_email.set("")
        current_user_role.set("customer")
        return {"sub": "", "role": "customer", "user_id": "", "anonymous": True}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[optional_auth] = _anon_auth

    mock_class, captured = _capture_a2a()
    monkeypatch.setattr("orchestrator.agent.httpx.AsyncClient", mock_class)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        resp = await http.post(
            "/api/chat",
            json={"message": "what did we discuss?", "conversation_id": str(victim_conversation)},
        )

    assert resp.status_code == 200
    assert captured, "the orchestrator never called a specialist"
    assert captured["headers"].get("x-session-id", "") == "", (
        "an anonymous caller had a conversation id they do not own forwarded to a specialist as a session id"
    )


@pytest.mark.asyncio
async def test_rehydration_refuses_a_conversation_the_caller_does_not_own(
    clean_db, monkeypatch: pytest.MonkeyPatch, sample_env: dict
) -> None:
    """历史查询本身也必须校验用户归属。

    即使其他调用点转发了未经验证的会话标识，也不能读取他人会话。
    """
    from shared.agent_host import _rehydrate_history_from_session

    victim_id, victim_conversation = uuid.uuid4(), uuid.uuid4()
    await _seed_prior_turn(clean_db, victim_id, victim_conversation, "victim2@example.com")

    other_id = uuid.uuid4()
    await clean_db.execute(
        """INSERT INTO users (id, email, password_hash, name, role)
           VALUES ($1, 'attacker@example.com', 'hash', 'Attacker', 'customer')""",
        other_id,
    )

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    monkeypatch.setattr("shared.db.get_pool", lambda: clean_db)
    current_user_email.set("attacker@example.com")

    leaked = await _rehydrate_history_from_session(str(victim_conversation))

    # 返回空列表而非 None，表明查询执行但没有匹配，
    # 这是需要验证的隔离性质。两者都为假值，
    # 调用方均会退化为无历史运行。
    assert not leaked, f"another user's conversation leaked: {leaked}"
