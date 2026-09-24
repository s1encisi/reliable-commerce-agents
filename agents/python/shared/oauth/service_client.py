"""自托管授权服务器令牌端点的 HTTP 客户端。

request_token 供编排器代理用户登录和刷新；acquire_service_token
供智能体间及 MCP 客户端凭据调用使用。
"""

from __future__ import annotations

import asyncio
import time

import httpx

from shared.config import settings
from shared.context import current_session_id, current_user_email, current_user_role
from shared.oauth.client_secrets import derive_client_secret


def _client_id() -> str:
    return settings.OAUTH_CLIENT_ID or "orchestrator"


def _client_secret() -> str:
    return settings.OAUTH_CLIENT_SECRET or derive_client_secret(settings.OAUTH_SEED_KEY, _client_id())


async def request_token(grant_type: str, **form: str) -> dict:
    """以当前服务的客户端身份请求令牌端点。

    非 2xx 响应抛出 httpx.HTTPStatusError，由调用方转换为用户错误。
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            settings.AUTH_SERVER_TOKEN_URL,
            data={"grant_type": grant_type, **form},
            auth=(_client_id(), _client_secret()),
        )
        response.raise_for_status()
        return response.json()


# 智能体间与 MCP 客户端凭据令牌获取。

_REFRESH_SKEW_SECONDS = 30

# 缓存键为 (scope, audience)，audience 不直接发送给授权服务器。
# 令牌端点根据 scope 推导受众，
# 映射见 auth_server/token.py；audience 只参与缓存键，
# 避免相同范围、不同资源的调用
# 错误共用缓存。
_service_token_cache: dict[tuple[str, str], tuple[str, float]] = {}
_service_token_lock = asyncio.Lock()


async def acquire_service_token(scope: str, audience: str) -> str:
    """通过客户端凭据授权获取令牌，按 (scope, audience) 缓存。

    过期前 _REFRESH_SKEW_SECONDS 提前刷新，减少调用途中失效风险。
    使用单调时钟抵御系统时间调整，并使用进程级锁合并并发刷新。
    """
    cache_key = (scope, audience)
    cached = _service_token_cache.get(cache_key)
    if cached is not None and time.monotonic() < cached[1] - _REFRESH_SKEW_SECONDS:
        return cached[0]

    async with _service_token_lock:
        # 等待锁期间，其他调用方可能已经刷新令牌。
        cached = _service_token_cache.get(cache_key)
        if cached is not None and time.monotonic() < cached[1] - _REFRESH_SKEW_SECONDS:
            return cached[0]

        token_data = await request_token("client_credentials", scope=scope)
        access_token = token_data["access_token"]
        expires_in = token_data.get("expires_in", 3600)
        _service_token_cache[cache_key] = (access_token, time.monotonic() + expires_in)
        return access_token


def reset_service_token_cache_for_tests() -> None:
    """清空进程内令牌缓存，供测试用例之间隔离状态。"""
    _service_token_cache.clear()


def build_mcp_http_client() -> httpx.AsyncClient:
    """为 oauth 模式的 MCP 工具创建独立 HTTP 客户端。

    默认 Authorization 请求头覆盖整个 MCP 会话，包括初始化和工具列表
    握手。MAF 的 header_provider 只覆盖 call_tool，无法认证工具调用
    之外的握手请求。令牌获取后通过 set_mcp_auth_header 设置默认头。
    """
    return httpx.AsyncClient()


def set_mcp_auth_header(client: httpx.AsyncClient, token: str) -> None:
    """原地设置或替换客户端默认 Authorization 请求头。"""
    client.headers["Authorization"] = f"Bearer {token}"


async def build_a2a_headers() -> dict[str, str]:
    """构建出站 A2A 请求头。

    local 使用共享密钥；oauth 使用含 agent:invoke 范围的短期服务令牌，
    不发送共享密钥。两种模式都通过 x-user-* 转发用户身份。
    """
    headers = {
        "x-user-email": current_user_email.get(""),
        "x-user-role": current_user_role.get(""),
        "x-session-id": current_session_id.get(""),
    }
    if settings.AUTH_MODE == "oauth":
        token = await acquire_service_token(scope="agent:invoke", audience=settings.AUTH_AGENT_AUDIENCE)
        headers["authorization"] = f"Bearer {token}"
    else:
        headers["x-agent-secret"] = settings.AGENT_SHARED_SECRET
    return headers
