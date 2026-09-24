"""仅使用标准库的 Jev System One API 客户端。

请求 POST https://api.typesafe.ai/v1/systemone，并使用 Bearer 认证。
评测在隔离子进程和固定依赖集运行，因此不为一次 HTTP 调用增加依赖。

choice 选择标签，score 返回等级，noul 返回是概率；各访问器检查返回
形态，不依赖自然语言解析。数值校准效果仍需通过实际数据验证。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-1.13.0"
ENV_API_KEY = "TYPESAFE_API_KEY"

# 默认调用节奏保守，避免接近服务限额。
# 评测框架有意顺序执行，
# 避免自身排队污染延迟样本；实际限额以服务配置为准。
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF = 0.6


class JevError(RuntimeError):
    """本模块异常的基类。"""


class JevAuthError(JevError):
    """凭据缺失或被拒绝；不重试，因为重试无法解决身份问题。"""


class JevRateLimitError(JevError):
    """HTTP 429，按退避策略重试。"""


class JevUnavailableError(JevError):
    """HTTP 5xx 或传输失败，按退避策略重试。"""


# --------------------------------------------------------------------------
# 问题构造器
#
# 让调用代码表达所需决策，
# 避免到处手写 JSON；包装保持轻量。
# --------------------------------------------------------------------------


def choice(instructions: str, criteria: Mapping[str, str]) -> dict[str, Any]:
    """从 criteria 中选择一个选项，最多支持 255 个标签。"""
    if not criteria:
        raise ValueError("choice requires at least one criterion")
    return {
        "type": "choice",
        "instructions": instructions,
        "criteria": dict(criteria),
    }


def score(instructions: str, criteria: Sequence[str]) -> dict[str, Any]:
    """将输入放到含 2–10 个描述等级的有序量表上。"""
    levels = list(criteria)
    if not 2 <= len(levels) <= 10:
        raise ValueError(f"score requires 2–10 levels, got {len(levels)}")
    return {
        "type": "score",
        "instructions": instructions,
        "criteria": levels,
    }


def noul(instructions: str) -> dict[str, Any]:
    """二元判断，返回 [0, 1] 范围内的是概率。"""
    return {"type": "noul", "instructions": instructions}


# --------------------------------------------------------------------------
# 响应对象
# --------------------------------------------------------------------------


@dataclass
class JevResponse:
    """一次往返的类型化答案及用量信息。"""

    model: str
    answers: dict[str, dict[str, Any]]
    input_tokens: int
    output_tokens: int
    latency_ms: float
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # 类型化访问器
    # 答案缺失时抛错，不静默返回默认值。
    # 这通常表示请求和代码对问题名称的理解不同，
    # 应立即暴露而非继续执行。

    def choice_of(self, name: str) -> tuple[str, float]:
        """返回（选中键，置信度）。"""
        ans = self._answer(name, "choice")
        return ans["choice"], float(ans.get("confidence", 0.0))

    def probabilities_of(self, name: str) -> dict[str, float]:
        ans = self._answer(name, "choice")
        return {k: float(v) for k, v in (ans.get("probabilities") or {}).items()}

    def score_of(self, name: str) -> tuple[float, float]:
        """返回声明量表上的（分数，置信度）。"""
        ans = self._answer(name, "score")
        return float(ans["score"]), float(ans.get("confidence", 0.0))

    def noul_of(self, name: str) -> float:
        """返回 [0, 1] 范围内的是概率。"""
        ans = self._answer(name, "noul")
        return float(ans["noul"])

    def _answer(self, name: str, expected_type: str) -> dict[str, Any]:
        if name not in self.answers:
            raise JevError(f"no answer for {name!r}; got {sorted(self.answers)}")
        ans = self.answers[name]
        got = ans.get("type")
        if got != expected_type:
            raise JevError(f"answer {name!r} is type {got!r}, expected {expected_type!r}")
        return ans

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# --------------------------------------------------------------------------
# 客户端
# --------------------------------------------------------------------------


class JevClient:
    """System One 的轻量同步客户端，带重试。

    调用之间不保留状态、不缓存结果或跨样本复用连接，
    便于评测将每个样本视为独立测量。
    """

    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff: float = DEFAULT_BACKOFF,
    ) -> None:
        resolved = api_key or os.environ.get(ENV_API_KEY, "")
        if not resolved:
            raise JevAuthError(f"no API key: pass api_key= or set ${ENV_API_KEY}")
        self._api_key = resolved
        self._endpoint = endpoint
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff

    @property
    def model(self) -> str:
        return self._model

    def ask(
        self,
        state: str | Mapping[str, Any] | Sequence[Any],
        questions: Mapping[str, dict[str, Any]],
    ) -> JevResponse:
        """一次往返评估全部问题。

        问题在服务端批量处理并共享 state；批量与多次独立调用的
        实际延迟和费用差异应以返回用量及测量结果为准。
        """
        if not questions:
            raise ValueError("at least one question is required")

        payload = {
            "model": self._model,
            "state": state,
            "questions": dict(questions),
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        started = time.perf_counter()
        raw = self._post_with_retries(body)
        latency_ms = (time.perf_counter() - started) * 1000.0

        usage = raw.get("usage") or {}
        return JevResponse(
            model=raw.get("model", self._model),
            answers=raw.get("answers") or {},
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            latency_ms=latency_ms,
            raw=raw,
        )

    # 传输实现

    def _post_with_retries(self, body: bytes) -> dict[str, Any]:
        last_error: Exception | None = None

        from urllib.parse import urlparse

        from shared.paid_transport import configured_budget, configured_price, current_root_run, usage_counts

        if urlparse(self._endpoint).hostname != "api.typesafe.ai" or not self._endpoint.startswith("https://"):
            raise JevError("仅允许已授权的官方 Jev 端点")
        budget, price = configured_budget(), configured_price("jev")
        ceiling = int(price.cost(len(body) * 2 + 2048, 0) * 1.15) + 1
        for attempt in range(self._max_retries + 1):
            price.assert_current()
            receipt = budget.reserve(
                "jev",
                self._model,
                ceiling,
                currency=price.currency,
                purpose="evaluation",
                price_version=price.version,
                run_id=current_root_run.get(),
            )
            request = urllib.request.Request(
                self._endpoint,
                data=body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    counts = usage_counts(result)
                    usage = {"input_tokens": counts[0], "output_tokens": counts[1]} if counts is not None else None
                    budget.settle(receipt, price.cost(*counts) if counts is not None else None, usage)
                    return result

            except urllib.error.HTTPError as exc:
                budget.settle(receipt, None)
                detail = _read_error_body(exc).replace(self._api_key, "[credential]")
                if exc.code in {400, 401, 402, 403, 404, 422}:
                    budget.halt("jev")
                # 除 429 外的 4xx 通常是请求错误，
                # 原样重试只会继续消耗配额。
                if exc.code == 429:
                    last_error = JevRateLimitError(f"429 rate limited: {detail}")
                elif 500 <= exc.code < 600:
                    last_error = JevUnavailableError(f"{exc.code}: {detail}")
                elif exc.code in (401, 403):
                    raise JevAuthError(f"{exc.code} rejected the API key: {detail}")
                else:
                    raise JevError(f"{exc.code}: {detail}")

            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                budget.settle(receipt, None)
                last_error = JevUnavailableError(f"transport failure: {exc}")

            if attempt < self._max_retries:
                time.sleep(self._backoff * (2**attempt))

        raise last_error or JevError("exhausted retries")


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")[:500]
    except Exception:  # noqa: BLE001 - error reporting must never itself raise
        return "<unreadable body>"
