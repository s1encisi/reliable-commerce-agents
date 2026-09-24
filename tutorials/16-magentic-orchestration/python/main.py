"""第 16 章：Magentic 动态编排。

管理者拆分产品发布任务，选择研究、营销、法律三个工作智能体，
反复委派直到完成。运行提示字符串保持与现有回放一致。
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
from agent_framework.orchestrations import MagenticBuilder  # noqa: E402
from agent_framework_orchestrations._magentic import StandardMagenticManager  # noqa: E402
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
        # 第 9 阶段：可用任意 OpenAI 兼容端点（GitHub Models、OpenRouter、
        # vLLM、LM Studio、Ollama）替代 api.openai.com——见
        # tutorials/00-setup/README.md 的「没有付费 API 密钥怎么办？」一节。
        base_url=os.environ.get("LLM_BASE_URL") or None,
    )


def researcher() -> Agent:
    return Agent(
        _default_client(),
        instructions="You are a Market Researcher. Respond with one concrete market insight.",
        name="researcher",
    )


def marketer() -> Agent:
    return Agent(
        _default_client(),
        instructions="You are a Marketer. Respond with one tagline or positioning sentence.",
        name="marketer",
    )


def legal() -> Agent:
    return Agent(
        _default_client(),
        instructions="You are a Legal advisor. Respond with one regulatory or IP concern.",
        name="legal",
    )


def manager_agent() -> Agent:
    """Magentic 管理者用于分解与委派的规划 LLM。"""
    return Agent(
        _default_client(),
        instructions=(
            "You are a program manager coordinating a small team. "
            "Decompose the user's task into concrete subtasks and route each to the "
            "right specialist. Keep your reasoning tight."
        ),
        name="magentic-manager",
    )


def build_workflow():
    manager = StandardMagenticManager(
        agent=manager_agent(),
        max_round_count=6,
        max_stall_count=2,
    )
    return MagenticBuilder(
        participants=[researcher(), marketer(), legal()],
        manager=manager,
    ).build()


async def _workflow_events(workflow, message: str):
    """流式输出 Magentic 工作流事件。

    真实客户端和回放客户端共用 MAF 的流式执行器及最终响应终结器，
    无需提供方专用分支。
    """
    async for event in workflow.run(message, stream=True):
        yield event


async def plan(task: str) -> tuple[list[str], str]:
    """运行 Magentic，返回参与者顺序及最终答案。"""
    workflow = build_workflow()
    speakers: list[str] = []
    final_messages: list[str] = []
    async for event in _workflow_events(workflow, task):
        etype = getattr(event, "type", None)
        if etype == "group_chat":
            data = getattr(event, "data", None)
            # GroupChatRequestSentEvent 在派发时携带 participant_name。
            if data and type(data).__name__ == "GroupChatRequestSentEvent":
                pname = getattr(data, "participant_name", None)
                if pname:
                    speakers.append(pname)
        elif etype == "output":
            payload = getattr(event, "data", None)
            # magentic 管理者的最终 "output" 事件，其载荷可能是一个
            # AgentResponseUpdate 列表，也可能是单个对象，取决于具体运行——
            # 两种形状都要处理。
            items = payload if isinstance(payload, list) else [payload] if payload is not None else []
            for item in items:
                text = getattr(item, "text", None)
                if text:
                    final_messages.append(text)
    return speakers, "\n\n".join(final_messages).strip()


async def main() -> None:
    task = sys.argv[1] if len(sys.argv) > 1 else "plan a product launch for an AI meal planner"
    print(f"Task: {task}\n")
    speakers, answer = await plan(task)
    print(f"Delegates consulted: {', '.join(speakers) if speakers else '(manager handled directly)'}\n")
    print("Final answer:")
    print(answer)


if __name__ == "__main__":
    asyncio.run(main())
