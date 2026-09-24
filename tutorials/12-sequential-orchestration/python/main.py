"""
MAF v1 — 第 12 章：顺序编排（Python）

SequentialBuilder 把智能体串成链：每个智能体都能看到目前为止的完整对话，
并在其上追加自己的一轮。经典的三步文章流水线：撰写者 → 评审者 → 定稿者。

运行：
    python tutorials/12-sequential-orchestration/python/main.py "quantum computing basics"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from agent_framework.orchestrations import SequentialBuilder  # noqa: E402
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


def writer() -> Agent:
    return Agent(
        _default_client(),
        instructions=("You are a Writer. Draft a 2-sentence paragraph on the topic the user provides. Keep it short."),
        name="writer",
    )


def reviewer() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Reviewer. Read the draft above and produce a single-sentence review "
            "pointing out one strength and one weakness. Do not rewrite the draft."
        ),
        name="reviewer",
    )


def finalizer() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Finalizer. Produce a one-sentence final version of the paragraph that "
            "addresses the reviewer's feedback. Output ONLY the final sentence — no preamble."
        ),
        name="finalizer",
    )


def build_workflow():
    return SequentialBuilder(participants=[writer(), reviewer(), finalizer()]).build()


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


async def run(topic: str) -> list[str]:
    """运行顺序执行流水线，按顺序返回每个智能体的响应文本。"""
    workflow = build_workflow()
    per_agent: dict[str, str] = {}
    async for event in _workflow_events(workflow, topic):
        # 每个智能体的一轮会出现在 executor_completed 事件里，其 data
        # 是一个只含一个 AgentExecutorResponse 的列表。
        if getattr(event, "type", None) != "executor_completed":
            continue
        payload = getattr(event, "data", None)
        if not isinstance(payload, list):
            continue
        for item in payload:
            agent_resp = getattr(item, "agent_response", None)
            eid = getattr(item, "executor_id", "")
            text = getattr(agent_resp, "text", None)
            if text and eid:
                per_agent[eid] = text
    ordered = ["writer", "reviewer", "finalizer"]
    return [per_agent.get(name, "") for name in ordered]


async def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else "quantum computing basics"
    print(f"Topic: {topic}\n")
    writer_out, reviewer_out, final = await run(topic)
    print(f"Writer:    {writer_out}\n")
    print(f"Reviewer:  {reviewer_out}\n")
    print(f"Finalizer: {final}")


if __name__ == "__main__":
    asyncio.run(main())
