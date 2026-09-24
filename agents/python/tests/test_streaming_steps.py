"""流式执行时间线测试。

专业智能体在 DONE 前发送工具步骤；编排器解析并合并步骤帧；
运行列表按认证用户隔离，并关联真实执行步骤。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import orchestrator.agent as orch_mod
from orchestrator.agent import call_specialist_agent
from shared.config import settings
from shared.context import (
    current_session_id,
    current_steps,
    current_stream_queue,
    current_user_email,
    current_user_role,
)

# ─────────────────────── helpers ────────────────────────────────────────────


@contextlib.contextmanager
def _noop_span(*args, **kwargs):
    yield


def _set_request_ctx(
    email: str = "u@example.com",
    role: str = "customer",
    session: str = "sess-1",
) -> None:
    current_user_email.set(email)
    current_user_role.set(role)
    current_session_id.set(session)


# ─────────────────────────────────────────────────────────────────────────────
# 1. 专业智能体流输出步骤帧。
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_message_stream_emits_step_frames_before_done(
    monkeypatch: pytest.MonkeyPatch,
    sample_env: dict,
) -> None:
    """捕获的工具步骤必须在 DONE 终止标记之前输出。"""

    from httpx import ASGITransport, AsyncClient

    from shared.agent_host import create_agent_app

    # 流式替身输出文本并填充步骤列表，
    # 模拟中间件在运行中完成工具记录。
    async def _fake_stream(agent, message, history=None):
        steps = current_steps.get()
        if steps is not None:
            steps.append(
                {
                    "tool_name": "search_products",
                    "tool_input": {"query": "headphones"},
                    "tool_output": {"count": 3},
                    "status": "success",
                    "duration_ms": 42,
                }
            )
        yield "headphones found"

    monkeypatch.setattr("shared.agent_host._run_agent_native_stream", _fake_stream)
    # 跨度函数在处理器内局部导入，应通过所属模块替换。
    monkeypatch.setattr(
        "shared.telemetry.agent_run_span",
        lambda *a, **kw: contextlib.nullcontext(),
    )

    app = create_agent_app(agent=MagicMock(), agent_name="product-discovery", port=9999)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/message:stream",
            json={"message": "find headphones"},
            headers={
                "x-agent-secret": sample_env["AGENT_SHARED_SECRET"],
                "x-user-email": "u@example.com",
                "x-user-role": "customer",
                "x-session-id": "sess-1",
            },
        )

    assert resp.status_code == 200
    body = resp.text

    assert "event: step" in body, "no event: step frame in stream"
    assert "data: [DONE]" in body

    done_pos = body.index("data: [DONE]")
    step_pos = body.index("event: step")
    assert step_pos < done_pos, "step frame must appear before [DONE]"

    # 步骤 JSON 包含工具名与宿主追加的智能体标签。
    step_data_line = next(line for line in body.splitlines() if line.startswith("data: ") and "search_products" in line)
    step = json.loads(step_data_line[6:])
    assert step["tool_name"] == "search_products"
    assert step["agent"] == "product-discovery"
    assert step["duration_ms"] == 42


@pytest.mark.asyncio
async def test_message_stream_emits_a_step_before_the_text_that_follows_it(
    monkeypatch: pytest.MonkeyPatch,
    sample_env: dict,
) -> None:
    """步骤事件必须先于描述它的文本到达。

    工具先完成，模型随后叙述；不能等答案全部输出后才一次性展示时间线。
    """

    from httpx import ASGITransport, AsyncClient

    from shared.agent_host import create_agent_app

    async def _fake_stream(agent, message, history=None):
        yield "Let me check. "
        steps = current_steps.get()
        if steps is not None:
            steps.append(
                {
                    "tool_name": "search_products",
                    "tool_input": {},
                    "tool_output": {},
                    "status": "success",
                    "duration_ms": 7,
                }
            )
        yield "I found three pairs. "
        yield "They are all in stock."

    monkeypatch.setattr("shared.agent_host._run_agent_native_stream", _fake_stream)
    monkeypatch.setattr("shared.telemetry.agent_run_span", lambda *a, **kw: contextlib.nullcontext())

    app = create_agent_app(agent=MagicMock(), agent_name="product-discovery", port=9999)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/message:stream",
            json={"message": "find headphones"},
            headers={
                "x-agent-secret": sample_env["AGENT_SHARED_SECRET"],
                "x-user-email": "u@example.com",
                "x-user-role": "customer",
                "x-session-id": "sess-1",
            },
        )

    body = resp.text
    step_pos = body.index("event: step")
    assert step_pos < body.index("They are all in stock."), "the step arrived after the answer had finished writing"
    assert step_pos > body.index("Let me check."), "the step was reported before the tool that produced it had run"
    # 每步只发送一次，结束时补发不能重复
    # 循环中已经发送的步骤。
    assert body.count("event: step") == 1


@pytest.mark.asyncio
async def test_message_stream_no_steps_skips_step_frames(
    monkeypatch: pytest.MonkeyPatch,
    sample_env: dict,
) -> None:
    """没有工具调用时不应输出步骤帧。"""

    from httpx import ASGITransport, AsyncClient

    from shared.agent_host import create_agent_app

    async def _fake_stream(agent, message, history=None):
        yield "simple answer"

    monkeypatch.setattr("shared.agent_host._run_agent_native_stream", _fake_stream)
    monkeypatch.setattr(
        "shared.telemetry.agent_run_span",
        lambda *a, **kw: contextlib.nullcontext(),
    )

    app = create_agent_app(agent=MagicMock(), agent_name="review-sentiment", port=9999)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/message:stream",
            json={"message": "any question"},
            headers={
                "x-agent-secret": sample_env["AGENT_SHARED_SECRET"],
                "x-user-email": "u@example.com",
                "x-user-role": "customer",
                "x-session-id": "sess-1",
            },
        )

    assert "event: step" not in resp.text
    assert "data: [DONE]" in resp.text


# ─────────────────────────────────────────────────────────────────────────────
# 2. 编排器合并专业智能体 SSE 步骤。
# ─────────────────────────────────────────────────────────────────────────────


def _build_streaming_mock(sse_lines: list[str]):
    """构造 stream 返回指定文本行的 HTTP 客户端替身。"""

    async def _aiter():
        for line in sse_lines:
            yield line

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_lines = _aiter

    stream_ctx = MagicMock()
    stream_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    stream_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.stream = MagicMock(return_value=stream_ctx)

    return MagicMock(return_value=mock_client)


@pytest.mark.asyncio
async def test_streaming_path_merges_specialist_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """专业智能体步骤帧应合并到 current_steps 供实时展示。"""

    specialist_step = {
        "tool_name": "search_products",
        "tool_input": {"query": "headphones"},
        "tool_output": {"count": 3},
        "status": "success",
        "duration_ms": 99,
        "agent": "product-discovery",
    }
    sse_lines = [
        "data: Here are the headphones",
        "",
        "event: step",
        f"data: {json.dumps(specialist_step)}",
        "",
        "data: [DONE]",
    ]

    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})
    monkeypatch.setattr(settings, "AGENT_SHARED_SECRET", "test-secret", raising=False)
    _set_request_ctx()

    # 非空队列启用流式路径。
    queue: asyncio.Queue = asyncio.Queue()
    current_stream_queue.set(queue)

    # 为本请求创建新步骤列表。
    steps_bucket: list[dict] = []
    current_steps.set(steps_bucket)

    mock_client_class = _build_streaming_mock(sse_lines)
    with (
        patch.object(orch_mod, "a2a_call_span", _noop_span),
        patch("orchestrator.agent.httpx.AsyncClient", mock_client_class),
    ):
        result = await call_specialist_agent(
            agent_name="product-discovery",
            message="find headphones",
        )

    assert "Here are the headphones" in result
    assert len(steps_bucket) == 1
    assert steps_bucket[0]["tool_name"] == "search_products"
    assert steps_bucket[0]["agent"] == "product-discovery"
    assert steps_bucket[0]["duration_ms"] == 99


@pytest.mark.asyncio
async def test_streaming_path_ignores_malformed_step_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非法步骤 JSON 被丢弃，不应使编排器崩溃。"""

    sse_lines = [
        "data: some answer",
        "",
        "event: step",
        "data: {not valid json!!!}",
        "",
        "data: [DONE]",
    ]

    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"order-management": "http://om:8082"})
    monkeypatch.setattr(settings, "AGENT_SHARED_SECRET", "test-secret", raising=False)
    _set_request_ctx()

    queue: asyncio.Queue = asyncio.Queue()
    current_stream_queue.set(queue)
    steps_bucket: list[dict] = []
    current_steps.set(steps_bucket)

    mock_client_class = _build_streaming_mock(sse_lines)
    with (
        patch.object(orch_mod, "a2a_call_span", _noop_span),
        patch("orchestrator.agent.httpx.AsyncClient", mock_client_class),
    ):
        result = await call_specialist_agent(
            agent_name="order-management",
            message="track my order",
        )

    assert "some answer" in result
    # 非法 JSON 未合并为步骤。
    assert steps_bucket == []


