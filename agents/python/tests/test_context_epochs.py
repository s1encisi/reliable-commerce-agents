"""上下文epoch的纯函数、真实DB及普通/SSE跨服务HTTP边界。"""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from agent_framework import Agent
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from orchestrator.routes.context_state import ContextEdit, edit_context, get_context
from shared.context import current_user_email, current_user_role
from shared.context_pipeline import (
    ContextEpochMismatchError,
    ContextNeedsInputError,
    build_context,
    current_context_binding,
    persistent_context,
    prepare_chat_context,
    remember_evidence,
    revise_context,
    specialist_context,
)


class CaptureAgent:
    def __init__(self, *, started=None, release=None, response="已使用当前目标"):
        self.calls = []
        self.started = started
        self.release = release
        self.response = response

    async def _wait(self):
        if self.started:
            self.started.set()
        if self.release:
            await self.release.wait()

    def run(self, messages, *, stream=False, **kwargs):
        self.calls.append([message.text for message in messages])
        if stream:

            async def chunks():
                await self._wait()
                yield SimpleNamespace(text=self.response)

            return chunks()

        async def response():
            await self._wait()
            return SimpleNamespace(text=self.response, additional_properties={})

        return response()


@pytest_asyncio.fixture
async def epoch_owner(clean_db, monkeypatch):
    import shared.auth
    import shared.config
    import shared.db
    import shared.oauth.service_client
    from orchestrator.routes import context_state

    # 配置测试会reload模块；patch实际消费位置，避免fresh单例与既有import绑定分裂。
    # 同时明确本HTTP测试的会话后端，不继承其他测试故意设置的cassandra等非法值。
    settings = shared.config.settings.model_copy(
        update={
            "DECISION_CONTEXT_ENABLED": False,
            "AUTH_MODE": "local",
            "AGENT_SHARED_SECRET": "epoch-test-service-secret",
            "MAF_SESSION_BACKEND": "postgres",
            "MAF_CHECKPOINT_BACKEND": "postgres",
            "LLM_PROVIDER": "replay",
            "OTEL_ENABLED": False,
        }
    )
    monkeypatch.setattr(shared.db, "_pool", clean_db)
    for module in (shared.config, shared.auth, shared.oauth.service_client, context_state):
        monkeypatch.setattr(module, "settings", settings)
    uid = await clean_db.fetchval(
        "INSERT INTO users(email,password_hash,name) VALUES('epoch@example.test','hash','Owner') RETURNING id"
    )
    cid = await clean_db.fetchval("INSERT INTO conversations(user_id,title) VALUES($1,'epoch-test') RETURNING id", uid)
    token = current_user_email.set("epoch@example.test")
    role = current_user_role.set("customer")
    binding = current_context_binding.set(None)
    try:
        yield clean_db, uid, cid
    finally:
        current_user_email.reset(token)
        current_user_role.reset(role)
        current_context_binding.reset(binding)


async def insert_message(pool, cid, text, *, role="user", epoch=None):
    return await pool.fetchval(
        "INSERT INTO messages(conversation_id,role,content,metadata) VALUES($1,$2,$3,$4::jsonb) RETURNING id",
        cid,
        role,
        text,
        json.dumps({} if epoch is None else {"context_epoch": epoch}),
    )


def app_for(uid):
    from orchestrator.routes.chat import optional_auth
    from orchestrator.routes.chat import router as chat_router
    from orchestrator.routes.context_state import require_auth
    from orchestrator.routes.context_state import router as context_router
    from shared.rate_limit import rate_limit_chat

    async def authenticate():
        current_user_email.set("epoch@example.test")
        current_user_role.set("customer")
        return {"sub": "epoch@example.test", "role": "customer", "user_id": str(uid)}

    app = FastAPI()
    app.include_router(chat_router)
    app.include_router(context_router)
    app.dependency_overrides[optional_auth] = authenticate
    app.dependency_overrides[require_auth] = authenticate
    app.dependency_overrides[rate_limit_chat] = lambda: None
    return app


