"""Jev (TypeSafe System One) API client — standard library only.

Wraps the single System One endpoint:

    POST https://api.typesafe.ai/v1/systemone
    Authorization: Bearer <TYPESAFE_API_KEY>

Why hand-rolled instead of a dependency: this module is consumed by the eval
harness, which runs in an isolated subprocess against a pinned dependency set.
Pulling in ``httpx``/``requests`` for one POST would widen that surface for no
gain.

Jev returns *typed* decisions rather than prose, which is the whole point for
this project — the values come back already shaped like the branch the caller
wants to write:

    choice -> pick one labelled option          (routing, triage)
    score  -> place the input on an ordered scale (relevance, risk)
    noul   -> calibrated yes/no probability       (gates, guardrails)

Because the types are fixed there is no parsing, no regex, and no
response-format drift to defend against — see the guards in
``JevResponse.choice_of`` / ``score_of`` / ``noul_of``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
ENV_API_KEY = "TYPESAFE_API_KEY"

# Documented limits: 250k tokens/second, 1200 requests/minute. We stay well
# under both by default; the harness is sequential on purpose so that latency
# samples are not contaminated by self-inflicted queueing.
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF = 0.6


class JevError(RuntimeError):
    """Base class for every failure raised by this module."""


class JevAuthError(JevError):
    """Missing or rejected credentials. Never retried — retrying cannot help."""


class JevRateLimitError(JevError):
    """HTTP 429. Retried with backoff."""


class JevUnavailableError(JevError):
    """HTTP 5xx or a transport failure. Retried with backoff."""


# --------------------------------------------------------------------------
# Question builders
#
# These exist so call sites read like the decision they are asking for, rather
# than like a JSON payload. They are deliberately thin.
# --------------------------------------------------------------------------


def choice(instructions: str, criteria: Mapping[str, str]) -> dict[str, Any]:
    """Pick exactly one of ``criteria`` (up to 255 labelled options)."""
    if not criteria:
        raise ValueError("choice requires at least one criterion")
    return {
        "type": "choice",
        "instructions": instructions,
        "criteria": dict(criteria),
    }


def score(instructions: str, criteria: Sequence[str]) -> dict[str, Any]:
    """Place the input on an ordered scale of 2–10 described levels."""
    levels = list(criteria)
    if not 2 <= len(levels) <= 10:
        raise ValueError(f"score requires 2–10 levels, got {len(levels)}")
    return {
        "type": "score",
        "instructions": instructions,
        "criteria": levels,
    }


def noul(instructions: str) -> dict[str, Any]:
    """A calibrated yes/no, returned as a probability in [0, 1]."""
    return {"type": "noul", "instructions": instructions}


# --------------------------------------------------------------------------
# Response
# --------------------------------------------------------------------------


@dataclass
class JevResponse:
    """One round trip's worth of typed answers, plus what it cost."""

    model: str
    answers: dict[str, dict[str, Any]]
    input_tokens: int
    output_tokens: int
    latency_ms: float
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # -- typed accessors ---------------------------------------------------
    # Each raises rather than silently returning a default: a missing answer
    # means the request and the code disagree about the question names, which
    # is a bug worth surfacing immediately.

    def choice_of(self, name: str) -> tuple[str, float]:
        """Return ``(winning_key, confidence)``."""
        ans = self._answer(name, "choice")
        return ans["choice"], float(ans.get("confidence", 0.0))

    def probabilities_of(self, name: str) -> dict[str, float]:
        ans = self._answer(name, "choice")
        return {k: float(v) for k, v in (ans.get("probabilities") or {}).items()}

    def score_of(self, name: str) -> tuple[float, float]:
        """Return ``(score, confidence)`` on the declared scale."""
        ans = self._answer(name, "score")
        return float(ans["score"]), float(ans.get("confidence", 0.0))

    def noul_of(self, name: str) -> float:
        """Return the yes-probability in [0, 1]."""
        ans = self._answer(name, "noul")
        return float(ans["noul"])

    def _answer(self, name: str, expected_type: str) -> dict[str, Any]:
        if name not in self.answers:
            raise JevError(
                f"no answer for {name!r}; got {sorted(self.answers)}"
            )
        ans = self.answers[name]
        got = ans.get("type")
        if got != expected_type:
            raise JevError(
                f"answer {name!r} is type {got!r}, expected {expected_type!r}"
            )
        return ans

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class JevClient:
    """Thin, synchronous, retrying wrapper over the System One endpoint.

    The client is intentionally stateless between calls: the eval harness
    wants each sample to be an independent measurement, so nothing is cached
    and no connection is pooled across samples.
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
            raise JevAuthError(
                f"no API key: pass api_key= or set ${ENV_API_KEY}"
            )
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
        """Evaluate every question in one round trip.

        Batching matters: questions run in parallel server-side and share the
        ``state`` cost, so asking three things in one call is cheaper and
        lower-latency than three calls.
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

    # -- transport ---------------------------------------------------------

    def _post_with_retries(self, body: bytes) -> dict[str, Any]:
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
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
                    return json.loads(resp.read().decode("utf-8"))

            except urllib.error.HTTPError as exc:
                detail = _read_error_body(exc)
                # 4xx other than 429 is a bug in the request, not a transient
                # condition — retrying just burns quota.
                if exc.code == 429:
                    last_error = JevRateLimitError(f"429 rate limited: {detail}")
                elif 500 <= exc.code < 600:
                    last_error = JevUnavailableError(f"{exc.code}: {detail}")
                elif exc.code in (401, 403):
                    raise JevAuthError(f"{exc.code} rejected the API key: {detail}")
                else:
                    raise JevError(f"{exc.code}: {detail}")

            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = JevUnavailableError(f"transport failure: {exc}")

            if attempt < self._max_retries:
                time.sleep(self._backoff * (2**attempt))

        raise last_error or JevError("exhausted retries")


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")[:500]
    except Exception:  # noqa: BLE001 - error reporting must never itself raise
        return "<unreadable body>"
