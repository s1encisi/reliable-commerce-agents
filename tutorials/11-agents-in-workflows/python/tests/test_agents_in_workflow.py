"""
第 11 章 —— 工作流中的智能体：测试。

仅集成测试 —— 翻译链每次运行会调用两次真实 Azure OpenAI
（英语→法语、法语→西班牙语）。测试里会校验工作流接线，
这样在真正打 LLM 之前就能确认图是正确组装的。
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
from main import FIXTURES_DIR, build_workflow, translate  # noqa: E402


def test_workflow_has_four_executors_including_two_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    # 仅构建 —— 从不调用 LLM，因此不应该需要真实凭据。_default_client() 的
    # OpenAI 分支通过硬性 os.environ[...] 读取 OPENAI_API_KEY（在没有密钥的
    # 真实运行中会快速失败），无凭据的 CI 任务里曾在这里被绊住。既然客户端
    # 从未被真正调用，放一个占位符就够了。
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder-not-used")
    workflow = build_workflow()
    ids = {getattr(e, "id", None) for e in workflow.get_executors_list()}
    assert {"input-adapter", "en-to-fr", "fr-to-es", "output-adapter"} <= ids


# ─────────────────── 回放测试（无需凭据，可在 CI 中运行） ────


@pytest.mark.asyncio
async def test_replay_translates_hello_to_spanish(monkeypatch: pytest.MonkeyPatch) -> None:
    """回放 tests/fixtures/replay/ —— 无网络、无凭据。

    两个智能体执行器，两份录制的夹具（英语→法语、法语→西班牙语）。
    曾对真实 LLM 录制一次（带 RECORD=true）并提交入库。
    与 test_real_llm_translates_hello_to_spanish 对应。
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"no recorded fixtures in {FIXTURES_DIR} — run with RECORD=true first")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    result = (await translate("Hello, how are you?")).lower()
    # 西班牙语对应说法是 "hola, ¿cómo estás?" —— 两个词任一命中即可。
    assert "hola" in result, f"expected Spanish in final output, got: {result!r}"


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
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_translates_hello_to_spanish() -> None:
    result = (await translate("Hello, how are you?")).lower()
    # 西班牙语对应说法是 "hola, ¿cómo estás?" —— 两个词任一命中即可。
    assert "hola" in result, f"expected Spanish in final output, got: {result!r}"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_chain_fires_both_agent_executors() -> None:
    """跟踪本次运行中哪些智能体执行器发出了输出事件。"""
    workflow = build_workflow()
    invoked: list[str] = []
    async for event in workflow.run("Good morning", stream=True):
        if getattr(event, "type", None) == "executor_completed":
            invoked.append(getattr(event, "executor_id", ""))
    # 法语和西班牙语两个翻译器都必须已完成。
    assert "en-to-fr" in invoked
    assert "fr-to-es" in invoked
    assert invoked.index("en-to-fr") < invoked.index("fr-to-es")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_output_contains_spanish_markers() -> None:
    result = (await translate("The sun is shining")).lower()
    # 这些常见西班牙语词大概率会出现其中之一。
    assert any(word in result for word in ("el ", "la ", "está", "sol"))