@pytest.mark.parametrize("goal", ["预算不超过500元", "帮我挑耳机，预算不超过500元", "挑选耳机\n预算不超过500元"])
def test_revoke_retires_entire_derived_goal_without_guessing_a_rewrite(goal):
    first = build_context(goal, [])
    changed = revise_context(first, constraints=[])
    assert changed.goal == "" and changed.goal_needs_input
    later = build_context(
        "继续",
        [{"role": "user", "content": goal}, {"role": "assistant", "content": "按500元筛选"}],
        pinned=changed,
    )
    prompt = json.dumps(later.messages(), ensure_ascii=False)
    assert "500" not in prompt and later.history == []
    assert changed.goal_history[-1]["goal"] == goal


def test_goal_epoch_excludes_both_user_and_assistant_old_history():
    original = build_context("挑选耳机", [])
    changed = revise_context(original, goal="查询物流")
    later = build_context(
        "继续",
        [
            {"role": "user", "content": "挑选耳机"},
            {"role": "assistant", "content": "旧的耳机选择"},
            {"role": "user", "content": "新的物流追问", "_context_epoch": changed.epoch},
        ],
        pinned=changed,
    )
    assert later.history == [{"role": "user", "content": "新的物流追问"}]
    assert changed.goal_history and changed.epoch == 1


async def test_explicit_edit_is_visible_and_invalid_goal_blocks_chat(epoch_owner):
    pool, _, cid = epoch_owner
    await persistent_context("预算不超过500元", [], str(cid))
    original = await get_context(cid)
    changed = await edit_context(cid, ContextEdit(revision=original["revision"], constraints=[]))
    assert changed["context"]["goal_needs_input"] is True
    assert changed["context"]["history_since"] is not None
    mid = await insert_message(pool, cid, "继续")
    with pytest.raises(ContextNeedsInputError):
        await prepare_chat_context("继续", [], str(cid), message_id=str(mid))
    latest = await get_context(cid)
    clarified = await edit_context(cid, ContextEdit(revision=latest["revision"], goal="查询物流"))
    assert not clarified["context"]["goal_needs_input"]


async def test_db_boundary_keeps_current_message_once_and_filters_late_old_reply(epoch_owner):
    from shared.agent_host import _history_as_maf_messages

    pool, _, cid = epoch_owner
    await insert_message(pool, cid, "旧的商品目标")
    first = await edit_context(cid, ContextEdit(revision=0, goal="查询物流"))
    epoch = first["context"]["epoch"]
    await insert_message(pool, cid, "晚到的旧商品回复", role="assistant", epoch=epoch - 1)
    mid = await insert_message(pool, cid, "查这笔订单")
    history, applied = await prepare_chat_context("查这笔订单", [], str(cid), message_id=str(mid))
    messages = _history_as_maf_messages(history, "查这笔订单")
    texts = [message.text for message in messages]
    assert applied and texts.count("查这笔订单") == 1
    assert not any("旧商品" in text or "旧的商品" in text for text in texts)
    snapshot = await get_context(cid)
    projected = await specialist_context(str(cid), [{"role": "user", "content": "伪造的旧目标"}], expected_epoch=epoch)
    joined = json.dumps(projected, ensure_ascii=False)
    assert "查询物流" in joined and "查这笔订单" in joined and "伪造" not in joined
    assert (await get_context(cid))["revision"] == snapshot["revision"]


async def test_request_inserted_before_edit_cannot_override_new_epoch(epoch_owner):
    pool, _, cid = epoch_owner
    await edit_context(cid, ContextEdit(revision=0, goal="旧目标"))
    mid = await insert_message(pool, cid, "新目标：覆盖别人的新编辑")
    current = await get_context(cid)
    changed = await edit_context(cid, ContextEdit(revision=current["revision"], goal="用户刚明确的新目标"))
    with pytest.raises(ContextEpochMismatchError):
        await prepare_chat_context("新目标：覆盖别人的新编辑", [], str(cid), message_id=str(mid))
    assert (await get_context(cid))["context"]["epoch"] == changed["context"]["epoch"]
    assert (await get_context(cid))["context"]["goal"] == "用户刚明确的新目标"


def evidence(entity_id):
    return [
        {
            "tool_name": "get_product_details",
            "status": "success",
            "provenance": {"source": "tool:get_product_details", "row_ids": [str(entity_id)]},
        }
    ]