@pytest.mark.asyncio
async def test_streaming_path_text_chunks_still_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """混合步骤帧时，文本仍必须进入流式队列。"""

    specialist_step = {"tool_name": "check_stock", "status": "success", "duration_ms": 5}
    sse_lines = [
        "data: chunk one",
        "",
        "event: step",
        f"data: {json.dumps(specialist_step)}",
        "",
        "data: chunk two",
        "",
        "data: [DONE]",
    ]

    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"inventory-fulfillment": "http://inv:8085"})
    monkeypatch.setattr(settings, "AGENT_SHARED_SECRET", "test-secret", raising=False)
    _set_request_ctx()

    queue: asyncio.Queue = asyncio.Queue()
    current_stream_queue.set(queue)
    steps_bucket: list[dict] = []
    current_steps.set(steps_bucket)

    mock_client_class = _build_streaming_mock(sse_lines)
    with (
        patch.object(orch_mod, "a2a_call_span", _noop_span),
        patch("orchestrator.agent.httpx.AsyncClient", mock_client_class),
    ):
        result = await call_specialist_agent(
            agent_name="inventory-fulfillment",
            message="check stock",
        )

    # 两个文本分块都进入最终组装文本。
    assert "chunk one" in result
    assert "chunk two" in result

    # 步骤帧按结构合并，不作为文本输出。
    assert len(steps_bucket) == 1
    assert steps_bucket[0]["tool_name"] == "check_stock"

    # 文本和步骤按各自通道进入队列，
    # 保留专业智能体生成顺序。
    # 不能只保存步骤却不即时转发，
    # 否则浏览器只能等流结束后
    # 一次性收到整条时间线。
    queue_items = []
    while not queue.empty():
        queue_items.append(await queue.get())

    assert [item[0] for item in queue_items] == ["delta", "frame", "delta"]
    assert [item[2] for item in queue_items if item[0] == "delta"] == ["chunk one", "chunk two"]

    frame = next(item for item in queue_items if item[0] == "frame")
    assert frame[1] == "step"
    assert frame[2]["tool_name"] == "check_stock"
    # 标记已投递，防止路由再次补发。
    assert frame[2]["_live"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 3. 按用户隔离的运行列表。
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_runs_endpoint_returns_user_runs(
    clean_db,
    monkeypatch: pytest.MonkeyPatch,
    sample_env: dict,
) -> None:
    """用户只能看到自己的运行及关联步骤。"""

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from orchestrator.routes import require_auth, router
    from shared.usage_db import log_agent_usage, log_execution_step

    # 在真实测试库插入用户和运行。
    user_id = uuid.uuid4()
    await clean_db.execute(
        """INSERT INTO users (id, email, password_hash, name, role)
           VALUES ($1, $2, 'hash', 'Test User', 'customer')""",
        user_id,
        "testuser@example.com",
    )

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    monkeypatch.setattr("shared.telemetry.get_current_trace_id", lambda: "trace-abc", raising=False)

    usage_log_id = await log_agent_usage(
        user_id=user_id,
        agent_name="orchestrator",
        input_summary="find headphones",
        tool_calls_count=2,
        duration_ms=350,
    )
    assert usage_log_id is not None

    await log_execution_step(
        usage_log_id=usage_log_id,
        step_index=0,
        tool_name="orchestrator:call_specialist_agent",
        tool_input={"agent_name": "product-discovery"},
        tool_output={"response": "Found 3 items"},
        status="success",
        duration_ms=300,
    )
    await log_execution_step(
        usage_log_id=usage_log_id,
        step_index=1,
        tool_name="product-discovery:search_products",
        tool_input={"query": "headphones"},
        tool_output={"count": 3},
        status="success",
        duration_ms=89,
    )

    # 构建替换认证依赖的最小应用。
    # 依赖必须为异步；同步依赖在线程池设置 ContextVar，
    # 不会自动传播回异步路由。
    async def _fake_auth():
        current_user_email.set("testuser@example.com")
        current_user_role.set("customer")
        return {"sub": "testuser@example.com", "role": "customer"}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = _fake_auth

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/runs", headers={"Authorization": "Bearer fake"})

    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] >= 1
    entry = next(e for e in data["entries"] if str(e["id"]) == str(usage_log_id))
    assert entry["agent_name"] == "orchestrator"
    assert entry["input_summary"] == "find headphones"
    assert entry["tool_calls_count"] == 2
    assert len(entry["steps"]) == 2

    step_names = {s["tool_name"] for s in entry["steps"]}
    assert "orchestrator:call_specialist_agent" in step_names
    assert "product-discovery:search_products" in step_names


