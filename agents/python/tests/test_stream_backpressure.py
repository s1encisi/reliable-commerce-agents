"""聊天流背压守卫测试。

在隔离的生成器逻辑中覆盖断连、墙钟超时和字节上限，无外部调用；
真实路由另有集成测试。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest


class _FakeRequest:
    """模拟 Starlette 请求的断连查询接口。"""

    def __init__(self, disconnect_after_chunks: int | None = None) -> None:
        self._disconnect_after = disconnect_after_chunks
        self._chunks_seen = 0

    async def is_disconnected(self) -> bool:
        if self._disconnect_after is None:
            return False
        return self._chunks_seen >= self._disconnect_after

    def saw_chunk(self) -> None:
        self._chunks_seen += 1


async def _run_generator(
    chunks: AsyncIterator[str],
    request: _FakeRequest,
    *,
    timeout_s: float,
    max_bytes: int,
) -> tuple[list[str], list[str]]:
    """复现事件生成器的守卫逻辑，省略数据库和智能体接线，
    返回已输出内容和累计响应。
    """
    full_response: list[str] = []
    yields: list[str] = []
    full_bytes = 0
    truncated = False
    deadline = time.monotonic() + timeout_s

    async for chunk in chunks:
        if await request.is_disconnected():
            break
        if time.monotonic() > deadline:
            timeout_msg = " [stream timed out — the agent took too long; please retry]"
            full_response.append(timeout_msg)
            yields.append(timeout_msg)
            break
        if not truncated:
            cb = len(chunk.encode("utf-8"))
            if full_bytes + cb > max_bytes:
                truncated = True
                marker = " [response truncated at limit]"
                full_response.append(marker)
                yields.append(marker)
                request.saw_chunk()
                continue
            full_bytes += cb
            full_response.append(chunk)
            yields.append(chunk)
        request.saw_chunk()

    return yields, full_response


# ─────────────────────── Cases ───────────────────────


@pytest.mark.asyncio
async def test_full_stream_passes_through_when_under_limits() -> None:
    async def chunks():
        for word in ("hello ", "there ", "world"):
            yield word

    yields, full = await _run_generator(chunks(), _FakeRequest(), timeout_s=10, max_bytes=1_000_000)
    assert yields == ["hello ", "there ", "world"]
    assert "".join(full) == "hello there world"


@pytest.mark.asyncio
async def test_client_disconnect_aborts_mid_stream() -> None:
    async def chunks():
        for i in range(20):
            yield f"chunk{i} "

    request = _FakeRequest(disconnect_after_chunks=3)
    yields, _ = await _run_generator(chunks(), request, timeout_s=10, max_bytes=1_000_000)
    # 输出三个分块后，第四轮检测到断连，
    # 循环随即结束。
    assert len(yields) == 3
    assert yields == ["chunk0 ", "chunk1 ", "chunk2 "]


@pytest.mark.asyncio
async def test_wall_clock_timeout_stops_runaway_stream() -> None:
    async def chunks():
        # 每块等待 50 毫秒，0.12 秒超时，
        # 通常应在两块后触发。
        for i in range(20):
            await asyncio.sleep(0.05)
            yield f"chunk{i}"

    yields, full = await _run_generator(chunks(), _FakeRequest(), timeout_s=0.12, max_bytes=1_000_000)
    assert any("stream timed out" in y for y in yields)
    # 考虑循环调度开销，超时标记前最多三块。
    real = [y for y in yields if "stream timed out" not in y]
    assert len(real) <= 3


@pytest.mark.asyncio
async def test_max_bytes_truncates_with_marker() -> None:
    async def chunks():
        for _ in range(100):
            yield "x" * 1024  # 每块 1 KiB。

    yields, _ = await _run_generator(
        chunks(),
        _FakeRequest(),
        timeout_s=10,
        max_bytes=4 * 1024,  # 总上限 4 KiB。
    )
    real = [y for y in yields if "truncated" not in y]
    assert sum(len(y) for y in real) <= 4 * 1024
    assert any("truncated" in y for y in yields)
