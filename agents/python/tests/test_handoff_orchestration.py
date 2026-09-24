"""编排器处理权交接工作流测试。

覆盖远程客户端适配器、A2A 请求及 HandoffBuilder 接线；
只替换 HTTP 传输，不访问真实网络和模型。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from agent_framework import Agent, ChatResponse, Message

from orchestrator.handoff import (
    _load_registry,
    build_orchestrator_handoff_workflow,
    build_remote_specialist_agents,
)
from shared.remote_agent import RemoteSpecialistChatClient, make_remote_specialist_agent


def _stub_orchestrator() -> Agent:
    """构造名称为 orchestrator 的真实 Agent，但不调用模型。

    使用远程客户端适配器绕开工厂凭据校验，组装工作流本身不执行请求。
    """
    return Agent(
        client=RemoteSpecialistChatClient(name="orchestrator", url="http://local-stub"),
        name="orchestrator",
        description="Stub orchestrator used only for handoff wiring tests.",
        instructions="You are a test stub.",
        # 构建器要求每个参与者设置此标志，
        # 与编排器工厂中的设置一致。
        require_per_service_call_history_persistence=True,
    )


# ─────────────────────── Helpers ───────────────────────


class _StubTransport(httpx.AsyncBaseTransport):
    """捕获 A2A 请求头与载荷供断言。"""

    def __init__(self, reply: str = "ok from stub", status: int = 200) -> None:
        self.reply = reply
        self.status = status
        self.calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        body = json.dumps({"response": self.reply}).encode()
        return httpx.Response(self.status, content=body, request=request)


@pytest.fixture
def stub_transport(monkeypatch: pytest.MonkeyPatch) -> _StubTransport:
    """替换 AsyncClient，让请求全部落到测试传输。"""
    transport = _StubTransport()
    orig = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return orig(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return transport


# ─────────────────────── RemoteSpecialistChatClient ─────────────


@pytest.mark.asyncio
async def test_remote_client_posts_to_a2a_endpoint_and_returns_reply(stub_transport: _StubTransport) -> None:
    stub_transport.reply = "stub says hello"
    client = RemoteSpecialistChatClient(name="order-management", url="http://order:8082/a2a")

    response = await client.get_response([Message(role="user", contents=["hi"])])

    assert isinstance(response, ChatResponse)
    assert response.messages[0].text == "stub says hello"
    assert response.messages[0].author_name == "order-management"

    assert len(stub_transport.calls) == 1
    call = stub_transport.calls[0]
    assert call.url.path.endswith("/message:send")
    body = json.loads(call.content)
    assert body["message"] == "hi"


@pytest.mark.asyncio
async def test_remote_client_flattens_multi_message_prompt(stub_transport: _StubTransport) -> None:
    client = RemoteSpecialistChatClient(name="pricing-promotions", url="http://p:8083/a2a")
    await client.get_response(
        [
            Message(role="user", contents=["first turn"]),
            Message(role="assistant", contents=["intermediate"]),
            Message(role="user", contents=["follow up"]),
        ]
    )
    body = json.loads(stub_transport.calls[0].content)
    assert "first turn" in body["message"]
    assert "follow up" in body["message"]


@pytest.mark.asyncio
async def test_remote_client_streaming_yields_single_chunk(stub_transport: _StubTransport) -> None:
    stub_transport.reply = "streamed reply"
    client = RemoteSpecialistChatClient(name="review-sentiment", url="http://r:8084/a2a")

    pieces = []
    async for update in client.get_response([Message(role="user", contents=["hi"])], stream=True):
        pieces.append(update.text)
    assert "".join(pieces) == "streamed reply"


@pytest.mark.asyncio
async def test_remote_client_surfaces_http_error(stub_transport: _StubTransport) -> None:
    stub_transport.status = 500
    stub_transport.reply = "boom"
    client = RemoteSpecialistChatClient(name="inventory-fulfillment", url="http://i:8085/a2a")

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_response([Message(role="user", contents=["hi"])])


# ─────────────────────── Handoff workflow wiring ───────────────


CANONICAL_REGISTRY: dict[str, str] = {
    "product-discovery": "http://pd:8081/a2a",
    "order-management": "http://om:8082/a2a",
    "pricing-promotions": "http://pp:8083/a2a",
    "review-sentiment": "http://rs:8084/a2a",
    "inventory-fulfillment": "http://if:8085/a2a",
}


@pytest.fixture
def registry_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """固定智能体注册表，使加载结果可确定。"""
    from shared import config as config_mod

    monkeypatch.setattr(config_mod.settings, "AGENT_REGISTRY", json.dumps(CANONICAL_REGISTRY))
    # 同时替换交接模块导入时捕获的别名，
    # 避免旧绑定绕过测试设置。
    import orchestrator.handoff as handoff_mod

    monkeypatch.setattr(handoff_mod.settings, "AGENT_REGISTRY", json.dumps(CANONICAL_REGISTRY))
    return CANONICAL_REGISTRY


def test_load_registry_parses_agent_registry(registry_env: dict[str, str]) -> None:
    assert _load_registry() == registry_env


def test_build_remote_specialist_agents_covers_registry(registry_env: dict[str, str]) -> None:
    agents = build_remote_specialist_agents(registry=registry_env)
    assert {a.name for a in agents} == set(registry_env.keys())
    for agent in agents:
        assert isinstance(agent, Agent)


def test_handoff_workflow_wires_orchestrator_plus_specialists(registry_env: dict[str, str]) -> None:
    specialists = build_remote_specialist_agents(registry=registry_env)
    workflow = build_orchestrator_handoff_workflow(
        orchestrator=_stub_orchestrator(),
        specialists=specialists,
        autonomous_mode=True,
    )
    ids = {getattr(e, "id", None) for e in workflow.get_executors_list()}
    assert "orchestrator" in ids
    assert set(registry_env.keys()) <= ids


def test_handoff_workflow_respects_autonomous_mode_flag(registry_env: dict[str, str]) -> None:
    """关闭自主模式仍应成功构建。"""
    specialists = build_remote_specialist_agents(registry=registry_env)
    workflow = build_orchestrator_handoff_workflow(
        orchestrator=_stub_orchestrator(),
        specialists=specialists,
        autonomous_mode=False,
    )
    assert workflow is not None


def test_handoff_workflow_with_empty_registry() -> None:
    """未配置远程专业智能体时，编排器也可单独构建。"""
    workflow = build_orchestrator_handoff_workflow(
        orchestrator=_stub_orchestrator(),
        specialists=[],
    )
    ids = {getattr(e, "id", None) for e in workflow.get_executors_list()}
    assert "orchestrator" in ids


# ─────────────────────── make_remote_specialist_agent smoke ────


def test_make_remote_specialist_agent_returns_configured_agent() -> None:
    agent = make_remote_specialist_agent("order-management", "http://order:8082/a2a")
    assert isinstance(agent, Agent)
    assert agent.name == "order-management"
