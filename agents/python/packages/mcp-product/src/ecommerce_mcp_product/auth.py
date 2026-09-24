"""本服务 OAuth 2.1 资源服务器模式下基于 JWKS 的令牌校验器。

此处为内置副本，而非共享代码：``ecommerce-mcp-product`` 是一个独立的 uv
工作区成员，从不导入 ``shared/``（它可独立安装/发布 —— 参见设计文档的
修正 #7）。主应用中精神上一致的 ``shared/oauth/verifier.py::RS256Verifier``
被有意地在此不复用；本模块改为手工与其保持同步，而不是引入跨包依赖。

仅在 ``MCP_AUTH_ENABLED=true`` 时生效（参见 ``server.py``）—— 本模块
自身在导入时没有副作用。
"""

from __future__ import annotations

import os

import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier

AUTH_SERVER_JWKS_URL = os.environ.get("AUTH_SERVER_JWKS_URL", "http://localhost:8090/.well-known/jwks.json")
AUTH_SERVER_ISSUER = os.environ.get("AUTH_SERVER_ISSUER", "http://localhost:8090")
MCP_PRODUCT_AUDIENCE = os.environ.get("MCP_PRODUCT_AUDIENCE", "mcp-product")
MCP_PRODUCT_REQUIRED_SCOPE = os.environ.get("MCP_PRODUCT_REQUIRED_SCOPE", "mcp:product")


class JwksTokenVerifier(TokenVerifier):
    """基于自托管 auth-server 的 JWKS 校验 bearer 令牌。"""

    def __init__(
        self,
        *,
        jwks_url: str = AUTH_SERVER_JWKS_URL,
        issuer: str = AUTH_SERVER_ISSUER,
        audience: str = MCP_PRODUCT_AUDIENCE,
        required_scope: str = MCP_PRODUCT_REQUIRED_SCOPE,
    ) -> None:
        self._jwks_client = PyJWKClient(jwks_url, cache_keys=True)
        self._issuer = issuer
        self._audience = audience
        self._required_scope = required_scope

    async def verify_token(self, token: str) -> AccessToken | None:
        """令牌有效时返回 ``AccessToken``，否则返回 ``None``（MCP
        SDK 会把 ``None`` 返回值映射为 401 + ``WWW-Authenticate`` 响应 ——
        无需向外传播异常）。"""
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.PyJWTError:
            return None

        granted_scopes = (payload.get("scope") or "").split()
        if self._required_scope not in granted_scopes:
            return None

        return AccessToken(
            token=token,
            client_id=payload.get("client_id", payload.get("sub", "unknown")),
            scopes=granted_scopes,
            expires_at=payload.get("exp"),
        )
