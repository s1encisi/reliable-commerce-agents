"""asyncpg 连接池管理。"""

from __future__ import annotations

import asyncpg

from shared.config import settings

_pool: asyncpg.Pool | None = None


async def init_db_pool() -> None:
    """在智能体生命周期启动阶段初始化连接池。"""
    global _pool
    _pool = await asyncpg.create_pool(
        settings.DATABASE_URL,
        min_size=5,
        max_size=20,
    )


def get_pool() -> asyncpg.Pool:
    """获取连接池；尚未初始化时抛错。"""
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_db_pool() first")
    return _pool


async def close_db_pool() -> None:
    """在智能体生命周期结束时关闭连接池。"""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
