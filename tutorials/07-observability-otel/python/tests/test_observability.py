"""
第 07 章 —— 基于 OpenTelemetry 的可观测性：测试。

仅集成测试 —— 需要有真实的 LLM 调用才能产出有意义的 span。
使用一个内存 span exporter，好让我们对 span 的名称 / 属性做断言。
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
from main import FIXTURES_DIR, ask, build_agent, setup_tracing  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

# 一个全局的内存 exporter，供所有测试共享 —— OTel 的 TracerProvider 每个
# 进程只能设置一次，所以我们在这里就装上它。
_EXPORTER = InMemorySpanExporter()
setup_tracing(service_name="maf-v1-ch07-tests", exporter=_EXPORTER)


# ─────────────────── 回放测试（无需凭据，可在 CI 中运行） ────


@pytest.mark.asyncio
async def test_replay_run_emits_spans(monkeypatch: pytest.MonkeyPatch) -> None:
    """回放 tests/fixtures/replay/ —— 不走网络、不需凭据。

    曾针对真实 LLM 录制过一次（以 RECORD=true 运行），随后提交入库。
    对应 test_real_llm_run_emits_spans。
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"{FIXTURES_DIR} 中没有已录制的夹具 —— 请先以 RECORD=true 运行")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    _EXPORTER.clear()
    from opentelemetry import trace

    provider = trace.get_tracer_provider()

    agent = build_agent()
    answer = await ask(agent, "Say 'hello' and nothing else.")
    provider.force_flush()

    spans = _EXPORTER.get_finished_spans()
    assert spans, "一次完成的智能体运行之后，期望至少有一个 span"
    assert answer, "期望得到非空回答"


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
async def test_real_llm_run_emits_spans() -> None:
    _EXPORTER.clear()
    from opentelemetry import trace

    provider = trace.get_tracer_provider()

    agent = build_agent()
    answer = await ask(agent, "Say 'hello' and nothing else.")
    provider.force_flush()

    spans = _EXPORTER.get_finished_spans()
    assert spans, "一次完成的智能体运行之后，期望至少有一个 span"
    assert answer, "期望得到非空回答"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_spans_include_genai_attributes() -> None:
    _EXPORTER.clear()
    from opentelemetry import trace

    provider = trace.get_tracer_provider()

    agent = build_agent()
    await ask(agent, "Say 'hi'.")
    provider.force_flush()

    spans = _EXPORTER.get_finished_spans()
    # MAF 的埋点遵循 GenAI 语义约定 —— 至少有一个 span 应当带 gen_ai.* 属性。
    genai_attrs = [k for span in spans for k in (span.attributes or {}).keys() if k.startswith("gen_ai.")]
    all_keys = [list((s.attributes or {}).keys()) for s in spans]
    assert genai_attrs, f"期望 span 上带有 GenAI 属性；实际键为：{all_keys}"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_two_runs_produce_distinct_trace_ids() -> None:
    _EXPORTER.clear()
    from opentelemetry import trace

    provider = trace.get_tracer_provider()

    agent = build_agent()
    await ask(agent, "Say '1'.")
    await ask(agent, "Say '2'.")
    provider.force_flush()

    spans = _EXPORTER.get_finished_spans()
    trace_ids = {span.get_span_context().trace_id for span in spans}
    assert len(trace_ids) >= 2, f"期望两次运行得到不同的 trace id，实际只有 {len(trace_ids)} 个"
