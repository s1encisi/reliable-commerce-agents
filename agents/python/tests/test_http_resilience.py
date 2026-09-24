"""有界重试与按主机端口隔离的熔断测试。

替换底层传输的结果序列，无真实网络。时间相关状态机使用短窗口和
真实短暂等待；不关心时间的用例跳过退避等待。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from shared.http_resilience import (
    CircuitBreakerOpenError,
    ResilientAsyncTransport,
    _HostBreaker,
)


def _request(url: str = "http://specialist.local/message:send") -> httpx.Request:
    return httpx.Request("POST", url, json={"message": "hi"}, extensions={"safe_to_retry": True})


def _response(status_code: int, request: httpx.Request) -> httpx.Response:
    return httpx.Response(status_code, request=request, content=b"{}")


def _patch_parent(monkeypatch: pytest.MonkeyPatch, side_effect: list) -> AsyncMock:
    mock = AsyncMock(side_effect=side_effect)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", mock)
    return mock


def _no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """不检查时序的测试跳过实际退避等待。"""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))


# ─────────────────────── Success / no retry ───────────────────────────────


async def test_successful_request_returns_on_first_attempt_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    req = _request()
    mock = _patch_parent(monkeypatch, [_response(200, req)])
    transport = ResilientAsyncTransport()

    resp = await transport.handle_async_request(req)

    assert resp.status_code == 200
    assert mock.call_count == 1


async def test_non_retryable_status_returns_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """404 属于请求问题，不应重试或损伤服务健康统计。"""
    req = _request()
    mock = _patch_parent(monkeypatch, [_response(404, req)])
    transport = ResilientAsyncTransport()

    resp = await transport.handle_async_request(req)

    assert resp.status_code == 404
    assert mock.call_count == 1


# ─────────────────────── Retry behavior ────────────────────────────────────


async def test_retries_connection_error_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_delay(monkeypatch)
    req = _request()
    mock = _patch_parent(
        monkeypatch,
        [httpx.ConnectError("refused", request=req), _response(200, req)],
    )
    transport = ResilientAsyncTransport(max_attempts=3)

    resp = await transport.handle_async_request(req)

    assert resp.status_code == 200
    assert mock.call_count == 2


async def test_retries_retryable_status_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_delay(monkeypatch)
    req = _request()
    mock = _patch_parent(
        monkeypatch,
        [_response(503, req), _response(503, req), _response(200, req)],
    )
    transport = ResilientAsyncTransport(max_attempts=3)

    resp = await transport.handle_async_request(req)

    assert resp.status_code == 200
    assert mock.call_count == 3


async def test_exhausts_retries_on_persistent_connection_error_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_delay(monkeypatch)
    req = _request()
    mock = _patch_parent(
        monkeypatch,
        [
            httpx.ConnectError("refused", request=req),
            httpx.ConnectError("refused", request=req),
            httpx.ConnectError("refused", request=req),
        ],
    )
    transport = ResilientAsyncTransport(max_attempts=3)

    with pytest.raises(httpx.ConnectError):
        await transport.handle_async_request(req)
    assert mock.call_count == 3


async def test_exhausts_retries_on_persistent_retryable_status_and_returns_last_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """持续 503 仍是 HTTP 响应，最终返回它，而非转为连接异常。"""
    _no_delay(monkeypatch)
    req = _request()
    mock = _patch_parent(monkeypatch, [_response(503, req), _response(503, req), _response(503, req)])
    transport = ResilientAsyncTransport(max_attempts=3)

    resp = await transport.handle_async_request(req)

    assert resp.status_code == 503
    assert mock.call_count == 3


async def test_timeout_exception_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_delay(monkeypatch)
    req = _request()
    mock = _patch_parent(
        monkeypatch,
        [httpx.ReadTimeout("slow", request=req), _response(200, req)],
    )
    transport = ResilientAsyncTransport(max_attempts=3)

    resp = await transport.handle_async_request(req)

    assert resp.status_code == 200
    assert mock.call_count == 2


async def test_non_transient_exception_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """不在重试集合的异常应立即传播，例如解码错误。"""
    req = _request()
    mock = _patch_parent(monkeypatch, [httpx.DecodingError("bad content")])
    transport = ResilientAsyncTransport(max_attempts=3)

    with pytest.raises(httpx.DecodingError):
        await transport.handle_async_request(req)
    assert mock.call_count == 1


# ─────────────────────── Backoff delay shape ───────────────────────────────


def test_delay_grows_exponentially_with_jitter_bounds() -> None:
    transport = ResilientAsyncTransport(base_delay_s=0.2, backoff_multiplier=2.0, jitter_fraction=0.2)
    for attempt, expected_base in [(1, 0.2), (2, 0.4), (3, 0.8)]:
        delay = transport._delay_for_attempt(attempt)
        low, high = expected_base * 0.8, expected_base * 1.2
        assert low <= delay <= high, f"attempt {attempt}: {delay} not in [{low}, {high}]"


def test_delay_never_negative_even_with_large_jitter() -> None:
    transport = ResilientAsyncTransport(base_delay_s=0.01, jitter_fraction=5.0)
    for attempt in range(1, 5):
        assert transport._delay_for_attempt(attempt) >= 0.0


# ─────────────────────── Circuit breaker ───────────────────────────────────


async def test_breaker_opens_after_failure_ratio_threshold_and_refuses_without_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_delay(monkeypatch)
    req = _request()
    # 五次连续连接错误达到最小样本数，应打开熔断器。
    mock = _patch_parent(monkeypatch, [httpx.ConnectError("down", request=req) for _ in range(20)])
    transport = ResilientAsyncTransport(max_attempts=1, min_throughput=5, failure_ratio_threshold=0.5)

    for _ in range(5):
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(req)

    calls_before_open = mock.call_count
    assert calls_before_open == 5

    with pytest.raises(CircuitBreakerOpenError):
        await transport.handle_async_request(req)
    assert mock.call_count == calls_before_open, "an open breaker must refuse without attempting the network call"


async def test_breaker_below_min_throughput_never_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_delay(monkeypatch)
    req = _request()
    mock = _patch_parent(monkeypatch, [httpx.ConnectError("down", request=req) for _ in range(10)])
    transport = ResilientAsyncTransport(max_attempts=1, min_throughput=10, failure_ratio_threshold=0.5)

    for _ in range(4):
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(req)

    # 失败数尚未达到最小样本数时，保持关闭。
    assert mock.call_count == 4
    mock.side_effect = [_response(200, req)]
    resp = await transport.handle_async_request(req)
    assert resp.status_code == 200


async def test_breaker_per_host_is_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    bad_req = _request("http://bad-specialist.local/message:send")
    good_req = _request("http://good-specialist.local/message:send")

    call_log: list[str] = []

    async def _router(_self: object, request: httpx.Request, **_kw: object) -> httpx.Response:
        call_log.append(request.url.host)
        if request.url.host == "bad-specialist.local":
            raise httpx.ConnectError("down", request=request)
        return _response(200, request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _router)
    transport = ResilientAsyncTransport(max_attempts=1, min_throughput=5, failure_ratio_threshold=0.5)

    for _ in range(5):
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(bad_req)
    with pytest.raises(CircuitBreakerOpenError):
        await transport.handle_async_request(bad_req)

    # 故障主机不能影响正常主机的熔断状态。
    resp = await transport.handle_async_request(good_req)
    assert resp.status_code == 200


async def test_breaker_is_per_port_not_just_per_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证同主机不同端口的服务隔离。

    五个专业智能体在本地共享 localhost；不能因一个失败就阻止其他端口。
    """
    bad_req = _request("http://localhost:8084/message:send")
    good_req = _request("http://localhost:8085/message:send")

    async def _router(_self: object, request: httpx.Request, **_kw: object) -> httpx.Response:
        if request.url.port == 8084:
            raise httpx.ConnectError("down", request=request)
        return _response(200, request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _router)
    transport = ResilientAsyncTransport(max_attempts=1, min_throughput=5, failure_ratio_threshold=0.5)

    for _ in range(5):
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(bad_req)
    with pytest.raises(CircuitBreakerOpenError):
        await transport.handle_async_request(bad_req)

    # 相同主机、不同端口仍需可访问。
    resp = await transport.handle_async_request(good_req)
    assert resp.status_code == 200


