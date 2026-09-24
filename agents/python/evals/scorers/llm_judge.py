"""用于相关性 / 完整性的 LLM 作为评判者（LLM-as-judge）评分器——替代了旧的
关键词别名表（``AgentEvaluator._score_completeness``）——后者会把一个孤零零的
``"$"`` 字符算作满足预期的 ``price`` 字段，而不管其中是否真的出现了价格。

不用于事实核验（grounding）——只要存在可供核验论断的真实数据，
``db_groundedness.py`` 的确定性数据库检查就严格更优。这个评分器针对的是
无法机械检查的那部分：响应是否真正回答了问题并覆盖了预期内容，且这种覆盖
是关键词表无法捕捉的（一个响应可以在不出现字面词 "price" 的情况下传达价格）。

评判结论按 ``(case_id, sha256(response_text))`` 缓存，这样在一次无关的代码
变更之后重跑套件时，就不会为未变化的响应再次消耗评判 token。
"""

from __future__ import annotations

import hashlib
import json
import logging

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

_JUDGE_INSTRUCTIONS = (
    "You are grading an AI shopping assistant's response for relevance and completeness. "
    "Given the user's question, the fields it was expected to cover, and the assistant's "
    "actual response, judge substance, not phrasing — a response that conveys a price "
    "without the literal word 'price' still counts. "
    "Reply with ONLY a JSON object, no other text: "
    '{"score": <float 0.0-1.0>, "reasoning": "<one sentence>", "failure_mode": <string or null>}. '
    "score 1.0 = fully relevant and complete, 0.5 = partially answers or misses some expected "
    "content, 0.0 = irrelevant or empty. failure_mode is a short label when score < 1.0 "
    '(e.g. "missing_field", "off_topic", "empty_response"), else null.'
)


class JudgeVerdict(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    reasoning: str
    failure_mode: str | None = None


# 进程生命周期内的缓存——评测以单次短时 CLI 调用的方式运行，
# 因此它不需要跨运行持久化（持久化由基线负责）。
_cache: dict[str, JudgeVerdict] = {}


def _cache_key(case_id: str, response_text: str) -> str:
    digest = hashlib.sha256(response_text.encode("utf-8")).hexdigest()[:16]
    return f"{case_id}:{digest}"


async def judge_response(
    case_id: str,
    user_input: str,
    response_text: str,
    expected_fields: list[str],
) -> JudgeVerdict:
    key = _cache_key(case_id, response_text)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    verdict = await _call_judge(user_input, response_text, expected_fields)
    _cache[key] = verdict
    return verdict


async def _call_judge(user_input: str, response_text: str, expected_fields: list[str]) -> JudgeVerdict:
    from agent_framework import Agent

    from shared.factory import get_chat_client

    judge = Agent(
        client=get_chat_client(),
        instructions=_JUDGE_INSTRUCTIONS,
        name="eval-judge",
    )
    prompt = (
        f"User question: {user_input}\n"
        f"Expected to cover: {', '.join(sorted(expected_fields)) or '(nothing specific)'}\n"
        f"Assistant response:\n{response_text or '(empty)'}"
    )
    try:
        response = await judge.run(prompt)
    except Exception as exc:
        logger.warning("llm_judge.call_failed error=%s", exc)
        return JudgeVerdict(score=0.0, reasoning=f"judge call failed: {exc}", failure_mode="judge_error")

    return _parse_verdict(response.text or "")


def _parse_verdict(text: str) -> JudgeVerdict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    try:
        data = json.loads(stripped)
        return JudgeVerdict(**data)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        logger.warning("llm_judge.unparsable_response error=%s text=%s", exc, text[:200])
        return JudgeVerdict(score=0.0, reasoning="judge response unparsable", failure_mode="judge_error")
