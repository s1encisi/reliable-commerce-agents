"""
第 13 章 —— 并发编排：测试。

仅集成测试 —— 并发执行会发起三次真实 LLM 调用。我们还会断言墙钟行为，
以确认它们确实是并行而非串行执行的。
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
from main import FIXTURES_DIR, analyze, build_workflow  # noqa: E402


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
    # 仅构建 —— 从不调用 LLM，因此不应该需要真实凭据。_default_client() 的
    # OpenAI 分支通过硬性 os.environ[...] 读取 OPENAI_API_KEY，无凭据的 CI
    # 任务里曾在这里被绊住。既然客户端从未被真正调用，放一个占位符就够了。
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder-not-used")
    assert build_workflow() is not None


@pytest.mark.asyncio
async def test_replay_all_three_agents_respond(monkeypatch: pytest.MonkeyPatch) -> None:
    """回放 tests/fixtures/replay/ —— 无网络、无凭据。

    曾对真实 LLM 录制一次（即下方的 test_real_llm_all_three_agents_respond，
    带 RECORD=true 运行）并提交入库。
    """
    recording = os.environ.get("RECORD", "").lower() in ("1", "true", "yes")
    if not recording and not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"no recorded fixtures in {FIXTURES_DIR} — run with RECORD=true first")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    per_agent, _ = await analyze("ultrasonic pet collar")
    assert "researcher" in per_agent
    assert "marketer" in per_agent
    assert "legal" in per_agent
    assert all(per_agent[name] for name in ("researcher", "marketer", "legal"))


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_all_three_agents_respond() -> None:
    per_agent, _ = await analyze("ultrasonic pet collar")
    assert "researcher" in per_agent
    assert "marketer" in per_agent
    assert "legal" in per_agent
    assert all(per_agent[name] for name in ("researcher", "marketer", "legal"))


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_runs_in_parallel_not_serial() -> None:
    """三次并行的 LLM 调用必须比串行基线更快完成。"""
    _, elapsed = await analyze("subscription box for rare herbal teas")
    # 每次调用约 1–3 秒。若串行执行，很容易超过 3 秒。
    # 在正常网络下，并行应当远低于 6 秒完成。
    assert elapsed < 6.0, f"expected parallel execution (<6s), got {elapsed:.2f}s"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_perspectives_differ_between_agents() -> None:
    per_agent, _ = await analyze("AI-powered meal planner")
    # 三种不同的视角应当产出三个不同的字符串。
    r, m, lg = per_agent["researcher"], per_agent["marketer"], per_agent["legal"]
    assert r != m
    assert m != lg
    assert r != lg
