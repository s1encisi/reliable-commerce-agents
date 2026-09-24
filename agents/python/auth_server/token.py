"""自托管 OAuth2 授权服务器的 RFC 9068 JWT 访问令牌生成器。

继承 authlib.oauth2.rfc9068.JWTBearerTokenGenerator，复用标准声明集合
iss/exp/client_id/iat/jti/scope/sub/aud 和 typ=at+jwt 头。补充两项能力：

1. 在 JWS 头中写入当前签名密钥的 kid。基类没有对应扩展点，因此重写
   生成方法，让资源服务器在密钥轮换后能从 JWKS 中选出正确密钥。
2. 将范围映射到资源受众，如 mcp:product 对应 mcp-product。用户令牌
   另从 grants.py 的 User 对象写入 role 与 user_id。编排器路由需要
   数据库 UUID 作为 user_id；OAuth 的 sub 是邮箱，不能替代主键。
"""

from __future__ import annotations

import time

from authlib.oauth2.rfc9068 import JWTBearerTokenGenerator
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import RSAKey

from shared.config import settings


def _scope_audience_map() -> dict[str, str]:
    """根据配置把权限范围映射为受众，尊重自定义覆盖值。"""
    return {
        "api:chat": settings.AUTH_ORCH_AUDIENCE,
        "agent:invoke": settings.AUTH_AGENT_AUDIENCE,
        settings.MCP_PRODUCT_REQUIRED_SCOPE: settings.MCP_PRODUCT_AUDIENCE,
        settings.MCP_INVENTORY_REQUIRED_SCOPE: settings.MCP_INVENTORY_AUDIENCE,
        # 授权服务器自身也是可选动态注册端点的受保护资源。
        # 该入口受开关约束，详见 auth_server/register.py。
        "client:register": settings.AUTH_SERVER_AUDIENCE,
    }


class AccessTokenGenerator(JWTBearerTokenGenerator):
    """带 kid 头和 role 声明的 RS256 JWT 访问令牌。"""

    def __init__(self, issuer: str, kid: str, signing_key: RSAKey, refresh_token_generator=None):
        super().__init__(issuer=issuer, alg="RS256", refresh_token_generator=refresh_token_generator)
        self._kid = kid
        self._signing_key = signing_key

    def get_jwks(self):
        return self._signing_key

    def get_audiences(self, client, user, scope) -> str | list[str]:
        requested = (scope or "").split()
        mapping = _scope_audience_map()
        audiences = sorted({aud for s in requested if (aud := mapping.get(s))})
        return audiences or client.get_client_id()

    def get_extra_claims(self, client, grant_type, user, scope):
        if user is None:
            return {}
        claims = {}
        if role := getattr(user, "role", None):
            claims["role"] = role
        if user_id := getattr(user, "user_id", None):
            claims["user_id"] = user_id
        return claims

    def access_token_generator(self, client, grant_type, user, scope):
        """重建 RFC 9068 声明集合，并在 JWS 头中加入 kid。"""
        now = int(time.time())
        expires_in = now + self._get_expires_in(client, grant_type)

        token_data = {
            "iss": self.issuer,
            "exp": expires_in,
            "client_id": client.get_client_id(),
            "iat": now,
            "jti": self.get_jti(client, grant_type, user, scope),
            "scope": scope,
            "sub": user.get_user_id() if user else client.get_client_id(),
            "aud": self.get_audiences(client, user, scope),
        }
        token_data.update(self.get_extra_claims(client, grant_type, user, scope))

        header = {"alg": self.alg, "typ": "at+jwt", "kid": self._kid}
        return joserfc_jwt.encode(header, token_data, key=self._signing_key)
