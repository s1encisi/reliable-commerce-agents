"""第 22 章的可运行演示 —— 群聊 / 圆桌辩论。

默认是确定性的（不调用 LLM）：圆桌成员就是普通的可调用对象。请从后端包内运行，
这样 ``workflows`` 导入才能解析：

    cd agents/python && uv run python ../../tutorials/22-group-chat-debate/python/main.py
"""

from __future__ import annotations

import asyncio

from workflows.group_chat import GroupChatWorkflow


def value_voice(question: str, transcript: list[dict[str, str]]) -> str:
    return "Strong price for the feature set; frequent discounts."


def quality_voice(question: str, transcript: list[dict[str, str]]) -> str:
    prior = len(transcript)
    return f"Considering {prior} prior point(s): reviews show excellent build quality."


def synthesize(state) -> str:
    return (
        f"Verdict on '{state.question}': both value and quality perspectives are "
        f"positive across {len(state.transcript)} turns — recommended."
    )


async def main() -> None:
    workflow = GroupChatWorkflow(
        panelists=[("value", value_voice), ("quality", quality_voice)],
        synthesizer=synthesize,
    )
    state = await workflow.execute("Is the Sony WH-1000XM5 worth it?")

    print("Transcript:")
    for turn in state.transcript:
        print(f"  {turn['speaker']:>8}: {turn['text']}")
    print("\nModerator verdict:")
    print(f"  {state.verdict}")


if __name__ == "__main__":
    asyncio.run(main())
