"""OAuth 客户端注册表——固定、经播种、内存缓存。

这是离线优先的，且一方客户端集合已知且静态（没有动态客户端注册），因此
注册表在启动时从 ``oauth_clients`` 加载一次，之后从内存提供。这也顺带避开了
热路径上 authlib 的 ``query_client`` 被同步调用的问题（见 ``_bridge.py``）：
客户端查找从不按请求访问数据库。
"""

from __future__ import annotations

import logging

import asyncpg
import bcrypt
from authlib.oauth2.rfc6749 import ClientMixin
from authlib.oauth2.rfc6749.util import list_to_scope, scope_to_list

logger = logging.getLogger(__name__)


class Client(ClientMixin):
    def __init__(
        self,
        client_id: str,
        client_secret_hash: str,
        allowed_grant_types: list[str],
        allowed_scopes: list[str],
        allowed_audiences: list[str],
        token_endpoint_auth_method: str,
    ):
        self.client_id = client_id
        self.client_secret_hash = client_secret_hash
        self.allowed_grant_types = set(allowed_grant_types)
        self.allowed_scopes = set(allowed_scopes)
        self.allowed_audiences = list(allowed_audiences)
        self.token_endpoint_auth_method = token_endpoint_auth_method

    # ClientMixin 接口

    def get_client_id(self) -> str:
        return self.client_id

    def get_default_redirect_uri(self):
        return None  # 本 AS 没有授权码/重定向流程

    def get_allowed_scope(self, scope: str | None) -> str:
        if not scope:
            return ""
        requested = set(scope_to_list(scope))
        return list_to_scope(sorted(requested & self.allowed_scopes))

    def check_redirect_uri(self, redirect_uri: str) -> bool:
        return False  # 本 AS 没有授权码/重定向流程

    def check_client_secret(self, client_secret: str) -> bool:
        try:
            return bcrypt.checkpw(client_secret.encode(), self.client_secret_hash.encode())
        except (ValueError, TypeError):
            return False

    def check_endpoint_auth_method(self, method: str, endpoint: str) -> bool:
        if endpoint != "token":
            return True
        return self.token_endpoint_auth_method == method

    def check_response_type(self, response_type: str) -> bool:
        return False  # 本 AS 没有授权码/隐式流程

    def check_grant_type(self, grant_type: str) -> bool:
        return grant_type in self.allowed_grant_types


class ClientStore:
    """内存中的客户端注册表，从 ``oauth_clients`` 预热一次。"""

    def __init__(self) -> None:
        self._clients: dict[str, Client] = {}

    async def load(self, pool: asyncpg.Pool) -> None:
        rows = await pool.fetch(
            """SELECT client_id, client_secret_hash, allowed_grant_types,
                      allowed_scopes, allowed_audiences, token_endpoint_auth_method
               FROM oauth_clients"""
        )
        clients = {
            row["client_id"]: Client(
                client_id=row["client_id"],
                client_secret_hash=row["client_secret_hash"],
                allowed_grant_types=list(row["allowed_grant_types"]),
                allowed_scopes=list(row["allowed_scopes"]),
                allowed_audiences=list(row["allowed_audiences"]),
                token_endpoint_auth_method=row["token_endpoint_auth_method"],
            )
            for row in rows
        }
        self._clients = clients
        logger.info("auth_server.clients_loaded count=%d", len(clients))

    def get(self, client_id: str) -> Client | None:
        return self._clients.get(client_id)
