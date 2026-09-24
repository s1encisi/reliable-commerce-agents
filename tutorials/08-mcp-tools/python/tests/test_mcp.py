"""
第 08 章 —— MCP 工具：测试。

- 单元测试：直接调用天气 MCP 服务器，验证它返回预置数据。
- 集成测试：端到端的智能体运行会通过 MCP 调用该工具，并把预置的天气预报
  写进最终回答。
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
from main import FIXTURES_DIR, build_mcp_tool, run  # noqa: E402
from weather_mcp_server import get_weather  # noqa: E402

# ─────────────────── 回放测试（无需凭据，可在 CI 中运行） ────


@pytest.mark.asyncio
async def test_replay_calls_mcp_weather_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """回放 tests/fixtures/replay/ —— 不发起网络 LLM 调用、不需凭据。

    MCP 服务器子进程仍然真实运行（它是本地的、不花钱）；只有 LLM 调用被回放。
    曾针对真实 LLM 录制过一次（以 RECORD=true 运行），随后提交入库。
    对应 test_real_llm_calls_mcp_weather_tool。
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"{FIXTURES_DIR} 中没有已录制的夹具 —— 请先以 RECORD=true 运行")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    answer = await run("What's the weather in Paris?")
    lowered = answer.lower()
    assert "sunny" in lowered or "18" in lowered, f"期望答案中出现 MCP 工具的数据，实际为：{answer!r}"


def test_weather_tool_returns_canned_data() -> None:
    # FastMCP 包装了该函数；通过 .fn 拿到原函数
    fn = getattr(get_weather, "fn", get_weather)
    assert "Sunny" in fn("Paris")
    assert "No weather data" in fn("Atlantis")


def test_weather_tool_is_case_insensitive() -> None:
    fn = getattr(get_weather, "fn", get_weather)
    assert fn("paris") == fn("PARIS")


def test_build_mcp_tool_configures_subprocess() -> None:
    tool = build_mcp_tool()
    assert tool.name == "weather-mcp"


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
async def test_real_llm_calls_mcp_weather_tool() -> None:
    answer = await run("What's the weather in Paris?")
    lowered = answer.lower()
    assert "sunny" in lowered or "18" in lowered, f"期望答案中出现 MCP 工具的数据，实际为：{answer!r}"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_real_llm_skips_mcp_tool_for_unrelated_question() -> None:
    answer = await run("What is the capital of France? Answer with only the city.")
    assert "paris" in answer.lower()
    # 预置的天气字符串不应出现在与天气无关的回答里。
    assert "sunny, 18" not in answer.lower()
