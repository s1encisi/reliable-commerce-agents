"""RFC 7591 动态客户端注册的校验和持久化逻辑。

HTTP 路由位于 main.py；AUTH_ALLOW_DYNAMIC_REGISTRATION 默认关闭。
平时客户端由固定种子数据注册，详见 clients.py。启用此入口后，main.py
仍须先校验调用方令牌中的 client:register 范围，本模块只处理校验后的请求。

新客户端仅能使用 client_credentials，且范围限于两个 MCP 只读范围。
第一方交互登录客户端保持固定，不能在这里申请 password、refresh_token、
agent:invoke、api:chat 或 client:register 权限。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

import asyncpg
from authlib.common.security import generate_token

from auth_server.token import _scope_audience_map
from shared.config import settings
from shared.jwt_utils import hash_password

REGISTRABLE_SCOPES = frozenset(
    {
        settings.MCP_PRODUCT_REQUIRED_SCOPE,
        settings.MCP_INVENTORY_REQUIRED_SCOPE,
    }
)


@dataclass
class RegistrationError(Exception):
    """携带 RFC 7591 错误对象：{"error": ..., "error_description": ...}。"""

    status: int
    error: str
    description: str

    def to_body(self) -> dict:
        return {"error": self.error, "error_description": self.description}


def validate_registration_request(body: dict) -> tuple[str, list[str]]:
    """校验注册请求体，成功时返回 (client_name, scopes)。

    未知范围、缺少名称、不支持的授权类型或重定向流程均抛出
    RegistrationError，对应 HTTP 400 与 invalid_client_metadata。
    """
    client_name = body.get("client_name")
    if not isinstance(client_name, str) or not client_name.strip():
        raise RegistrationError(400, "invalid_client_metadata", "client_name is required")

    raw_scope = body.get("scope")
    if not isinstance(raw_scope, str) or not raw_scope.strip():
        raise RegistrationError(400, "invalid_client_metadata", "scope is required")
    requested = raw_scope.split()
    unknown = [s for s in requested if s not in REGISTRABLE_SCOPES]
    if unknown:
        raise RegistrationError(
            400,
            "invalid_client_metadata",
            f"scope(s) not registrable via this endpoint: {', '.join(unknown)}",
        )

    grant_types = body.get("grant_types")
    if grant_types is not None and list(grant_types) != ["client_credentials"]:
        raise RegistrationError(
            400,
            "invalid_client_metadata",
            "grant_types must be exactly ['client_credentials'] — this AS supports no redirect flow",
        )

    if body.get("redirect_uris"):
        raise RegistrationError(
            400,
            "invalid_client_metadata",
            "redirect_uris is not supported — this AS has no authorization-code/redirect flow",
        )

    return client_name.strip(), sorted(requested)


async def create_client(pool: asyncpg.Pool, client_name: str, scopes: list[str]) -> tuple[str, str]:
    """生成、哈希并持久化客户端，返回 (client_id, plaintext_secret)。

    明文密钥只返回一次；oauth_clients 中仅保存 bcrypt 哈希。
    """
    client_id = f"ext-{secrets.token_hex(8)}"
    client_secret = generate_token(48)
    secret_hash = hash_password(client_secret)

    audience_map = _scope_audience_map()
    audiences = sorted({aud for s in scopes if (aud := audience_map.get(s))})

    await pool.execute(
        """INSERT INTO oauth_clients
               (client_id, client_secret_hash, client_name, allowed_grant_types,
                allowed_scopes, allowed_audiences, token_endpoint_auth_method)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        client_id,
        secret_hash,
        client_name,
        ["client_credentials"],
        scopes,
        audiences,
        "client_secret_basic",
    )
    return client_id, client_secret
