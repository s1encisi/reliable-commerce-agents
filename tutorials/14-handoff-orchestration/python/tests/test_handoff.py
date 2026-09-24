"""第 14 章处理权交接测试。

离线回放复用已录制路由，真实模型用例单独控制。构建器内部集合
顺序受 PYTHONHASHSEED 影响，夹具录制时使用 0，回放和 CI 也必须
保持相同值，否则后续指令文本及哈希可能改变。
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
from main import FIXTURES_DIR, ask, build_workflow  # noqa: E402


def _llm_available() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        return bool(
            os.environ.get("AZURE_OPENAI_ENDPOINT")
            and (os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))
        )
    key = os.environ.get("OPENAI_API_KEY", "")
    return bool(key) and not key.startswith("sk-your-")


def test_workflow_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    # 只构建、不调用模型，因此不需要真实凭据。
    # 默认客户端会直接读取 OPENAI_API_KEY，
    # 无凭据 CI 仍需要提供非空占位值，
    # 但客户端不会被执行，
    # 不会产生真实请求。
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder-not-used")
    assert build_workflow() is not None


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("PYTHONHASHSEED") != "0",
    reason="handoff replay fixtures were recorded with PYTHONHASHSEED=0 (see module docstring) "
    "— run with PYTHONHASHSEED=0 set, or this test flakes on hash-randomization-sensitive turns",
)
async def test_replay_routes_math_to_math_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """读取已提交夹具，无网络或凭据。

    保持 PYTHONHASHSEED 与录制一致，即可重现固定交接路径。
    真实交接轮数可能变化，但已录制轨迹的结果应稳定。
    """
    recording = os.environ.get("RECORD", "").lower() in ("1", "true", "yes")
    if not recording and not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"no recorded fixtures in {FIXTURES_DIR} — run with RECORD=true first")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    participants, answer = await ask("What is 37 * 42?")
    assert "math" in participants, f"math question should reach math agent, got: {participants}"
    assert "1554" in answer.replace(",", "") or "1,554" in answer


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_routes_math_to_math_agent() -> None:
    participants, answer = await ask("What is 37 * 42?")
    assert "math" in participants, f"math question should reach math agent, got: {participants}"
    assert "1554" in answer.replace(",", "") or "1,554" in answer


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_routes_history_to_history_agent() -> None:
    participants, answer = await ask("When did World War 2 end? Answer with the year only.")
    assert "history" in participants, f"history question should reach history agent, got: {participants}"
    assert "1945" in answer


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_routing_diverges_between_domains() -> None:
    math_routing, _ = await ask("What is 100 / 4?")
    history_routing, _ = await ask("Who was the first president of the United States?")
    # 不同领域应交给不同专业智能体。
    assert set(math_routing) != set(history_routing)
