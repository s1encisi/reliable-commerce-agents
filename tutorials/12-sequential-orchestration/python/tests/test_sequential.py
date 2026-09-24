"""
第 12 章 —— 顺序编排：测试。

仅集成测试 —— 顺序执行流水线每次运行会发起三次真实 LLM 调用。
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
from main import FIXTURES_DIR, build_workflow, run  # noqa: E402


def _llm_available() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        return bool(
            os.environ.get("AZURE_OPENAI_ENDPOINT")
            and (os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))
        )
    key = os.environ.get("OPENAI_API_KEY", "")
    return bool(key) and not key.startswith("sk-your-")


def test_workflow_builds_with_three_participants(monkeypatch: pytest.MonkeyPatch) -> None:
    # 仅构建 —— 从不调用 LLM，因此不应该需要真实凭据。_default_client() 的
    # OpenAI 分支通过硬性 os.environ[...] 读取 OPENAI_API_KEY，无凭据的 CI
    # 任务里曾在这里被绊住。既然客户端从未被真正调用，放一个占位符就够了。
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder-not-used")
    workflow = build_workflow()
    assert workflow is not None


@pytest.mark.asyncio
async def test_replay_runs_all_three_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    """回放 tests/fixtures/replay/ —— 无网络、无凭据。

    曾对真实 LLM 录制一次（即下方的 test_real_llm_runs_all_three_agents，
    带 RECORD=true 运行）并提交入库。
    """
    recording = os.environ.get("RECORD", "").lower() in ("1", "true", "yes")
    if not recording and not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"no recorded fixtures in {FIXTURES_DIR} — run with RECORD=true first")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    writer_out, reviewer_out, final = await run("Why sleep matters")
    assert writer_out, "writer must produce an output"
    assert reviewer_out, "reviewer must produce an output"
    assert final, "finalizer must produce an output"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_runs_all_three_agents() -> None:
    writer_out, reviewer_out, final = await run("Why sleep matters")
    assert writer_out, "writer must produce an output"
    assert reviewer_out, "reviewer must produce an output"
    assert final, "finalizer must produce an output"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_writer_drafts_and_reviewer_critiques() -> None:
    writer_out, reviewer_out, _ = await run("Benefits of learning Python")
    # 撰写者应当产出多个句子（至少有一个句号）。
    assert "." in writer_out
    # 评审者应当提及批评相关概念（优点/缺点式表述）。
    lowered = reviewer_out.lower()
    assert any(k in lowered for k in ("strength", "weakness", "could", "however", "but", "improve"))


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_outputs_differ_between_agents() -> None:
    w, r, f = await run("The importance of exercise")
    # 三个各不相同的输出 —— 没有意外回环。
    assert w != r
    assert r != f
    assert w != f
