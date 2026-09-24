"""
第 04 章 —— 会话与记忆：测试。

- 单元测试让 AgentSession 走一遍 dict 往返（不涉及 LLM）。
- 集成测试针对真实的 Azure OpenAI，证明持久化能跨全新的智能体实例生效。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from agent_framework import AgentSession  # noqa: E402
from main import FIXTURES_DIR, ask_and_save, build_agent  # noqa: E402

# ─────────── 单元测试（不涉及 LLM） ───────────


def test_session_roundtrip_preserves_session_id() -> None:
    original = AgentSession(session_id="sess-42")
    original.state["foo"] = "bar"
    as_dict = original.to_dict()

    # 必须能走一遍 JSON 往返（也就是磁盘上的存储格式）。
    rehydrated = AgentSession.from_dict(json.loads(json.dumps(as_dict)))
    assert rehydrated.session_id == "sess-42"


def test_session_to_dict_is_json_serialisable() -> None:
    session = AgentSession()
    session.state["hello"] = "world"
    json.dumps(session.to_dict())  # 不可序列化时会抛错


def test_session_state_is_roundtrip_safe() -> None:
    original = AgentSession()
    original.state["a"] = 1
    original.state["b"] = [1, 2, 3]
    original.state["c"] = {"nested": True}

    rehydrated = AgentSession.from_dict(json.loads(json.dumps(original.to_dict())))
    assert rehydrated.state["a"] == 1
    assert rehydrated.state["b"] == [1, 2, 3]
    assert rehydrated.state["c"] == {"nested": True}


def test_fresh_session_has_new_id() -> None:
    a = AgentSession()
    b = AgentSession()
    assert a.session_id != b.session_id


# ─────────── 回放测试（无需凭据，可在 CI 中运行） ───────────


@pytest.mark.asyncio
async def test_replay_session_persists_across_fresh_agent_instances(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """回放 tests/fixtures/replay/ —— 不走网络、不需凭据。

    曾针对真实 LLM 录制过一次（即下方的
    test_session_persists_across_fresh_agent_instances，以 RECORD=true 运行），
    随后提交入库。因为是两轮，会涉及两份夹具 —— 每轮的消息历史各一份。
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"{FIXTURES_DIR} 中没有已录制的夹具 —— 请先以 RECORD=true 运行")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    session_file = tmp_path / "session.json"

    agent1 = build_agent()
    await ask_and_save(agent1, "Remember: I want to buy SKU-4471.", session_file)
    assert session_file.exists()

    agent2 = build_agent()
    answer = await ask_and_save(
        agent2,
        "What did I say I wanted to buy? Answer with the SKU only.",
        session_file,
    )
    assert "SKU-4471" in answer.upper(), f"期望追问的回答中出现 'SKU-4471'，实际为：{answer!r}"


# ─────────── 集成测试：真实 LLM 的持久化 ───────────


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
async def test_session_persists_across_fresh_agent_instances(tmp_path: pathlib.Path) -> None:
    """
    在第 1 轮用一个 Agent 实例存下一个事实，丢弃该智能体，构建一个全新的，
    加载会话，然后追问。
    """
    session_file = tmp_path / "session.json"

    agent1 = build_agent()
    await ask_and_save(agent1, "Remember: I want to buy SKU-4471.", session_file)
    assert session_file.exists()

    # 构建一个全新的智能体（另一个 Agent 实例，也就是第二次从命令行
    # 调用 main.py 时会做的事）。
    agent2 = build_agent()
    answer = await ask_and_save(
        agent2,
        "What did I say I wanted to buy? Answer with the SKU only.",
        session_file,
    )
    assert "SKU-4471" in answer.upper(), f"期望追问的回答中出现 'SKU-4471'，实际为：{answer!r}"