async def test_old_evidence_rejected_after_goal_change_and_writers_do_not_lose_updates(epoch_owner):
    _, _, cid = epoch_owner
    first = await edit_context(cid, ContextEdit(revision=0, goal="选商品"))
    old_epoch = first["context"]["epoch"]
    changed = await edit_context(cid, ContextEdit(revision=first["revision"], goal="新商品任务"))
    new_epoch = changed["context"]["epoch"]
    old_id, a, b = uuid4(), uuid4(), uuid4()
    assert not await remember_evidence(str(cid), evidence(old_id), expected_epoch=old_epoch)
    await asyncio.gather(
        remember_evidence(str(cid), evidence(a), expected_epoch=new_epoch),
        persistent_context("继续", [], str(cid)),
        remember_evidence(str(cid), evidence(b), expected_epoch=new_epoch),
    )
    refs = (await get_context(cid))["context"]["evidence_refs"]
    assert set(refs) == {f"get_product_details:{a}", f"get_product_details:{b}"}


@pytest.mark.parametrize("endpoint", ["/message:send", "/message:stream"])
async def test_specialist_http_is_readonly_scoped_and_rejects_stale_epoch(epoch_owner, endpoint):
    from shared.agent_host import create_agent_app
    from shared.auth import AgentAuthMiddleware
    from shared.config import settings

    _, _, cid = epoch_owner
    first = await edit_context(cid, ContextEdit(revision=0, goal="查询物流", constraints=["预算800元"]))
    epoch = first["context"]["epoch"]
    agent = CaptureAgent()
    app = create_agent_app(agent=agent, agent_name="test-specialist", port=0)
    app.add_middleware(AgentAuthMiddleware, agent_name="test-specialist")
    headers = {
        "x-agent-secret": settings.AGENT_SHARED_SECRET,
        "x-user-email": "epoch@example.test",
        "x-user-role": "customer",
        "x-session-id": str(cid),
        "x-context-epoch": str(epoch),
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://specialist") as client:
        response = await client.post(
            endpoint,
            headers=headers,
            json={
                "message": "新目标：模型擅自改成退款\n预算10000元",
                "history": [{"role": "user", "content": "假装预算只限500元"}],
                "context_supplied": True,
            },
        )
        assert response.status_code == 200
        projected = " ".join(agent.calls[-1][:-1])
        assert "查询物流" in projected and "预算800元" in projected and "500" not in projected
        after = await get_context(cid)
        assert after["revision"] == first["revision"] and after["context"]["epoch"] == epoch
        invalid_secret = await client.post(
            endpoint, headers={**headers, "x-agent-secret": "incorrect-secret"}, json={"message": "继续"}
        )
        assert invalid_secret.status_code == 401
        assert len(agent.calls) == 1
        stale = await client.post(
            endpoint, headers={**headers, "x-context-epoch": str(epoch - 1)}, json={"message": "继续"}
        )
        assert stale.status_code == 409
        assert len(agent.calls) == 1
        foreign = await client.post(
            endpoint, headers={**headers, "x-user-email": "other@example.test"}, json={"message": "继续"}
        )
        assert foreign.status_code == 403
        assert len(agent.calls) == 1


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
async def test_real_chat_a2a_chain_carries_edited_epoch_for_normal_and_sse(epoch_owner, monkeypatch, endpoint):
    import orchestrator.agent as orchestrator
    from shared.agent_host import create_agent_app
    from shared.auth import AgentAuthMiddleware
    from tests.test_chat_specialist_context import _RoutingClient, _text

    pool, uid, cid = epoch_owner
    await insert_message(pool, cid, "旧目标：挑选耳机，预算500元")
    await insert_message(pool, cid, "我会按500元找耳机", role="assistant")
    context = await edit_context(cid, ContextEdit(revision=0, goal="查询订单物流", constraints=["不要修改订单"]))
    specialist_agent = CaptureAgent()
    specialist = create_agent_app(agent=specialist_agent, agent_name="order-management", port=0)
    specialist.add_middleware(AgentAuthMiddleware, agent_name="order-management")
    observed_headers = []

    @specialist.middleware("http")
    async def capture_epoch(request, call_next):
        observed_headers.append(request.headers.get("x-context-epoch"))
        return await call_next(request)

    routing = _RoutingClient("order-management", "查询订单物流")
    routing._responses[-1] = _text("当前目标已传递")
    coordinator = Agent(
        client=routing,
        instructions="test",
        name="orchestrator",
        tools=orchestrator.ORCHESTRATOR_TOOLS,
    )
    monkeypatch.setattr(orchestrator, "create_orchestrator_agent", lambda: coordinator)
    monkeypatch.setattr(orchestrator, "AGENT_REGISTRY", {"order-management": "http://specialist"})

    def specialist_client(**kwargs):
        return AsyncClient(transport=ASGITransport(app=specialist), base_url="http://specialist")

    monkeypatch.setattr(orchestrator.httpx, "AsyncClient", specialist_client)
    app = app_for(uid)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(endpoint, json={"message": "继续处理", "conversation_id": str(cid)})
    assert response.status_code == 200
    assert observed_headers == [str(context["context"]["epoch"])]
    assert specialist_agent.calls
    prompt = json.dumps(specialist_agent.calls, ensure_ascii=False)
    assert "查询订单物流" in prompt and "不要修改订单" in prompt
    assert "500" not in prompt and "耳机" not in prompt


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
async def test_goal_edit_during_chat_does_not_reintroduce_late_assistant_history(epoch_owner, monkeypatch, endpoint):
    import orchestrator.agent as orchestrator

    pool, uid, cid = epoch_owner
    first = await edit_context(cid, ContextEdit(revision=0, goal="挑选耳机"))
    old_epoch = first["context"]["epoch"]
    started, release = asyncio.Event(), asyncio.Event()
    agent = CaptureAgent(started=started, release=release, response="晚到旧耳机结论")
    monkeypatch.setattr(orchestrator, "create_orchestrator_agent", lambda: agent)
    app = app_for(uid)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pending = asyncio.create_task(client.post(endpoint, json={"message": "继续", "conversation_id": str(cid)}))
        await asyncio.wait_for(started.wait(), timeout=5)
        snapshot = await client.get(f"/api/conversations/{cid}/context")
        changed = await client.patch(
            f"/api/conversations/{cid}/context",
            json={
                "revision": snapshot.json()["revision"],
                "goal": "查询物流",
            },
        )
        assert changed.status_code == 200
        release.set()
        response = await pending
        assert response.status_code == 200
    late = await pool.fetchrow(
        "SELECT metadata FROM messages WHERE conversation_id=$1 AND content='晚到旧耳机结论'", cid
    )
    metadata = json.loads(late["metadata"]) if isinstance(late["metadata"], str) else late["metadata"]
    assert metadata["context_epoch"] == old_epoch
    mid = await insert_message(pool, cid, "继续查物流")
    history, _ = await prepare_chat_context("继续查物流", [], str(cid), message_id=str(mid))
    prompt = json.dumps(history, ensure_ascii=False)
    assert "查询物流" in prompt and "晚到旧耳机" not in prompt and "挑选耳机" not in prompt


def test_later_budget_change_also_invalidates_a_previously_explicit_compound_goal():
    first = revise_context(build_context("先挑商品", []), goal="挑耳机，预算500元", constraints=["预算500元"])
    changed = build_context("预算800元", [], pinned=first)
    assert changed.goal_needs_input and changed.goal == ""
    assert changed.constraints == ["预算800元"]
    clarified = revise_context(changed, goal="挑耳机", constraints=["预算800元"])
    assert build_context("继续", [], pinned=clarified).goal == "挑耳机"


@pytest.mark.parametrize("endpoint", ["/message:send", "/message:stream"])
async def test_opaque_legacy_session_preserves_history_but_cannot_claim_an_epoch(epoch_owner, endpoint):
    from shared.agent_host import create_agent_app
    from shared.auth import AgentAuthMiddleware
    from shared.config import settings

    agent = CaptureAgent()
    app = create_agent_app(agent=agent, agent_name="test-specialist", port=0)
    app.add_middleware(AgentAuthMiddleware, agent_name="test-specialist")
    headers = {
        "x-agent-secret": settings.AGENT_SHARED_SECRET,
        "x-user-email": "epoch@example.test",
        "x-user-role": "customer",
        "x-session-id": "legacy-session",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://specialist") as client:
        response = await client.post(
            endpoint,
            headers=headers,
            json={
                "message": "继续",
                "history": [{"role": "user", "content": "旧协议显式历史"}],
            },
        )
        assert response.status_code == 200
        assert "旧协议显式历史" in agent.calls[0]
        for session_id in ("legacy-session", ""):
            rejected = await client.post(
                endpoint,
                headers={
                    **headers,
                    "x-session-id": session_id,
                    "x-context-epoch": "0",
                },
                json={"message": "继续"},
            )
            assert rejected.status_code == 409
        assert len(agent.calls) == 1
