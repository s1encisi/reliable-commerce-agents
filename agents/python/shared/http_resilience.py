"""为出站 A2A 调用提供有界重试和按主机、端口隔离的熔断器。

编排器的 call_specialist_agent 与 RemoteSpecialistChatClient 共用此传输层。
专业智能体出现短暂故障时，带抖动的指数退避减少瞬时失败；滚动窗口内
失败率过高时，熔断器暂时拒绝新请求，避免继续压垮目标服务。

实现保留在本模块，便于完整阅读重试和熔断机制；教程第 23 章解释同一思路。
调用方只需给 httpx.AsyncClient 指定 transport，不必修改请求逻辑：

    async with httpx.AsyncClient(timeout=30, transport=ResilientAsyncTransport()) as client:
        resp = await client.post(url, json=body)

SSE 流式调用只在建立连接、接收响应头阶段重试。一旦调用方开始消费
响应体，就不再重新发送请求，以免重复向浏览器输出已有分块。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque

import httpx

logger = logging.getLogger(__name__)

# 有界重试：最多尝试 3 次，基础延迟 200 毫秒，采用带抖动的指数退避。
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_S = 0.2
DEFAULT_BACKOFF_MULTIPLIER = 2.0
DEFAULT_JITTER_FRACTION = 0.2

# 仅重试请求超时、限流和服务端错误，
# 即 408、429 与 5xx。其他 4xx 通常是请求自身错误，
# 原样重发仍会失败，
# 因此直接返回。
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

# 熔断器按滚动窗口计算失败率，
# 默认阈值 50%、最小请求数 5、采样窗口 30 秒，
# 打开后冷却 30 秒。
DEFAULT_FAILURE_RATIO_THRESHOLD = 0.5
DEFAULT_MIN_THROUGHPUT = 5
DEFAULT_SAMPLING_WINDOW_S = 30.0
DEFAULT_BREAK_DURATION_S = 30.0


class CircuitBreakerOpenError(httpx.TransportError):
    """目标主机处于熔断状态时抛出，不发起网络请求。

    超时表示已经尝试但不可达或过慢；此异常表示依据近期失败记录主动
    拒绝尝试，减轻本进程和目标服务的压力。
    """


class _HostBreaker:
    """按主机维护滚动失败率，并支持半开探测的熔断器。

    关闭状态正常放行并记录结果；打开状态立即拒绝请求。冷却结束后
    仅放行一个半开探测：成功则关闭并清空窗口，失败则重新打开一个周期。
    状态由现有字段推导，不另设枚举。
    """

    def __init__(
        self,
        *,
        failure_ratio_threshold: float,
        min_throughput: int,
        sampling_window_s: float,
        break_duration_s: float,
    ) -> None:
        self._failure_ratio_threshold = failure_ratio_threshold
        self._min_throughput = min_throughput
        self._sampling_window_s = sampling_window_s
        self._break_duration_s = break_duration_s
        self._outcomes: deque[tuple[float, bool]] = deque()  # 记录（单调时钟时间，是否成功）。
        self._open_until: float | None = None
        self._half_open_probe_in_flight = False

    def _prune(self, now: float) -> None:
        cutoff = now - self._sampling_window_s
        while self._outcomes and self._outcomes[0][0] < cutoff:
            self._outcomes.popleft()

    def allow_request(self) -> bool:
        now = time.monotonic()
        if self._open_until is None:
            return True
        if now < self._open_until:
            return False
        if self._half_open_probe_in_flight:
            # 冷却结束后若已有探测在途，继续拒绝其他请求。
            # 等待该探测成功或失败后再决定是否放行，
            # 避免并发请求全部变成探测。
            return False
        self._half_open_probe_in_flight = True
        return True

    def record_success(self) -> None:
        now = time.monotonic()
        self._prune(now)
        self._outcomes.append((now, True))
        self._open_until = None
        self._half_open_probe_in_flight = False

    def record_failure(self) -> None:
        now = time.monotonic()
        self._prune(now)
        self._outcomes.append((now, False))
        self._half_open_probe_in_flight = False

        total = len(self._outcomes)
        if total < self._min_throughput:
            return
        failures = sum(1 for _, ok in self._outcomes if not ok)
        ratio = failures / total
        if ratio >= self._failure_ratio_threshold:
            self._open_until = now + self._break_duration_s
            logger.warning(
                "http_resilience.circuit_open failure_ratio=%.2f sample_size=%d break_s=%.2f",
                ratio,
                total,
                self._break_duration_s,
            )


class ResilientAsyncTransport(httpx.AsyncHTTPTransport):
    """为 httpx 增加有界重试及按主机、端口隔离的熔断器。

    默认参数见模块常量；具体调用可通过构造函数覆盖。
    """

    def __init__(
        self,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_delay_s: float = DEFAULT_BASE_DELAY_S,
        backoff_multiplier: float = DEFAULT_BACKOFF_MULTIPLIER,
        jitter_fraction: float = DEFAULT_JITTER_FRACTION,
        failure_ratio_threshold: float = DEFAULT_FAILURE_RATIO_THRESHOLD,
        min_throughput: int = DEFAULT_MIN_THROUGHPUT,
        sampling_window_s: float = DEFAULT_SAMPLING_WINDOW_S,
        break_duration_s: float = DEFAULT_BREAK_DURATION_S,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._max_attempts = max_attempts
        self._base_delay_s = base_delay_s
        self._backoff_multiplier = backoff_multiplier
        self._jitter_fraction = jitter_fraction
        self._breakers: dict[str, _HostBreaker] = {}
        self._breaker_kwargs = {
            "failure_ratio_threshold": failure_ratio_threshold,
            "min_throughput": min_throughput,
            "sampling_window_s": sampling_window_s,
            "break_duration_s": break_duration_s,
        }

    def _breaker_for(self, authority: str) -> _HostBreaker:
        """每个主机和端口组合使用独立熔断器。

        本地多个专业智能体共享 localhost，但端口为 8081–8085；若仅按
        主机隔离，一个服务故障会使全部服务被熔断。Docker 中主机名不同，
        因此这种错误主要在本地和评测环境暴露。
        """
        breaker = self._breakers.get(authority)
        if breaker is None:
            breaker = _HostBreaker(**self._breaker_kwargs)
            self._breakers[authority] = breaker
        return breaker

    def _delay_for_attempt(self, attempt: int) -> float:
        """指数退避：第一次使用基础延迟，并叠加正负 jitter_fraction 抖动。"""
        base = self._base_delay_s * (self._backoff_multiplier ** (attempt - 1))
        jitter = base * self._jitter_fraction
        return max(0.0, base + random.uniform(-jitter, jitter))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.netloc.decode("ascii", "replace")
        breaker = self._breaker_for(host)

        if not breaker.allow_request():
            raise CircuitBreakerOpenError(
                f"Circuit open for {host} — too many recent failures, refusing to attempt this call.",
                request=request,
            )

        safe = request.method in {"GET", "HEAD", "OPTIONS"} or request.extensions.get("safe_to_retry") is True
        safe = safe or request.headers.get("x-execution-policy") == "read_only"
        max_attempts = self._max_attempts if safe else 1
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = await super().handle_async_request(request)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                breaker.record_failure()
                if attempt == max_attempts:
                    raise
                delay = self._delay_for_attempt(attempt)
                logger.warning(
                    "http_resilience.retry host=%s attempt=%d/%d reason=%s delay_s=%.2f",
                    host,
                    attempt,
                    self._max_attempts,
                    type(exc).__name__,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            if response.status_code not in RETRYABLE_STATUS_CODES:
                breaker.record_success()
                return response

            breaker.record_failure()
            if attempt == max_attempts:
                return response

            await response.aclose()
            delay = self._delay_for_attempt(attempt)
            logger.warning(
                "http_resilience.retry host=%s attempt=%d/%d reason=status_%d delay_s=%.2f",
                host,
                attempt,
                self._max_attempts,
                response.status_code,
                delay,
            )
            await asyncio.sleep(delay)

        # max_attempts 至少为 1 时，所有分支都会在最后一次返回或抛错。
        # 此处仅保留防御性兜底。
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("ResilientAsyncTransport: exhausted retries with no captured exception")