@pytest.mark.asyncio
async def test_runs_endpoint_does_not_leak_other_users(
    clean_db,
    monkeypatch: pytest.MonkeyPatch,
    sample_env: dict,
) -> None:
    """不能读取其他用户的运行。"""

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from orchestrator.routes import require_auth, router
    from shared.usage_db import log_agent_usage

    alice_id = uuid.uuid4()
    bob_id = uuid.uuid4()
    await clean_db.executemany(
        "INSERT INTO users (id, email, password_hash, name, role) VALUES ($1, $2, 'h', $2, 'customer')",
        [(alice_id, "alice@example.com"), (bob_id, "bob@example.com")],
    )

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    monkeypatch.setattr("shared.telemetry.get_current_trace_id", lambda: None, raising=False)

    await log_agent_usage(user_id=alice_id, agent_name="orchestrator", input_summary="alice run")
    await log_agent_usage(user_id=bob_id, agent_name="orchestrator", input_summary="bob run")

    async def _auth_as_alice():
        current_user_email.set("alice@example.com")
        current_user_role.set("customer")
        return {"sub": "alice@example.com", "role": "customer"}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = _auth_as_alice

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/runs", headers={"Authorization": "Bearer fake"})

    assert resp.status_code == 200
    entries = resp.json()["entries"]
    summaries = {e["input_summary"] for e in entries}
    assert "alice run" in summaries
    assert "bob run" not in summaries, "alice must not see bob's runs"
