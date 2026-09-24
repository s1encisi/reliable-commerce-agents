"""第 15 章：群聊编排。

写作者、评论者和编辑讨论文案。演示两种管理策略：selection_func
按索引轮询，不调用模型；orchestrator_agent 由模型根据已有对话
决定下一位发言者和终止时机。运行命令中的提示字符串保持与夹具一致。
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
from agent_framework.orchestrations import GroupChatBuilder, GroupChatState  # noqa: E402
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


def writer() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Writer. Draft or revise copy the user asks for. Output exactly one short line — no preamble."
        ),
        name="writer",
    )


def critic() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Critic. Read the Writer's latest draft and respond in one "
            "sentence pointing out one concrete improvement. Do not rewrite."
        ),
        name="critic",
    )


def editor() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are an Editor. Given the Writer's draft and the Critic's feedback, "
            "produce the final polished line. Output exactly one short line — no preamble."
        ),
        name="editor",
    )


def round_robin_selector(state: GroupChatState) -> str:
    """按轮次索引轮询参与者。

    participants 为有序映射，current_round % n 决定下一位；
    构建器 max_rounds=3 限制总轮数。
    """
    names = list(state.participants.keys())
    return names[state.current_round % len(names)]


def prompt_driven_orchestrator() -> Agent:
    """创建选择下一位发言者的模型管理智能体。

    通过 GroupChatBuilder.orchestrator_agent 接入，MAF 每轮提供已有
    对话并请求选择，无需另写管理循环。
    """
    return Agent(
        _default_client(),
        name="orchestrator",
        description="Coordinates the Writer/Critic/Editor group chat.",
        instructions=(
            "You coordinate a Writer/Critic/Editor group chat about marketing copy.\n"
            "Guidelines:\n"
            "- Start with the Writer so there is a draft to react to.\n"
            "- Invite the Critic after the Writer has produced a draft.\n"
            "- Invite the Editor only after both Writer and Critic have spoken.\n"
            "- Stop once the Editor has produced a polished final line."
        ),
    )


def build_workflow(strategy: str = "round-robin"):
    """按管理策略构建群聊。

    round-robin 使用确定性选择函数，prompt 使用模型管理智能体。
    """
    participants = [writer(), critic(), editor()]

    if strategy == "prompt":
        return GroupChatBuilder(
            participants=participants,
            orchestrator_agent=prompt_driven_orchestrator(),
            # 硬性安全网；编排器可能更早结束。
            max_rounds=4,
        ).build()

    return GroupChatBuilder(
        participants=participants,
        selection_func=round_robin_selector,
        max_rounds=3,
    ).build()


async def _workflow_events(workflow, message: str):
    """输出工作流流式事件。

    MAF 通过流式 AgentExecutor 驱动参与者；回放客户端也安装相同
    终结器，因此无需按提供方分支处理。
    """
    async for event in workflow.run(message, stream=True):
        yield event


async def run(topic: str, strategy: str = "round-robin") -> list[tuple[str, str]]:
    """执行群聊，按轮次返回（发言者，文本）列表。"""
    workflow = build_workflow(strategy)
    turns: list[tuple[str, str]] = []
    async for event in _workflow_events(workflow, topic):
        etype = getattr(event, "type", None)
        if etype == "group_chat":
            data = getattr(event, "data", None)
            speaker = getattr(data, "agent_name", None) or getattr(data, "source", None)
            message = getattr(data, "message", None) or getattr(data, "content", None)
            text = getattr(message, "text", None) if message else None
            if speaker and text:
                turns.append((speaker, text))
        elif etype == "executor_completed":
            payload = getattr(event, "data", None)
            if isinstance(payload, list):
                for item in payload:
                    agent_resp = getattr(item, "agent_response", None)
                    eid = getattr(item, "executor_id", "")
                    text = getattr(agent_resp, "text", None)
                    if text and eid in {"writer", "critic", "editor"}:
                        turns.append((eid, text))
    return turns


async def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else "slogan for a coffee shop"
    strategy = sys.argv[2] if len(sys.argv) > 2 else "round-robin"
    print(f"Topic: {topic}")
    print(f"Manager: {strategy}\n")

    turns = await run(topic, strategy)
    for speaker, text in turns:
        print(f"{speaker:<8} {text}\n")


if __name__ == "__main__":
    asyncio.run(main())
