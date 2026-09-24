"""真实 authlib 调用链的令牌端点往返测试。

直接构建 OAuthAuthorizationServer，使用真实测试 PostgreSQL，
通过 to_thread 和同步桥接调用；绕开读取全局配置的应用生命周期。
"""

from __future__ import annotations

import asyncio
import base64

import asyncpg
import httpx
import pytest
import pytest_asyncio
from joserfc import jwt as joserfc_jwt

import shared.db as shared_db
from auth_server import _bridge, keys
from auth_server.clients import ClientStore
from auth_server.grants import hash_token
from auth_server.server import OAuthAuthorizationServer
from shared.jwt_utils import hash_password

pytestmark = pytest.mark.integration

ISSUER = "http://test-auth-server"


@pytest_asyncio.fixture(autouse=True)
async def _db_pool(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> asyncpg.Pool:
    """把 clean_db 注入共享池，供授权类通过 get_pool 访问。"""
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    return clean_db


def _basic_auth_header(client_id: str, secret: str) -> httpx.Headers:
    token = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    return httpx.Headers({"Authorization": f"Basic {token}"})


async def _seed_client(pool, client_id, secret, grants, scopes, audiences):
    await pool.execute(
        """INSERT INTO oauth_clients
               (client_id, client_secret_hash, client_name, allowed_grant_types, allowed_scopes, allowed_audiences)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        client_id,
        hash_password(secret),
        client_id,
        grants,
        scopes,
        audiences,
    )


async def _seed_user(pool, email, password, role="customer") -> str:
    row = await pool.fetchrow(
        """INSERT INTO users (email, password_hash, name, role, is_active)
           VALUES ($1, $2, $3, $4, TRUE)
           RETURNING id""",
        email,
        hash_password(password),
        "Test User",
        role,
    )
    return str(row["id"])


@pytest.fixture(autouse=True)
async def _bind_loop():
    """必须使用异步夹具，在 pytest-asyncio 活动循环内执行。"""
    _bridge.bind_main_loop()


@pytest.fixture
async def server(clean_db):
    kid, signing_key = await keys.ensure_active_key(clean_db)
    store = ClientStore()
    await store.load(clean_db)
    return OAuthAuthorizationServer(
        client_store=store, pool=clean_db, issuer=ISSUER, kid=kid, signing_key=signing_key
    ), signing_key


class TestClientCredentialsGrant:
    async def test_issues_scoped_token(self, clean_db, server):
        srv, signing_key = server
        await _seed_client(
            clean_db,
            "product-discovery",
            "topsecret",
            ["client_credentials"],
            ["agent:invoke", "mcp:product"],
            ["ecommerce-agents", "mcp-product"],
        )
        await srv.client_store.load(clean_db)

        status, body, _headers = await asyncio.to_thread(
            srv.handle_token_request,
            {"grant_type": "client_credentials", "scope": "agent:invoke mcp:product"},
            _basic_auth_header("product-discovery", "topsecret"),
        )

        assert status == 200
        assert body["token_type"] == "Bearer"
        assert "refresh_token" not in body

        decoded = joserfc_jwt.decode(body["access_token"], signing_key)
        assert decoded.header["typ"] == "at+jwt"
        assert decoded.header["kid"]  # 响应需包含 kid，让多个 JWKS 密钥可被正确选择。
        claims = decoded.claims
        assert claims["iss"] == ISSUER
        assert claims["sub"] == "product-discovery"
        assert claims["client_id"] == "product-discovery"
        assert set(claims["aud"]) == {"ecommerce-agents", "mcp-product"}
        assert set(claims["scope"].split()) == {"agent:invoke", "mcp:product"}

    async def test_wrong_secret_rejected(self, clean_db, server):
        srv, _ = server
        await _seed_client(
            clean_db, "svc", "right-secret", ["client_credentials"], ["agent:invoke"], ["ecommerce-agents"]
        )
        await srv.client_store.load(clean_db)

        status, body, _headers = await asyncio.to_thread(
            srv.handle_token_request,
            {"grant_type": "client_credentials", "scope": "agent:invoke"},
            _basic_auth_header("svc", "wrong-secret"),
        )
        assert status == 401
        assert body["error"] == "invalid_client"

    async def test_disallowed_grant_type_rejected(self, clean_db, server):
        srv, _ = server
        await _seed_client(clean_db, "ro-only", "sekrit", ["password"], ["agent:invoke"], ["ecommerce-agents"])
        await srv.client_store.load(clean_db)

        status, body, _headers = await asyncio.to_thread(
            srv.handle_token_request,
            {"grant_type": "client_credentials"},
            _basic_auth_header("ro-only", "sekrit"),
        )
        assert status == 400
        assert body["error"] == "unauthorized_client"

    async def test_out_of_scope_request_is_trimmed_not_rejected(self, clean_db, server):
        """本服务器按 Client.get_allowed_scope 取请求范围与许可范围的交集。"""
        srv, signing_key = server
        await _seed_client(clean_db, "narrow", "sekrit", ["client_credentials"], ["agent:invoke"], ["ecommerce-agents"])
        await srv.client_store.load(clean_db)

        status, body, _headers = await asyncio.to_thread(
            srv.handle_token_request,
            {"grant_type": "client_credentials", "scope": "agent:invoke mcp:product"},
            _basic_auth_header("narrow", "sekrit"),
        )
        assert status == 200
        decoded = joserfc_jwt.decode(body["access_token"], signing_key)
        assert decoded.claims["scope"] == "agent:invoke"


class TestResourceOwnerPasswordCredentialsGrant:
    async def test_issues_access_and_refresh_token_with_role_claim(self, clean_db, server):
        srv, signing_key = server
        await _seed_client(
            clean_db,
            "orchestrator",
            "orch-secret",
            ["password", "refresh_token"],
            ["api:chat"],
            ["ecommerce-orchestrator"],
        )
        user_id = await _seed_user(clean_db, "alice@example.com", "hunter2", role="admin")
        await srv.client_store.load(clean_db)

        status, body, _headers = await asyncio.to_thread(
            srv.handle_token_request,
            {
                "grant_type": "password",
                "username": "alice@example.com",
                "password": "hunter2",
                "scope": "api:chat",
            },
            _basic_auth_header("orchestrator", "orch-secret"),
        )

        assert status == 200
        assert "refresh_token" in body
        decoded = joserfc_jwt.decode(body["access_token"], signing_key)
        # 编排器路由直接读取令牌中的 user_id，
        # 该字段应通过集成链路验证，
        # 值必须是 users.id 的 UUID，不能用 sub 邮箱替代。
        assert decoded.claims["user_id"] == user_id
        assert decoded.claims["sub"] == "alice@example.com"
        assert decoded.claims["role"] == "admin"
        assert decoded.claims["aud"] == ["ecommerce-orchestrator"]

        # 刷新令牌已实际保存为哈希。
        row = await clean_db.fetchrow(
            "SELECT subject FROM oauth_tokens WHERE token_hash = $1", hash_token(body["refresh_token"])
        )
        assert row["subject"] == "alice@example.com"

    async def test_wrong_password_rejected(self, clean_db, server):
        srv, _ = server
        await _seed_client(
            clean_db, "orchestrator", "orch-secret", ["password"], ["api:chat"], ["ecommerce-orchestrator"]
        )
        await _seed_user(clean_db, "bob@example.com", "hunter2")
        await srv.client_store.load(clean_db)

        status, body, _headers = await asyncio.to_thread(
            srv.handle_token_request,
            {"grant_type": "password", "username": "bob@example.com", "password": "wrong"},
            _basic_auth_header("orchestrator", "orch-secret"),
        )
        assert status == 400
        assert body["error"] == "invalid_request"


class TestRefreshTokenGrant:
    async def _issue_initial_tokens(self, clean_db, srv):
        await _seed_client(
            clean_db,
            "orchestrator",
            "orch-secret",
            ["password", "refresh_token"],
            ["api:chat"],
            ["ecommerce-orchestrator"],
        )
        await _seed_user(clean_db, "carol@example.com", "hunter2")
        await srv.client_store.load(clean_db)

        _status, body, _headers = await asyncio.to_thread(
            srv.handle_token_request,
            {
                "grant_type": "password",
                "username": "carol@example.com",
                "password": "hunter2",
                "scope": "api:chat",
            },
            _basic_auth_header("orchestrator", "orch-secret"),
        )
        return body["refresh_token"]

    async def test_refresh_does_not_rotate_the_token(self, clean_db, server):
        """浏览器保存的单个刷新令牌必须持续有效；轮换会破坏现有前端契约。"""
        srv, signing_key = server
        refresh_token = await self._issue_initial_tokens(clean_db, srv)

        status, body, _headers = await asyncio.to_thread(
            srv.handle_token_request,
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
            _basic_auth_header("orchestrator", "orch-secret"),
        )

        assert status == 200
        assert "refresh_token" not in body  # 不轮换：不会签发新刷新令牌。
        decoded = joserfc_jwt.decode(body["access_token"], signing_key)
        assert decoded.claims["sub"] == "carol@example.com"

        # 同一刷新令牌第二次仍应通过校验。
        status2, body2, _headers2 = await asyncio.to_thread(
            srv.handle_token_request,
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
            _basic_auth_header("orchestrator", "orch-secret"),
        )
        assert status2 == 200
        assert "access_token" in body2

    async def test_unknown_refresh_token_rejected(self, clean_db, server):
        srv, _ = server
        await _seed_client(
            clean_db, "orchestrator", "orch-secret", ["refresh_token"], ["api:chat"], ["ecommerce-orchestrator"]
        )
        await srv.client_store.load(clean_db)

        status, body, _headers = await asyncio.to_thread(
            srv.handle_token_request,
            {"grant_type": "refresh_token", "refresh_token": "never-issued"},
            _basic_auth_header("orchestrator", "orch-secret"),
        )
        assert status == 400
        assert body["error"] == "invalid_grant"
