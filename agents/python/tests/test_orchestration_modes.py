"""编排模式测试，使用真实 Agent、工具调用层与 HandoffBuilder。

客户端返回预设模型响应；专业智能体请求经过真实 httpx，
仅网络目标由测试传输替代。
"""

from __future__ import annotations

import uuid
from typing import Any

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

from orchestrator.modes import DEFAULT_MODE, MODES, UnknownModeError, get_mode, list_modes
from orchestrator.modes.base import RunContext
from orchestrator.modes.handoff_mode import HandoffMode
from orchestrator.modes.tool_router import ToolRouterMode
from shared.agent_observability import STEP_MIDDLEWARE
from tests.test_handoff_orchestration import _StubTransport, stub_transport  # noqa: F401


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        messages=[Message(role="assistant", contents=[Content.from_text(text=text)])],
        response_id=str(uuid.uuid4()),
        finish_reason="stop",
    )


class _ScriptedClient(FunctionInvocationLayer, BaseChatClient):
    """按顺序返回预设响应，驱动真实函数与交接工具调用。

    组合 FunctionInvocationLayer；流式路径通过 _build_response_stream
    安装最终响应终结器，满足 MAF 内部对 ChatResponse 的要求。"""

    def __init__(self, *responses: ChatResponse) -> None:
        super().__init__()
        self._responses = list(responses)
        self.calls: list[Any] = []

    async def _next(self) -> ChatResponse:
        if not self._responses:
            raise AssertionError("_ScriptedClient ran out of responses")
        return self._responses.pop(0)

    def _inner_get_response(self, *, messages, stream: bool, options: Any = None, **_: Any):
        self.calls.append(messages)

        if stream:

            async def _gen():
                response = await self._next()
                for msg in response.messages:
                    yield ChatResponseUpdate(role=msg.role, contents=msg.contents, author_name=msg.author_name)

            return self._build_response_stream(_gen())

        return self._next()


# ─────────────────────── registry ───────────────────────


def test_default_mode_is_tool() -> None:
    assert DEFAULT_MODE == "tool"
    assert isinstance(get_mode(None), ToolRouterMode)
    assert isinstance(get_mode(""), ToolRouterMode)


def test_get_mode_resolves_registered_names() -> None:
    from orchestrator.modes.group_chat_mode import GroupChatMode
    from orchestrator.modes.workflow_mode import PrePurchaseMode, ReturnReplaceMode

    assert isinstance(get_mode("tool"), ToolRouterMode)
    assert isinstance(get_mode("handoff"), HandoffMode)
    assert isinstance(get_mode("workflow:pre-purchase"), PrePurchaseMode)
    assert isinstance(get_mode("workflow:return-replace"), ReturnReplaceMode)
    assert isinstance(get_mode("group-chat"), GroupChatMode)


def test_get_mode_raises_named_error_for_unknown_mode() -> None:
    with pytest.raises(UnknownModeError, match="workflow:not-a-real-mode"):
        get_mode("workflow:not-a-real-mode")


def test_list_modes_reports_capabilities_and_default_flag() -> None:
    modes = list_modes()
    names = {m["name"] for m in modes}
    assert names == set(MODES)
    tool_entry = next(m for m in modes if m["name"] == "tool")
    assert tool_entry["default"] is True
    assert tool_entry["capabilities"]["is_graph"] is False
    handoff_entry = next(m for m in modes if m["name"] == "handoff")
    assert handoff_entry["default"] is False
    assert handoff_entry["capabilities"]["is_graph"] is True


# ─────────────────────── ToolRouterMode ───────────────────────


@pytest.mark.asyncio
async def test_tool_router_yields_run_completed_with_text(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _ScriptedClient(_text_response("Paris is the capital of France."))
    fake_agent = Agent(client=fake, instructions="test", name="orchestrator")
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: fake_agent)

    mode = ToolRouterMode()
    events = [e async for e in mode.run("capital of France?", RunContext(history=[]))]

    assert events, "must yield at least the run_completed event"
    final = events[-1]
    assert final.kind == "run_completed"
    assert final.payload["text"] == "Paris is the capital of France."
    assert final.payload["agents_involved"] == ["orchestrator"]


@pytest.mark.asyncio
async def test_tool_router_yields_tool_call_events_for_captured_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    """先调用真实工具再返回答案，验证步骤记录确实覆盖工具执行。"""
    from agent_framework import tool

    @tool(name="get_weather", description="Get weather for a city")
    async def get_weather(city: str) -> str:
        return f"sunny in {city}"

    call_response = ChatResponse(
        messages=[
            Message(
                role="assistant",
                contents=[Content.from_function_call(call_id="c1", name="get_weather", arguments={"city": "Paris"})],
            )
        ],
        response_id=str(uuid.uuid4()),
        finish_reason="tool_calls",
    )
    fake = _ScriptedClient(call_response, _text_response("It's sunny in Paris."))
    fake_agent = Agent(
        client=fake,
        instructions="test",
        name="orchestrator",
        tools=[get_weather],
        middleware=STEP_MIDDLEWARE,
    )
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: fake_agent)

    mode = ToolRouterMode()
    events = [e async for e in mode.run("weather in Paris?", RunContext(history=[]))]

    tool_call_events = [e for e in events if e.kind == "tool_call"]
    assert len(tool_call_events) == 1
    assert tool_call_events[0].node_id == "get_weather"
    assert tool_call_events[0].agent == "orchestrator"
    assert tool_call_events[0].payload["status"] == "success"

    assert events[-1].kind == "run_completed"
    assert events[-1].payload["text"] == "It's sunny in Paris."


