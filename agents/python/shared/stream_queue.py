"""SSE 生产者与消费者之间的有界队列，同时限制帧数与字节数。"""

import asyncio
import json
from typing import Any


class BoundedStreamQueue(asyncio.Queue):
    def __init__(self, maxsize: int = 128, max_bytes: int = 1_048_576) -> None:
        if maxsize <= 0 or max_bytes <= 0:
            raise ValueError("流式缓冲上限必须大于零")
        super().__init__(maxsize=maxsize)
        self.max_bytes = max_bytes
        self.buffered_bytes = 0
        self._space = asyncio.Event()
        self._space.set()

    def _size(self, item: Any) -> int:
        size = len(json.dumps(item, ensure_ascii=False, default=str).encode())
        if size > self.max_bytes:
            raise ValueError("单帧超过流式缓冲上限")
        return size

    async def put(self, item: Any) -> None:
        size = self._size(item)
        while self.full() or self.buffered_bytes + size > self.max_bytes:
            self._space.clear()
            await self._space.wait()
        self.put_nowait(item)

    def put_nowait(self, item: Any) -> None:
        size = self._size(item)
        if self.buffered_bytes + size > self.max_bytes:
            raise asyncio.QueueFull
        super().put_nowait((item, size))
        self.buffered_bytes += size

    def _get(self) -> Any:
        item, size = super()._get()
        self.buffered_bytes -= size
        self._space.set()
        return item