async def test_breaker_half_open_probe_succeeds_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    req = _request()
    mock = _patch_parent(monkeypatch, [httpx.ConnectError("down", request=req) for _ in range(5)])
    transport = ResilientAsyncTransport(
        max_attempts=1,
        min_throughput=5,
        failure_ratio_threshold=0.5,
        sampling_window_s=10.0,
        break_duration_s=0.05,
    )

    for _ in range(5):
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(req)
    with pytest.raises(CircuitBreakerOpenError):
        await transport.handle_async_request(req)

    await asyncio.sleep(0.08)  # 等待冷却时间结束。

    mock.side_effect = [_response(200, req)]
    resp = await transport.handle_async_request(req)
    assert resp.status_code == 200

    # 探测成功后熔断器关闭，
    # 下一调用是普通请求，不再被视为探测。
    mock.side_effect = [_response(200, req)]
    resp2 = await transport.handle_async_request(req)
    assert resp2.status_code == 200


async def test_breaker_half_open_probe_fails_and_reopens(monkeypatch: pytest.MonkeyPatch) -> None:
    req = _request()
    mock = _patch_parent(monkeypatch, [httpx.ConnectError("down", request=req) for _ in range(5)])
    transport = ResilientAsyncTransport(
        max_attempts=1,
        min_throughput=5,
        failure_ratio_threshold=0.5,
        sampling_window_s=10.0,
        break_duration_s=0.05,
    )

    for _ in range(5):
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(req)

    await asyncio.sleep(0.08)

    mock.side_effect = [httpx.ConnectError("still down", request=req)]
    with pytest.raises(httpx.ConnectError):
        await transport.handle_async_request(req)  # 探测请求本身。

    # 探测失败应立即重新打开熔断器。
    with pytest.raises(CircuitBreakerOpenError):
        await transport.handle_async_request(req)


# ─────────────────────── _HostBreaker unit behavior ────────────────────────


def test_host_breaker_prunes_outcomes_outside_the_sampling_window() -> None:
    breaker = _HostBreaker(
        failure_ratio_threshold=0.5,
        min_throughput=2,
        sampling_window_s=0.05,
        break_duration_s=1.0,
    )
    breaker.record_failure()
    breaker.record_failure()
    assert breaker._open_until is not None  # 两次请求均失败且达到最小请求数，打开熔断器。

    # 窗口推进后旧失败应被清理，
    # 通过再次记录失败触发内部清理，
    # 并断言队列随时间缩小。
    import time

    time.sleep(0.08)
    breaker._prune(time.monotonic())
    assert len(breaker._outcomes) == 0
