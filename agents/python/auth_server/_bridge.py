"""为 authlib 回调提供的「异步之上跑同步」桥接。

authlib 的 OAuth2 核心（``AuthorizationServer``、各 grant 类、client/token
mixin）完全是同步的——没有 Starlette/FastAPI 集成，也没有异步支持。本仓库
的数据库访问（asyncpg）则完全是异步的。与其仅为这一个服务再引入一个同步的
Postgres 驱动，不如让令牌端点在 worker 线程中运行 authlib 的同步调用链
（经 ``asyncio.to_thread``），而它任何需要数据库的回调都把协程经
``run_coroutine_threadsafe`` 提交回主事件循环——也就是持有 asyncpg 连接池
的那个循环——并在等待期间只阻塞 worker 线程。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

_main_loop: asyncio.AbstractEventLoop | None = None


def bind_main_loop() -> None:
    """捕获正在运行的事件循环。在应用启动时调用一次。"""
    global _main_loop
    _main_loop = asyncio.get_running_loop()


def run_coro_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """从同步代码运行一个异步协程并阻塞等待结果。

    从 worker 线程调用是安全的（例如在 ``asyncio.to_thread`` 内部）；
    若从主循环自己的线程调用则会抛错，因为那会等待自己而死锁。
    """
    if _main_loop is None:
        raise RuntimeError("bind_main_loop() was not called during startup")
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("run_coro_sync() must not be called from the main event loop's thread")
    return asyncio.run_coroutine_threadsafe(coro, _main_loop).result()
