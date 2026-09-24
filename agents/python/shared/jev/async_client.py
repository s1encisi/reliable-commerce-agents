"""真实请求链使用的异步 Jev 客户端；保留同步客户端供旧评测复现。"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Any

import httpx

from shared.config import settings
from shared.context_pipeline import public_text
from shared.jev.decisions import SPECIALIST_ROUTES
from shared.paid_transport import PaidTransport, usage_counts


class DecisionError(ValueError):
    """决策响应不满足类型、候选或概率契约。"""


@dataclass(frozen=True)
class Decision:
    route: str
    probabilities: dict[str, float]
    confidence: float
    model: str
    latency_ms: float
    usage: tuple[int, int] | None
    question_version: str = "commerce-route-v1"


def parse_decision(data: dict[str, Any], candidates: set[str], elapsed_ms: float) -> Decision:
    try:
        answer = data["answers"]["route"]
        if answer["type"] != "choice" or answer["choice"] not in candidates:
            raise ValueError
        probs = answer["probabilities"]
        confidence = answer["confidence"]
        if set(probs) != candidates or isinstance(confidence, bool) or not isinstance(confidence, (float, int)):
            raise ValueError
        values = [*probs.values(), confidence]
        if any(
            isinstance(p, bool) or not isinstance(p, (float, int)) or not math.isfinite(p) or not 0 <= p <= 1
            for p in values
        ):
            raise ValueError
        if not math.isclose(sum(probs.values()), 1.0, abs_tol=0.02) or data["model"] != "jev-1.13.0":
            raise ValueError
        return Decision(answer["choice"], probs, confidence, data["model"], elapsed_ms, usage_counts(data))
    except (KeyError, TypeError, ValueError) as exc:
        raise DecisionError("Jev 返回非法决策，必须回退") from exc


async def decide_route(
    state: dict[str, Any], available: set[str], *, http: httpx.AsyncClient | None = None
) -> Decision:
    import json

    criteria = {key: value for key, value in SPECIALIST_ROUTES.items() if key in available}
    criteria["defer"] = "信息不足、含多个无法确定主次的目标，或需要澄清。"
    if len(criteria) == 1:
        raise DecisionError("没有可用专业服务")
    payload = {
        "model": "jev-1.13.0",
        "state": json.loads(public_text(json.dumps(state, ensure_ascii=False))),
        "questions": {
            "route": {
                "type": "choice",
                "instructions": (
                    "根据最新问题及必要历史选择当前主要意图的负责服务。"
                    "候选仅表示分工，不赋予操作权限。不明确时选择 defer。"
                ),
                "criteria": criteria,
            }
        },
    }
    owned = http is None
    http = http or httpx.AsyncClient(transport=PaidTransport("jev"), timeout=settings.JEV_TIMEOUT_SECONDS)
    started = time.monotonic()
    try:
        async with asyncio.timeout(settings.JEV_TIMEOUT_SECONDS):
            for attempt in range(2):
                response = await http.post(
                    "https://api.typesafe.ai/v1/systemone",
                    json=payload,
                    headers={"Authorization": f"Bearer {settings.TYPESAFE_API_KEY}"},
                )
                if response.status_code not in {429, 529, 502, 503, 504} or attempt == 1:
                    response.raise_for_status()
                    return parse_decision(response.json(), set(criteria), (time.monotonic() - started) * 1000)
                try:
                    delay = max(0.05, float(response.headers.get("retry-after", ".1")))
                except ValueError:
                    delay = 0.1
                if delay >= settings.JEV_TIMEOUT_SECONDS - (time.monotonic() - started):
                    raise TimeoutError("Jev 重试等待超出总截止时间")
                await asyncio.sleep(delay)
    finally:
        if owned:
            await http.aclose()
    raise DecisionError("没有得到可用决策")
