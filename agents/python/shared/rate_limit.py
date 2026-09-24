"""聊天端点的 Redis 滑动窗口限流。

匿名店铺请求也可能触发多次模型调用，因此按用户或 IP 限制请求量。
每个键使用有序集合保存时间戳；Lua 脚本原子清理过期项、计数并在
未超限时记录请求，避免分离读写产生并发穿透。

作为 FastAPI 依赖，与 optional_auth 一同挂到 /api/chat 和
/api/chat/stream；具体限额由配置提供。
"""

from __future__ import annotations

import logging
import time
import uuid

import redis.asyncio as redis
from fastapi import HTTPException, Request

from shared.config import settings

logger = logging.getLogger(__name__)

# 原子检查并记录滑动窗口的 Lua 脚本。
# KEYS[1]：限流键，例如 ratelimit:chat:user:<id>。
# ARGV[1]：当前毫秒时间戳。
# ARGV[2]：窗口毫秒数。
# ARGV[3]：窗口最大请求数。
# ARGV[4]：本请求唯一成员标识，
# 防止同一毫秒到达的请求相互覆盖。
# 清理过期项、统计数量，未超限才新增记录。
# 全部在一次原子操作中完成，
# 避免其他客户端插入检查与记录之间。
_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
local count = redis.call('ZCARD', key)

if count < max_requests then
    redis.call('ZADD', key, now_ms, member)
    redis.call('PEXPIRE', key, window_ms)
    return {1, count + 1}
end

return {0, count}
"""

_redis_client: redis.Redis | None = None


def get_redis_client() -> redis.Redis:
    """按需创建每进程共享的异步 Redis 客户端。"""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


class RateLimitExceededError(Exception):
    """限流检查抛出的异常，由调用方转换为 HTTP 响应。"""

    def __init__(self, retry_after_s: float) -> None:
        self.retry_after_s = retry_after_s
        super().__init__(f"Rate limit exceeded; retry after {retry_after_s:.1f}s")


async def check_rate_limit(
    client: redis.Redis,
    key: str,
    *,
    max_requests: int,
    window_s: float,
) -> None:
    """key 在最近 window_s 内达到 max_requests 时抛出 RateLimitExceededError。"""
    now_ms = time.time() * 1000
    window_ms = window_s * 1000
    member = f"{now_ms}:{uuid.uuid4().hex[:8]}"

    allowed, _count = await client.eval(
        _SLIDING_WINDOW_SCRIPT,
        1,
        key,
        now_ms,
        window_ms,
        max_requests,
        member,
    )
    if not allowed:
        raise RateLimitExceededError(retry_after_s=window_s)


def _client_ip(request: Request) -> str:
    """尽可能获取客户端 IP。

    当前实现信任 X-Forwarded-For，适用于由可信反向代理提供该头的环境。
    生产部署必须核对可信代理链；不能把任意客户端可伪造的请求头当作身份。
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def rate_limit_chat(request: Request) -> None:
    """FastAPI 聊天限流依赖：登录用户按用户标识，匿名请求按 IP。

    RATE_LIMIT_ENABLED=False 时不处理。Redis 不可达时记录并放行，
    避免限流依赖故障导致聊天整体不可用。这与审批记录写入失败时拒绝
    执行的策略不同：容量保护和敏感写操作承担不同风险。
    """
    if not settings.RATE_LIMIT_ENABLED:
        return

    # 身份由前置 optional_auth 或 require_auth 依赖写入请求状态。
    # 路由声明须让身份依赖先执行，
    # 本依赖再直接读取请求状态，
    # 不依赖认证函数返回值的显式传递。
    # 这样限流保持为一个独立依赖，
    # 无需在每个路由里额外传入 user。
    from shared.context import current_user_email

    user_email = current_user_email.get()
    if user_email:
        key = f"ratelimit:chat:user:{user_email}"
    else:
        key = f"ratelimit:chat:ip:{_client_ip(request)}"

    client = get_redis_client()
    try:
        await check_rate_limit(
            client,
            key,
            max_requests=settings.RATE_LIMIT_MAX_REQUESTS,
            window_s=settings.RATE_LIMIT_WINDOW_SECONDS,
        )
    except RateLimitExceededError as exc:
        raise HTTPException(
            status_code=429,
            detail=f"Too many chat requests — try again in about {exc.retry_after_s:.0f} seconds.",
            headers={"Retry-After": str(int(exc.retry_after_s))},
        ) from None
    except HTTPException:
        raise
    except Exception:
        logger.exception("rate_limit.redis_unavailable key=%s — failing open", key)
        return
