"""
MAF v1 — 第 11 章：工作流中的智能体（Python）

把一个 Agent 包装成工作流里的执行器。两个智能体执行器串联：
英语 → 法语 → 西班牙语。每个智能体都是一次真实的 LLM 调用；工作流负责
协调它们的输入与输出。

运行：
    python tutorials/11-agents-in-workflows/python/main.py "Hello world"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, Message  # noqa: E402
from agent_framework._workflows._agent_executor import (  # noqa: E402
    AgentExecutor,
    AgentExecutorRequest,
    AgentExecutorResponse,
)
from agent_framework._workflows._executor import Executor, handler  # noqa: E402
from agent_framework._workflows._workflow_builder import WorkflowBuilder  # noqa: E402
from agent_framework._workflows._workflow_context import WorkflowContext  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"


def _default_client() -> OpenAIChatClient | OpenAIChatCompletionClient | ReplayChatClient:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "replay":
        # ReplayChatClient 的流式分支接上了真实客户端所用的同一个收尾器
        # （BaseChatClient._build_response_stream）—— 本章的工作流级流式输出
        # （下方 translate() 调用 workflow.run(..., stream=True)，进而通过
        # agent.run(stream=True) 驱动每个 AgentExecutor）正需要它把增量更新
        # 折叠回一个 ChatResponse，再由 get_final_response() 取出。
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


def translator(target_language: str, name: str) -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            f"You are a translator. Translate the user's message to {target_language}. "
            "Output ONLY the translation — no quotes, no preamble, no explanation."
        ),
        name=name,
    )


class InputAdapter(Executor):
    """把工作流输入（一个普通字符串）转换成 AgentExecutorRequest。"""

    def __init__(self) -> None:
        super().__init__(id="input-adapter")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[AgentExecutorRequest]) -> None:
        await ctx.send_message(
            AgentExecutorRequest(
                messages=[Message(role="user", contents=[message])],
                should_respond=True,
            )
        )


class OutputAdapter(Executor):
    """把最终的 AgentExecutorResponse 解包成一个普通字符串输出。"""

    def __init__(self) -> None:
        super().__init__(id="output-adapter")

    @handler
    async def run(self, response: AgentExecutorResponse, ctx: WorkflowContext[None, str]) -> None:
        await ctx.yield_output(response.agent_response.text)


def build_workflow():
    input_adapter = InputAdapter()
    english_to_french = AgentExecutor(translator("French", name="en-to-fr"), id="en-to-fr")
    french_to_spanish = AgentExecutor(translator("Spanish", name="fr-to-es"), id="fr-to-es")
    output_adapter = OutputAdapter()

    return (
        WorkflowBuilder(start_executor=input_adapter)
        .add_edge(input_adapter, english_to_french)
        .add_edge(english_to_french, french_to_spanish)
        .add_edge(french_to_spanish, output_adapter)
        .build()
    )


async def translate(text: str) -> str:
    workflow = build_workflow()
    outputs: list[str] = []
    async for event in workflow.run(text, stream=True):
        if getattr(event, "type", None) == "output":
            outputs.append(event.data)
    return outputs[-1] if outputs else ""


async def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else "Hello, how are you?"
    print(f"English input: {text}")
    result = await translate(text)
    print(f"Spanish output: {result}")


if __name__ == "__main__":
    asyncio.run(main())