@pytest.mark.asyncio
async def test_tool_router_forwards_history_into_the_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """历史必须进入模型消息，不能只设置无人读取的 ContextVar。

    直接断言模型输入，避免无效状态变量让测试通过却未证明上下文传播。
    """
    fake = _ScriptedClient(_text_response("ok"))
    fake_agent = Agent(client=fake, instructions="test", name="orchestrator")
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: fake_agent)

    history = [{"role": "user", "content": "earlier message"}]
    mode = ToolRouterMode()
    async for _ in mode.run("follow-up", RunContext(history=history)):
        pass

    assert fake.calls, "the model was never called"
    texts = [m.text for m in fake.calls[-1] if m.text]
    assert "earlier message" in texts
    assert "follow-up" in texts


def test_tool_router_graph_mermaid_is_none() -> None:
    assert ToolRouterMode().graph_mermaid() is None


# ─────────────────────── HandoffMode ───────────────────────


@pytest.mark.asyncio
async def test_handoff_mode_routes_to_specialist_and_reports_its_answer(
    monkeypatch: pytest.MonkeyPatch,
    stub_transport: _StubTransport,  # noqa: F811 — pytest fixture param, not a redefinition
) -> None:
    """首轮发出真实交接工具调用，专业智能体经 A2A 测试传输执行，
    随后预设回复通过交接网格返回。
    """
    stub_transport.reply = "1554"

    handoff_call = ChatResponse(
        messages=[
            Message(
                role="assistant",
                contents=[Content.from_function_call(call_id="c1", name="handoff_to_math", arguments={})],
            )
        ],
        response_id=str(uuid.uuid4()),
        finish_reason="tool_calls",
    )
    orchestrator_client = _ScriptedClient(handoff_call)
    fake_orchestrator = Agent(
        client=orchestrator_client,
        instructions="test",
        name="orchestrator",
        require_per_service_call_history_persistence=True,
    )

    monkeypatch.setattr(
        "orchestrator.handoff._load_registry",
        lambda: {"math": "http://math-specialist:9999/a2a"},
    )
    monkeypatch.setattr("orchestrator.handoff.create_handoff_triage_agent", lambda: fake_orchestrator)

    mode = HandoffMode()
    events = [e async for e in mode.run("What is 37 * 42?", RunContext(history=[]))]

    handoff_events = [e for e in events if e.kind == "handoff"]
    assert handoff_events, "expected at least one adapted handoff event"

    final = events[-1]
    assert final.kind == "run_completed"
    assert "math" in final.payload["agents_involved"]
    assert "1554" in final.payload["text"]

    # A2A 请求确实经过 HTTP 客户端到达测试传输。
    assert len(stub_transport.calls) >= 1


def test_handoff_mode_graph_mermaid_is_none() -> None:
    assert HandoffMode().graph_mermaid() is None


def test_handoff_mode_capabilities_marks_is_graph() -> None:
    caps = HandoffMode().capabilities
    assert caps.is_graph is True
    assert caps.supports_hitl is False


# ─────────────── Handoff triage agent contract ───────────────
#
# 防止交接模式再次生成长篇独白却未访问专业智能体。
# 历史故障中输出达 23637 字符、耗时 100–200 秒。
# 根因不是增量累加，
# 而是错误复用带业务工具的编排器作为起始智能体，
# 提示词仍要求使用 call_specialist_agent。
#
# 交接网格依靠交接工具推进，
# 若起始智能体自行回答而不交接，
# 流程只能回到用户或继续自身，
# 自主模式下就可能反复续写。


def test_handoff_triage_agent_has_no_tools(monkeypatch) -> None:
    """分诊智能体不能携带让它绕过交接的业务工具。

    测试只检查工具表面，使用客户端替身，无需模型凭据。
    """
    import orchestrator.handoff as handoff_module

    captured: dict[str, object] = {}

    class _FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(handoff_module, "create_chat_client", lambda: object())
    monkeypatch.setattr(handoff_module, "Agent", _FakeAgent)

    handoff_module.create_handoff_triage_agent()

    assert "tools" not in captured or not captured["tools"], (
        "the handoff triage agent must carry no tools of its own — it routes by "
        "calling MAF's synthesised handoff tools, and anything else it can call "
        "is an escape hatch from doing that"
    )
    assert "context_providers" not in captured or not captured["context_providers"], (
        "context providers exist to help an agent answer; this one only chooses"
    )


def test_handoff_triage_prompt_does_not_reuse_the_tool_router_prompt() -> None:
    """交接提示词不能要求使用仅属于 tool 模式的 call_specialist_agent。

    直接检查组合提示词，避免引用不存在的工具。
    """
    from shared.prompt_loader import load_prompt

    triage = load_prompt("handoff-triage")
    orchestrator = load_prompt("orchestrator")

    assert triage, "handoff-triage.yaml must exist and compose to a non-empty prompt"
    assert triage != orchestrator, "the two modes route by opposite mechanisms"
    assert "call_specialist_agent" not in triage
    assert "hand off" in triage.lower() or "handoff" in triage.lower()


def test_handoff_autonomous_mode_is_bounded() -> None:
    """自主轮次上限是防止长篇自我续写的安全限制，不能沿用过高默认值。"""
    from shared.config import settings

    assert settings.HANDOFF_MAX_TURNS <= 5, (
        "a high autonomous turn limit turns 'the agent cannot hand off' from a "
        "short wrong answer into a very expensive one"
    )
