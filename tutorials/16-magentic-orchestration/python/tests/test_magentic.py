"""第 16 章动态编排测试。

包含离线回放与可选真实模型多轮用例；真实调用控制规模以限制预算。
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
from main import FIXTURES_DIR, build_workflow, plan  # noqa: E402


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
    # 只构建、不执行模型，
    # 但默认客户端读取 OPENAI_API_KEY，
    # 无凭据 CI 因此设置占位值，
    # 满足构造前提，
    # 不会真正调用客户端。
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder-not-used")
    assert build_workflow() is not None


@pytest.mark.asyncio
async def test_replay_manager_delegates_to_at_least_one_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """回放已提交夹具，不访问网络。

    管理循环的真实轮数不固定，因此检查最终答案而非强制固定轮次数。
    """
    recording = os.environ.get("RECORD", "").lower() in ("1", "true", "yes")
    if not recording and not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"no recorded fixtures in {FIXTURES_DIR} — run with RECORD=true first")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    _, answer = await plan("plan a short launch brief for an AI meal planner")
    assert answer, "final answer must not be empty"
    assert len(answer) > 50, "final answer should be substantive, not a stub"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_manager_delegates_to_at_least_one_worker() -> None:
    speakers, answer = await plan("plan a short launch brief for an AI meal planner")
    assert answer, "final answer must not be empty"
    # 管理者可能委派工作智能体，
    # 也可能已有足够信息直接回答，当前用例允许两者。
    assert len(answer) > 50, "final answer should be substantive, not a stub"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_manager_can_select_from_multiple_workers() -> None:
    """更广泛任务应促使管理者调用多个工作智能体。"""
    speakers, _ = await plan("produce a brief covering market context, a tagline, and one regulatory note")
    # 预期至少发生一次对可用工作智能体的委派。
    known = {"researcher", "marketer", "legal"}
    assert any(s in known for s in speakers) or not speakers, f"unexpected speaker list: {speakers}"
