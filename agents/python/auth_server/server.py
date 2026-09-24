"""将 authlib.AuthorizationServer 桥接到 Starlette。

authlib 没有内置 Starlette/FastAPI 集成，因此这里实现其所需的
create_oauth2_request、create_json_request、handle_response、query_client
及 save_token 接口。main.py 负责解析请求并在线程中执行同步调用链；
本模块接收的是已经解析的普通请求对象。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import asyncpg
from authlib.common.security import generate_token
from authlib.oauth2.rfc6749 import AuthorizationServer as _BaseAuthorizationServer
from authlib.oauth2.rfc6749.grants import ClientCredentialsGrant
from authlib.oauth2.rfc6749.requests import BasicOAuth2Payload, JsonRequest, OAuth2Request

from auth_server._bridge import run_coro_sync
from auth_server.clients import ClientStore
from auth_server.grants import RefreshTokenGrant, ResourceOwnerPasswordCredentialsGrant, hash_token
from auth_server.token import AccessTokenGenerator
from shared.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SimpleRequest:
    """传给 authlib 同步调用链的已解析请求。

    必须在进入同步代码前构建，因为 authlib 不等待异步操作。headers 必须
    不区分大小写，例如 httpx.Headers：ASGI 提供小写键，而 authlib 按
    Authorization 取值。
    """

    method: str
    uri: str
    form: dict = field(default_factory=dict)
    headers: object = field(default_factory=dict)


class OAuthAuthorizationServer(_BaseAuthorizationServer):
    def __init__(self, client_store: ClientStore, pool: asyncpg.Pool, issuer: str, kid: str, signing_key):
        super().__init__(
            scopes_supported=[
                "api:chat",
                "agent:invoke",
                settings.MCP_PRODUCT_REQUIRED_SCOPE,
                settings.MCP_INVENTORY_REQUIRED_SCOPE,
                "client:register",
            ]
        )
        self.client_store = client_store
        self.pool = pool

        token_generator = AccessTokenGenerator(
            issuer=issuer,
            kid=kid,
            signing_key=signing_key,
            refresh_token_generator=lambda client, grant_type, user, scope: generate_token(48),
        )
        self.register_token_generator("default", token_generator)

        self.register_grant(ClientCredentialsGrant)
        self.register_grant(ResourceOwnerPasswordCredentialsGrant)
        self.register_grant(RefreshTokenGrant)

    # 框架集成

    def query_client(self, client_id: str):
        return self.client_store.get(client_id)

    def send_signal(self, name: str, *args, **kwargs) -> None:
        # 这是 authlib 对接框架信号系统的钩子，例如 Flask 的 blinker。
        # 本项目没有订阅者，因此这里有意保持空操作，
        # 避免触发基类的 NotImplementedError。
        return None

    def save_token(self, token: dict, request: OAuth2Request) -> None:
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            return  # client_credentials 和不轮换的 refresh_token 授权不会生成新刷新令牌。

        client = request.client
        user = getattr(request, "user", None)
        scope = token.get("scope") or ""
        expires_at = datetime.now(UTC) + timedelta(seconds=settings.AUTH_REFRESH_TOKEN_TTL)

        async def _persist() -> None:
            await self.pool.execute(
                """INSERT INTO oauth_tokens
                       (client_id, subject, token_type, token_hash, scope, expires_at)
                   VALUES ($1, $2, 'refresh_token', $3, $4, $5)""",
                client.get_client_id(),
                user.get_user_id() if user else None,
                hash_token(refresh_token),
                scope,
                expires_at,
            )

        # save_token 与 authlib 同步调用链的其他部分在同一个工作线程运行。
        # 因此需要桥接回主事件循环，
        # 方式与授权回调一致。
        run_coro_sync(_persist())

    def create_oauth2_request(self, request: SimpleRequest) -> OAuth2Request:
        req = OAuth2Request(request.method, request.uri, headers=request.headers)
        # 部分授权处理器会直接读取 form，所以它必须保留原始字典。
        # 例如 RefreshTokenGrant 会读取 request.form.get("refresh_token")。
        # 在对象构造后再设置它，
        # 避免使用已废弃的 body= 构造参数。
        req._body = request.form
        req.payload = BasicOAuth2Payload(request.form)
        return req

    def create_json_request(self, request: SimpleRequest) -> JsonRequest:
        # 此适配接口当前未被令牌端点使用，
        # 令牌端点经过 create_oauth2_request。
        # 保留最小可用实现，
        # 供后续需要 JSON 请求适配的端点复用。
        class _DictPayload:
            def __init__(self, data: dict):
                self._data = data

            @property
            def data(self) -> dict:
                return self._data

        req = JsonRequest(request.method, request.uri, headers=request.headers)
        req.payload = _DictPayload(request.form)
        return req

    def handle_response(self, status: int, body, headers):
        return status, body, headers

    # main.py 使用的入口

    def handle_token_request(self, form: dict, headers: object, uri: str = "/oauth/token"):
        """同步入口；路由应通过 asyncio.to_thread 调用。"""
        request = SimpleRequest(method="POST", uri=uri, form=form, headers=headers)
        return self.create_token_response(request)
