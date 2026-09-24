"""
第 22 章 —— 群聊辩论：测试。

不涉及 LLM —— 圆桌成员就是普通的可调用对象，因此每条断言都是精确的
（与第 09 章、第 30 章的无 LLM 工作流测试遵循同一先例）。

本章在本系列中比较特殊：它从 `agents/python` 导入*生产*的
`workflows.group_chat` 模块，而不是自带一份独立示例，因此下面的 sys.path
设置会伸进后端包，正如本章自己的 README 告诉读者该如何运行它那样。

这个导入也正是本文件存在的原因。生产模块有自己的测试，位于
`agents/python/tests/test_workflow_group_chat.py`，在很长一段时间里，那被视为
同时也覆盖了本章。并非如此：它覆盖的是模块，不是本章。演示自己的圆桌成员、
它的综合器，以及本章赖以成立的那个主张 —— 靠后的圆桌成员能看到此前的发言 ——
都没有被测到。那个主张恰恰是读者唯一带走的东西，而它在演示输出里是看不见的：
两位恰好不引用彼此的圆桌成员，无论状态是否共享，产出的记录都一模一样。
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

# 本章从 agents/python 运行，这样 `workflows` 才能解析 —— 见其 README。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4] / "agents" / "python"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from main import quality_voice, synthesize, value_voice  # noqa: E402
from workflows.group_chat import GroupChatState, GroupChatWorkflow  # noqa: E402

QUESTION = "Is the Sony WH-1000XM5 worth it?"


def _says(text: str):
    return lambda question, transcript: text


def _reports_prior_speakers():
    def responder(question, transcript):
        if not transcript:
            return "I spoke first"
        return "I heard: " + ", ".join(turn["speaker"] for turn in transcript)

    return responder


# ─────────────── 顺序性 ───────────────


async def test_every_panelist_speaks_once_in_declared_order() -> None:
    workflow = GroupChatWorkflow(
        panelists=[("first", _says("a")), ("second", _says("b")), ("third", _says("c"))]
    )

    state = await workflow.execute(QUESTION)

    assert [turn["speaker"] for turn in state.transcript] == ["first", "second", "third"]
    assert [turn["text"] for turn in state.transcript] == ["a", "b", "c"]


async def test_a_later_panelist_sees_every_earlier_turn() -> None:
    # 这个模式赖以成立的那条断言。若不共享记录，第三位圆桌成员会报告
    # "I spoke first" —— 而且整轮运行里其他任何地方都看不出异常。
    workflow = GroupChatWorkflow(
        panelists=[
            ("first", _reports_prior_speakers()),
            ("second", _reports_prior_speakers()),
            ("third", _reports_prior_speakers()),
        ]
    )

    state = await workflow.execute(QUESTION)

    assert state.transcript[0]["text"] == "I spoke first"
    assert state.transcript[1]["text"] == "I heard: first"
    assert state.transcript[2]["text"] == "I heard: first, second"


async def test_a_panelist_cannot_see_turns_that_have_not_happened_yet() -> None:
    # 顺序性保证的另一半，且并不能由上一条测试推出：如果圆桌是并发跑的，
    # 一份按引用共享的记录就可能暴露出后面的发言。
    seen: list[int] = []

    def counting(question, transcript):
        seen.append(len(transcript))
        return "ok"

    await GroupChatWorkflow(
        panelists=[("a", counting), ("b", counting), ("c", counting)]
    ).execute(QUESTION)

    assert seen == [0, 1, 2]


async def test_the_question_reaches_every_panelist() -> None:
    questions: list[str] = []

    def capture(question, transcript):
        questions.append(question)
        return "ok"

    await GroupChatWorkflow(panelists=[("a", capture), ("b", capture)]).execute(QUESTION)

    assert questions == [QUESTION, QUESTION]


# ─────────────── 主持人 ───────────────


async def test_the_moderator_runs_last_and_is_recorded() -> None:
    # completed_steps 是审计轨迹。一个在最后一位圆桌成员之前运行的主持人，
    # 仍会产出一个看似合理的结论 —— 只是基于一份过短的记录。
    state = await GroupChatWorkflow(
        panelists=[("value", _says("cheap")), ("quality", _says("solid"))]
    ).execute(QUESTION)

    assert state.completed_steps == ["value", "quality", "moderator"]


async def test_the_default_synthesis_names_every_speaker() -> None:
    state = await GroupChatWorkflow(
        panelists=[("value", _says("cheap")), ("quality", _says("solid"))]
    ).execute(QUESTION)

    assert "2 perspective(s)" in state.verdict
    assert "value, quality" in state.verdict
    assert QUESTION in state.verdict


async def test_a_custom_synthesizer_replaces_the_default() -> None:
    state = await GroupChatWorkflow(
        panelists=[("value", _says("cheap"))],
        synthesizer=lambda _state: "MY VERDICT",
    ).execute(QUESTION)

    assert state.verdict == "MY VERDICT"


async def test_the_synthesizer_sees_the_complete_transcript() -> None:
    # 如果拿到的是最后一位发言之前取的副本，这里会读到 1 —— 而结论会
    # 自信地出错，而不是明显地崩掉。
    seen = -1

    def synthesizer(state: GroupChatState) -> str:
        nonlocal seen
        seen = len(state.transcript)
        return "done"

    await GroupChatWorkflow(
        panelists=[("a", _says("x")), ("b", _says("y"))], synthesizer=synthesizer
    ).execute(QUESTION)

    assert seen == 2


# ─────────────── 失败处理 ───────────────


async def test_a_failing_panelist_becomes_a_visible_turn() -> None:
    # 因为某位专家超时就让整张圆桌崩掉，比让它报告「有一个声音缺席」更糟：
    # 主持人可以围绕一个它看得见的缺口去做调和。
    def boom(question, transcript):
        raise RuntimeError("provider down")

    state = await GroupChatWorkflow(
        panelists=[("value", _says("cheap")), ("quality", boom)]
    ).execute(QUESTION)

    assert len(state.transcript) == 2
    assert "quality could not respond" in state.transcript[1]["text"]
    assert "provider down" in state.transcript[1]["text"]
    assert state.verdict


async def test_a_panelist_after_a_failure_still_runs_and_sees_the_failed_turn() -> None:
    def boom(question, transcript):
        raise RuntimeError("nope")

    state = await GroupChatWorkflow(
        panelists=[("a", boom), ("b", _reports_prior_speakers())]
    ).execute(QUESTION)

    assert state.transcript[1]["text"] == "I heard: a"
    assert state.completed_steps == ["a", "b", "moderator"]


async def test_an_empty_panel_is_rejected() -> None:
    # 一个总结「沉默」的主持人会针对什么都没有的东西给出一个自信的结论。
    import pytest

    with pytest.raises(ValueError, match="at least one panelist"):
        await GroupChatWorkflow(panelists=[]).execute(QUESTION)


async def test_a_single_panelist_panel_still_reaches_the_moderator() -> None:
    state = await GroupChatWorkflow(panelists=[("solo", _says("x"))]).execute(QUESTION)

    assert len(state.transcript) == 1
    assert state.completed_steps == ["solo", "moderator"]
    assert state.verdict


# ─────────────── 本章自己的演示 ───────────────


async def test_the_demo_panel_produces_a_two_turn_debate_and_a_verdict() -> None:
    state = await GroupChatWorkflow(
        panelists=[("value", value_voice), ("quality", quality_voice)],
        synthesizer=synthesize,
    ).execute(QUESTION)

    assert [turn["speaker"] for turn in state.transcript] == ["value", "quality"]
    assert "recommended" in state.verdict


async def test_the_quality_panelist_demonstrably_reads_the_transcript() -> None:
    # 演示自己对「这是一场辩论」的证明。如果质量视角哪天不再统计此前的发言，
    # 本章会继续正常运行，却不再演示它自己的主题。
    state = await GroupChatWorkflow(
        panelists=[("value", value_voice), ("quality", quality_voice)],
        synthesizer=synthesize,
    ).execute(QUESTION)

    assert "1 prior point(s)" in state.transcript[1]["text"]


async def test_an_async_panelist_is_awaited() -> None:
    # Responder 契约允许返回协程，这样由真实智能体支撑的圆桌成员无需改动
    # 签名就能接入 —— 这正是 orchestrator/modes/group_chat_mode.py 在生产中
    # 所走的路径。
    async def async_voice(question, transcript):
        return "async take"

    state = await GroupChatWorkflow(panelists=[("async", async_voice)]).execute(QUESTION)

    assert state.transcript[0]["text"] == "async take"
