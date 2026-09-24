"""
MAF v1 — 第 13 章：并发编排（Python）

三个智能体并行分析同一个产品创意：研究员检查市场契合度，市场人员建议
定位角度，法务标记风险。ConcurrentBuilder 收集每个智能体的响应；我们
把三份都打印出来，并展示汇总结果。

运行：
    python tutorials/13-concurrent-orchestration/python/main.py "ultrasonic pet collar"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from agent_framework.orchestrations import ConcurrentBuilder  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"


def _default_client() -> OpenAIChatClient | OpenAIChatCompletionClient | ReplayChatClient:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "replay":
        return ReplayChatClient(
            fixtures_dir=FIXTURES_DIR,
            record=os.environ.get("RECORD", "").lower() in ("1", "true", "yes"),
            record_provider=os.environ.get("REPLAY_RECORD_PROVIDER", "openai"),
        )
    if provider == "azure":
        return OpenAIChatCompletionClient(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"),
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
    return OpenAIChatClient(
        model=os.environ.get("LLM_MODEL", "gpt-4.1"),
        api_key=os.environ["OPENAI_API_KEY"],
        # 阶段 9：可改用任意 OpenAI 兼容端点（GitHub Models、OpenRouter、
        # vLLM、LM Studio、Ollama）替代 api.openai.com —— 见
        # tutorials/00-setup/README.md 的「没有付费 API 密钥？两条路」一节。
        base_url=os.environ.get("LLM_BASE_URL") or None,
    )


def researcher() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Market Researcher. In one sentence, assess the market fit of the product idea the user provides."
        ),
        name="researcher",
    )


def marketer() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Marketer. In one sentence, propose a positioning angle for the product idea the user provides."
        ),
        name="marketer",
    )


def legal() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Legal advisor. In one sentence, flag one regulatory or IP "
            "concern about the product idea the user provides."
        ),
        name="legal",
    )


def build_workflow():
    return ConcurrentBuilder(participants=[researcher(), marketer(), legal()]).build()


async def _workflow_events(workflow, message: str):
    """从一次流式运行中产出工作流事件。

    ``workflow.run(..., stream=True)`` 会通过 MAF 的流式 AgentExecutor 路径
    驱动每个参与者的一轮，而该路径又对流式聊天客户端的响应进行流转。
    ``ReplayChatClient``（见 tutorials/_shared/replay_client.py）接上了真实
    客户端所用的同一个收尾器，因此回放模式能沿着这条相同路径正确流式输出 ——
    这里无需针对特定提供方写分支。
    """
    async for event in workflow.run(message, stream=True):
        yield event


async def analyze(idea: str) -> tuple[dict[str, str], float]:
    """运行并发分析。返回 {agent_name: response} 以及墙钟耗时（秒）。"""
    workflow = build_workflow()
    per_agent: dict[str, str] = {}
    start = time.perf_counter()
    async for event in _workflow_events(workflow, idea):
        if getattr(event, "type", None) != "executor_completed":
            continue
        payload = getattr(event, "data", None)
        if not isinstance(payload, list):
            continue
        for item in payload:
            agent_resp = getattr(item, "agent_response", None)
            eid = getattr(item, "executor_id", "")
            text = getattr(agent_resp, "text", None)
            if text and eid in ("researcher", "marketer", "legal"):
                per_agent[eid] = text
    elapsed = time.perf_counter() - start
    return per_agent, elapsed


async def main() -> None:
    idea = sys.argv[1] if len(sys.argv) > 1 else "a subscription box for rare herbal teas"
    print(f"Idea: {idea}\n")
    per_agent, elapsed = await analyze(idea)
    for name in ("researcher", "marketer", "legal"):
        print(f"{name.capitalize():<12} {per_agent.get(name, '(no response)')}\n")
    print(f"Wall-clock: {elapsed:.2f}s (three LLM calls ran in parallel)")


if __name__ == "__main__":
    asyncio.run(main())
