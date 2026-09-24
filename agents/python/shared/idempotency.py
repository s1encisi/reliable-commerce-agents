"""数据库写操作的通用幂等键机制。

并发锁阻止同时重复提交，幂等缓存则让成功后的顺序重试重放原结果。
协议使用 idempotency_keys 表：先 INSERT ON CONFLICT 预留；已有
completed 记录则重放，年轻的 in_progress 记录则报冲突，超过
_STALE_AFTER 的预留按现有规则尝试接管。成功后缓存结果，异常时
释放预留，允许后续重试。

调用方保持 JSON 字典结果契约。退货可靠执行另由 after_sales 的原子
操作记录处理；超时接管并不能证明旧执行者已经停止。
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, ParamSpec, TypeVar

logger = logging.getLogger(__name__)

# 超过此时长的 in_progress 预留按现有规则视为遗留记录，
# 可能来自进程中断或结果未写入。
# 这是一种恢复策略，不是旧进程必然停止的证据。
_STALE_AFTER = timedelta(seconds=60)

_CONFLICT_MESSAGE = "A request for this action is already being processed. Please wait a moment and try again."

P = ParamSpec("P")
R = TypeVar("R", bound=dict)


def _canonical_key(scope: str, identity: str, bound_args: dict[str, Any]) -> str:
    canonical = json.dumps(bound_args, sort_keys=True, default=str)
    digest = hashlib.sha256(f"{identity}:{scope}:{canonical}".encode()).hexdigest()
    return f"{scope}:{digest}"


def _bound_args(fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """将位置参数和关键字参数归一为相同的名称到值映射。"""
    sig = inspect.signature(fn)
    bound = sig.bind(*args, **kwargs)
    bound.apply_defaults()
    return dict(bound.arguments)


async def _reserve(pool: Any, key: str, scope: str) -> tuple[bool, dict[str, Any] | None]:
    """尝试认领 key，返回（是否预留，冲突或缓存结果）。"""
    row = await pool.fetchrow(
        """INSERT INTO idempotency_keys (key, scope, status)
           VALUES ($1, $2, 'in_progress')
           ON CONFLICT (key) DO NOTHING
           RETURNING key""",
        key,
        scope,
    )
    if row is not None:
        return True, None

    existing = await pool.fetchrow(
        "SELECT status, result, created_at FROM idempotency_keys WHERE key = $1",
        key,
    )
    if existing is None:
        # 若并发失败请求恰好释放了该键，
        # 再次尝试预留一次。
        return await _reserve(pool, key, scope)

    if existing["status"] == "completed":
        # 未注册编解码器时，asyncpg 的 JSONB 返回原始 JSON 文本。
        # 必须显式解码；直接 dict(...) 会按字符迭代，
        # 无法得到预期字典，
        # 还会产生难以理解的 ValueError。
        # 处理方式与 hitl.py 的 _decode_jsonb 一致。
        raw = existing["result"]
        cached = json.loads(raw) if isinstance(raw, str) else (dict(raw) if raw else {})
        logger.info("idempotency.replay scope=%s key=%s", scope, key)
        return False, cached or {}

    age = datetime.now(UTC) - existing["created_at"]
    if age > _STALE_AFTER:
        taken = await pool.fetchrow(
            """UPDATE idempotency_keys SET created_at = NOW()
               WHERE key = $1 AND status = 'in_progress' AND created_at = $2
               RETURNING key""",
            key,
            existing["created_at"],
        )
        if taken is not None:
            logger.warning("idempotency.reclaimed_stale scope=%s key=%s age_s=%.1f", scope, key, age.total_seconds())
            return True, None
        # 若查询后、更新前被其他调用方接管或完成，
        # 返回冲突，
        # 后续重试再读取最新状态。

    logger.info("idempotency.conflict scope=%s key=%s status=%s", scope, key, existing["status"])
    return False, {"error": _CONFLICT_MESSAGE}


async def _complete(pool: Any, key: str, result: dict[str, Any]) -> None:
    await pool.execute(
        "UPDATE idempotency_keys SET status = 'completed', result = $2::jsonb, completed_at = NOW() WHERE key = $1",
        key,
        json.dumps(result),
    )


async def _release(pool: Any, key: str) -> None:
    """包装调用抛错后释放预留，避免永久阻止合法重试。"""
    await pool.execute(
        "DELETE FROM idempotency_keys WHERE key = $1 AND status = 'in_progress'",
        key,
    )


def idempotent(
    scope: str,
    *,
    identity_fn: Callable[..., str] | None = None,
    cache_result: Callable[[dict[str, Any]], bool] | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """按身份、范围和参数为返回字典的异步写函数提供幂等包装。

    identity_fn 接收原调用参数并返回稳定身份，默认读取 current_user_email。
    所有执行路径必须返回字典，保持工具与审批执行接口的现有约定。
    """

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            from shared.context import current_user_email

            identity = identity_fn(*args, **kwargs) if identity_fn else current_user_email.get()
            if not identity:
                # 没有稳定身份时无法构造隔离的幂等键。
                # 现有调用方在函数体内已有缺少用户上下文的守卫，
                # 让该守卫返回正常错误，
                # 而非在前置条件校验之前
                # 抢先访问数据库连接池。
                # 当前项目也没有合法的匿名资金写入路径，
                # 因此这里不为匿名请求建立缓存。
                return await fn(*args, **kwargs)

            from shared.db import get_pool

            key = _canonical_key(scope, identity, _bound_args(fn, args, kwargs))
            pool = get_pool()
            reserved, conflict_or_cached = await _reserve(pool, key, scope)
            if not reserved:
                return conflict_or_cached  # type: ignore[return-value]

            try:
                result = await fn(*args, **kwargs)
            except Exception:
                await _release(pool, key)
                raise

            if isinstance(result, dict) and cache_result is not None and not cache_result(result):
                # 某些业务结果重试时需要重新获取证据或审批。
                # 由调用方显式选择；其他工具保持原有协议。
                await _release(pool, key)
            elif isinstance(result, dict):
                await _complete(pool, key, result)
            else:
                # 当前调用方都返回字典；非字典结果不能按相同方式重放。
                # 使用空缓存结束预留，
                # 避免一直保持处理中；
                # 后续重试按现有逻辑重新执行。
                await _complete(pool, key, {})
            return result

        return wrapper

    return decorator
