"""
MAF v1 —— 第 14 章：移交式编排（Python）

分诊智能体把用户问题路由到数学或历史专家。网状结构允许专家交回分诊
智能体以处理追问。自主模式无需等待人工输入即可作答——适合聊天界面。

运行：
    python tutorials/14-handoff-orchestration/python/main.py "37 * 42 等于多少？"
    python tutorials/14-handoff-orchestration/python/main.py "二战是哪一年结束的？"
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
from agent_framework.orchestrations import HandoffBuilder  # noqa: E402
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


def triage() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Triage agent. Read the user's question and hand off to the "
            "right specialist: math for arithmetic/math questions, history for "
            "historical facts or dates. If the specialist answers, simply acknowledge "
            "and stop — do not rewrite the answer."
        ),
        name="triage",
        # 自 agent-framework-orchestrations>=1.0.1 起，HandoffBuilder.build()
        # 要求每个参与者都设置该参数——其中间件会在移交期间短路工具调用，
        # 因此本地历史必须与服务端实际看到的内容保持一致。
        require_per_service_call_history_persistence=True,
    )


def math_expert() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Math expert. Answer arithmetic and math questions directly "
            "with a single short sentence containing the numerical answer."
        ),
        name="math",
        require_per_service_call_history_persistence=True,
    )


def history_expert() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a History expert. Answer historical questions in one short "
            "sentence with the specific date or year."
        ),
        name="history",
        require_per_service_call_history_persistence=True,
    )


def build_workflow():
    t = triage()
    m = math_expert()
    h = history_expert()
    return (
        HandoffBuilder(participants=[t, m, h])
        .with_start_agent(t)
        .add_handoff(t, [m, h])
        .add_handoff(m, [t])  # 专家可交回分诊智能体以处理追问
        .add_handoff(h, [t])
        .with_autonomous_mode(agents=[t, m, h], turn_limits={"triage": 3, "math": 2, "history": 2})
        .build()
    )


async def _workflow_events(workflow, message: str):
    """以流式运行产出工作流事件。

    ``workflow.run(..., stream=True)`` 通过 MAF 的流式 AgentExecutor 路径
    驱动每个参与者的发言，进而流式获取聊天客户端的响应。``ReplayChatClient``
    （见 tutorials/_shared/replay_client.py）接入了与真实客户端相同的
    终结器，因此回放模式也能沿同一条路径正确流式输出——此处无需任何
    针对具体提供方的分支。
    """
    async for event in workflow.run(message, stream=True):
        yield event


async def ask(question: str) -> tuple[list[str], str]:
    """运行移交图；返回（有序的参与者 id 列表，最终答案）。

    输出文本以分块形式出现在 'output' 事件上（每个增量一块）。
    我们按执行器 id 顺序拼接，以重建每个智能体的发言。
    """
    workflow = build_workflow()
    current_agent: str | None = None
    buffers: list[tuple[str, list[str]]] = []
    handoffs: list[str] = []
    async for event in _workflow_events(workflow, question):
        etype = getattr(event, "type", None)
        eid = getattr(event, "executor_id", "") if etype == "output" else None
        if etype == "output" and eid in {"triage", "math", "history"}:
            if current_agent != eid:
                current_agent = eid
                buffers.append((eid, []))
            update = getattr(event, "data", None)
            text = getattr(update, "text", None) if update is not None else None
            if text:
                buffers[-1][1].append(text)
        elif etype == "handoff_sent":
            data = getattr(event, "data", None)
            target = getattr(data, "target", None)
            if target:
                handoffs.append(target)
    turns = [(eid, "".join(parts).strip()) for eid, parts in buffers if any(parts)]
    participants = [eid for eid, _ in turns]
    final = turns[-1][1] if turns else ""
    return participants, final


async def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else "What's 37 * 42?"
    print(f"Q: {question}")
    participants, answer = await ask(question)
    print(f"Routing: {' → '.join(participants)}")
    print(f"A: {answer}")


if __name__ == "__main__":
    asyncio.run(main())
