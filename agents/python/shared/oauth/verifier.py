"""使用自托管授权服务器的 JWKS 校验 RS256 访问令牌。

用于 oauth 模式下编排器用户路由和专业智能体认证，由工厂选择；
local 模式的 HS256 路径保持独立。
"""

from __future__ import annotations

import jwt
from jwt import PyJWKClient

from shared.config import settings


class RS256Verifier:
    """根据授权服务器公布的 JWKS 校验 RS256 访问令牌。"""

    def __init__(self) -> None:
        self._jwks_client = PyJWKClient(
            settings.AUTH_SERVER_JWKS_URL,
            cache_keys=True,
            lifespan=settings.AUTH_JWKS_CACHE_TTL,
        )

    def decode(self, token: str, *, audience: str, required_scope: str | None = None) -> dict:
        """校验签名、签发者、受众和过期时间。

        失败时抛出 PyJWTError 子类，与调用方已有 HS256 异常处理兼容。
        """
        signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=settings.AUTH_SERVER_ISSUER,
        )
        if required_scope is not None:
            granted = (payload.get("scope") or "").split()
            if required_scope not in granted:
                raise jwt.InvalidTokenError(f"token missing required scope '{required_scope}'")
        return payload
