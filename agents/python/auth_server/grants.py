"""自托管授权服务器的 OAuth2 各 grant 类。

``ClientCredentialsGrant`` 无需定制——authlib 内置的实现对我们的用途已经
完整，并在 ``server.py`` 中按原样注册。另外两个 grant 需要对 Postgres 做
用户/令牌查询，经由 ``_bridge`` 从 authlib 的同步回调桥接过去（原因见它的
模块 docstring）。
"""

from __future__ import annotations

import hashlib
import logging

from authlib.oauth2.rfc6749 import TokenMixin
from authlib.oauth2.rfc6749.grants import RefreshTokenGrant as _BaseRefreshTokenGrant
from authlib.oauth2.rfc6749.grants import (
    ResourceOwnerPasswordCredentialsGrant as _BaseResourceOwnerPasswordCredentialsGrant,
)

from auth_server._bridge import run_coro_sync
from shared.config import settings
from shared.db import get_pool
from shared.jwt_utils import verify_password

logger = logging.getLogger(__name__)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


class User:
    """最小化的资源所有者包装。

    ``get_user_id()`` 是 authlib 自己的钩子，支撑 OAuth 的 ``sub`` 声明
    ——值为 email，与 shared/jwt_utils.py 中既有的 ``sub``=email 约定一致。
    ``user_id`` 则是单独的 `users.id` UUID，约 18 条编排器路由会直接从
    令牌负载读取它（``user.get("user_id")``）；它在
    ``token.py::get_extra_claims`` 中与 ``role`` 一起被标记为独立的声明。
    """

    def __init__(self, email: str, role: str, is_active: bool, user_id: str):
        self.email = email
        self.role = role
        self.is_active = is_active
        self.user_id = user_id

    def get_user_id(self) -> str:
        return self.email


class ResourceOwnerPasswordCredentialsGrant(_BaseResourceOwnerPasswordCredentialsGrant):
    def authenticate_user(self, username: str, password: str) -> User | None:
        async def _lookup() -> User | None:
            pool = get_pool()
            row = await pool.fetchrow(
                "SELECT id, email, password_hash, role, is_active FROM users WHERE email = $1",
                username,
            )
            if row is None or not row["is_active"]:
                return None
            if not verify_password(password, row["password_hash"]):
                return None
            return User(email=row["email"], role=row["role"], is_active=row["is_active"], user_id=str(row["id"]))

        return run_coro_sync(_lookup())


class RefreshTokenRecord(TokenMixin):
    def __init__(self, client_id: str, subject: str | None, scope: str | None):
        self.client_id = client_id
        self.subject = subject
        self.scope = scope

    def check_client(self, client) -> bool:
        return self.client_id == client.get_client_id()

    def get_scope(self) -> str | None:
        return self.scope

    def get_expires_in(self) -> int:
        return settings.AUTH_REFRESH_TOKEN_TTL


class RefreshTokenGrant(_BaseRefreshTokenGrant):
    # authlib 已经默认把 INCLUDE_NEW_REFRESH_TOKEN 设为 False——
    # 同一个刷新令牌在整个会话期间保持有效，因此编排器向前端的
    # 非轮换转发继续可用（见 docs/security-guide.md 与设计方案的
    # 更正 #6）。

    def authenticate_refresh_token(self, refresh_token: str) -> RefreshTokenRecord | None:
        async def _lookup() -> RefreshTokenRecord | None:
            pool = get_pool()
            row = await pool.fetchrow(
                """SELECT client_id, subject, scope FROM oauth_tokens
                   WHERE token_hash = $1 AND token_type = 'refresh_token'
                     AND revoked = FALSE AND expires_at > NOW()""",
                hash_token(refresh_token),
            )
            if row is None:
                return None
            return RefreshTokenRecord(client_id=row["client_id"], subject=row["subject"], scope=row["scope"])

        return run_coro_sync(_lookup())

    def authenticate_user(self, credential: RefreshTokenRecord) -> User | None:
        if not credential.subject:
            return None  # 服务（客户端凭据）令牌不携带刷新令牌

        async def _lookup() -> User | None:
            pool = get_pool()
            row = await pool.fetchrow(
                "SELECT id, email, role, is_active FROM users WHERE email = $1", credential.subject
            )
            if row is None or not row["is_active"]:
                return None
            return User(email=row["email"], role=row["role"], is_active=row["is_active"], user_id=str(row["id"]))

        return run_coro_sync(_lookup())

    def revoke_old_credential(self, refresh_token: RefreshTokenRecord) -> None:
        # 刻意做成空操作：非轮换 grant，同一个刷新令牌
        # 必须在整个会话生命周期内保持有效（更正 #6）。
        return None
