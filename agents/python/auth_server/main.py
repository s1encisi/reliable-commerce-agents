"""自托管 OAuth2 授权服务器入口。

启动 HTTP 服务：
    uvicorn auth_server.main:app --host 0.0.0.0 --port 8090

端点：
    GET  /health
    GET  /.well-known/jwks.json
    GET  /.well-known/oauth-authorization-server（RFC 8414 元数据）
    POST /oauth/token
    POST /oauth/register（RFC 7591；由 AUTH_ALLOW_DYNAMIC_REGISTRATION 开关控制）
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

import httpx
import joserfc.errors as jose_errors
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import RSAKey
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from auth_server import keys, register
from auth_server._bridge import bind_main_loop
from auth_server.clients import ClientStore
from auth_server.server import OAuthAuthorizationServer
from shared.config import settings
from shared.db import close_db_pool, get_pool, init_db_pool

logger = logging.getLogger(__name__)

_server: OAuthAuthorizationServer | None = None
_signing_key: RSAKey | None = None


@asynccontextmanager
async def lifespan(app: Starlette):
    bind_main_loop()
    await init_db_pool()
    pool = get_pool()

    kid, signing_key = await keys.ensure_active_key(pool)

    client_store = ClientStore()
    await client_store.load(pool)

    global _server, _signing_key
    _signing_key = signing_key
    _server = OAuthAuthorizationServer(
        client_store=client_store,
        pool=pool,
        issuer=settings.AUTH_SERVER_ISSUER,
        kid=kid,
        signing_key=signing_key,
    )
    logger.info("auth_server.ready issuer=%s kid=%s", settings.AUTH_SERVER_ISSUER, kid)

    try:
        yield
    finally:
        await close_db_pool()


class _RegistrationTokenError(Exception):
    """_verify_registration_token 校验失败时抛出的异常。"""


def _verify_registration_token(token: str) -> None:
    """使用本进程内存中的签名密钥校验 /oauth/register 的持有者令牌。

    不能复用 shared/oauth/verifier.py 的 RS256Verifier：它通过阻塞 HTTP
    请求获取 JWKS。授权服务器是单工作进程的 asyncio 服务，在请求处理中
    同步访问自身 JWKS 会阻塞事件循环，直到超时。其他资源服务器不会遇到
    这个问题，因为它们访问的是独立的授权服务器进程。

    授权服务器应直接使用已有密钥验签，并自行检查 iss、aud、scope、exp；
    joserfc.decode 只检查签名，不会完成这些声明校验。
    """
    if _signing_key is None:
        raise _RegistrationTokenError("auth-server not initialized")

    try:
        decoded = joserfc_jwt.decode(token, _signing_key)
    except jose_errors.JoseError as exc:
        raise _RegistrationTokenError(str(exc)) from exc

    claims = decoded.claims
    if claims.get("iss") != settings.AUTH_SERVER_ISSUER:
        raise _RegistrationTokenError("wrong issuer")
    if settings.AUTH_SERVER_AUDIENCE not in (claims.get("aud") or []):
        raise _RegistrationTokenError("wrong audience")
    if "client:register" not in (claims.get("scope") or "").split():
        raise _RegistrationTokenError("missing required scope")
    if claims.get("exp", 0) <= time.time():
        raise _RegistrationTokenError("token expired")


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "auth-server"})


async def jwks(request: Request) -> JSONResponse:
    document = await keys.get_jwks(get_pool())
    return JSONResponse(document)


async def metadata(request: Request) -> JSONResponse:
    """RFC 8414 授权服务器元数据。"""
    issuer = settings.AUTH_SERVER_ISSUER
    return JSONResponse(
        {
            "issuer": issuer,
            "token_endpoint": f"{issuer}/oauth/token",
            "jwks_uri": f"{issuer}/.well-known/jwks.json",
            "registration_endpoint": f"{issuer}/oauth/register",
            "grant_types_supported": ["client_credentials", "password", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
            "scopes_supported": [
                "api:chat",
                "agent:invoke",
                settings.MCP_PRODUCT_REQUIRED_SCOPE,
                settings.MCP_INVENTORY_REQUIRED_SCOPE,
                "client:register",
            ],
        }
    )


async def token_endpoint(request: Request) -> JSONResponse:
    form = await request.form()
    form_dict = {k: v for k, v in form.items()}
    # ASGI 将请求头名称转为小写，而 authlib 按精确的 Authorization 名称取值。
    # 如果使用普通 dict，两者大小写不同会导致认证信息无法匹配。
    # httpx.Headers 的读写都不区分大小写，
    # 并提供同步桥接层需要的映射接口，
    # 可在工作线程中安全读取。
    headers = httpx.Headers(dict(request.headers))

    assert _server is not None, "auth-server not initialized"
    status, body, response_headers = await asyncio.to_thread(
        _server.handle_token_request, form_dict, headers, str(request.url)
    )
    return JSONResponse(body, status_code=status, headers=dict(response_headers or {}))


def _unauthorized(error: str, description: str) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=401,
        headers={"WWW-Authenticate": f'Bearer error="{error}", error_description="{description}"'},
    )


async def register_endpoint(request: Request) -> JSONResponse:
    """RFC 7591 动态客户端注册，默认关闭。

    调用方必须提供含 client:register 范围的令牌，并在本进程内校验，
    不能通过网络访问自身。请求体校验与持久化见 auth_server/register.py。
    """
    if not settings.AUTH_ALLOW_DYNAMIC_REGISTRATION:
        return JSONResponse(
            {"error": "registration_disabled", "error_description": "Dynamic client registration is disabled"},
            status_code=403,
        )

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return _unauthorized("invalid_token", "Authentication required")

    token = auth_header[len("Bearer ") :]
    try:
        _verify_registration_token(token)
    except _RegistrationTokenError as exc:
        logger.warning("auth_server.register_token_rejected error=%s", exc)
        return _unauthorized("invalid_token", "Invalid, expired, or insufficiently-scoped token")

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "invalid_client_metadata", "error_description": "Request body must be valid JSON"},
            status_code=400,
        )

    try:
        client_name, scopes = register.validate_registration_request(body)
    except register.RegistrationError as exc:
        return JSONResponse(exc.to_body(), status_code=exc.status)

    pool = get_pool()
    client_id, client_secret = await register.create_client(pool, client_name, scopes)

    assert _server is not None, "auth-server not initialized"
    await _server.client_store.load(pool)

    return JSONResponse(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "client_id_issued_at": int(time.time()),
            "client_secret_expires_at": 0,
            "client_name": client_name,
            "grant_types": ["client_credentials"],
            "scope": " ".join(scopes),
            "token_endpoint_auth_method": "client_secret_basic",
        },
        status_code=201,
    )


app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/health", health),
        Route("/.well-known/jwks.json", jwks),
        Route("/.well-known/oauth-authorization-server", metadata),
        Route("/oauth/token", token_endpoint, methods=["POST"]),
        Route("/oauth/register", register_endpoint, methods=["POST"]),
    ],
)


def main() -> None:
    """命令行入口，与其他智能体 Dockerfile 的 CMD 保持一致。"""
    import uvicorn

    uvicorn.run("auth_server.main:app", host="0.0.0.0", port=8090)


if __name__ == "__main__":
    main()
