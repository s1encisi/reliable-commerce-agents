"""真实 OAuth 服务令牌通过真实智能体认证中间件的集成测试。

使用真实 PostgreSQL 和 authlib 签发 RS256；只把 JWKS 网络获取
替换为本进程签名密钥，校验器与中间件均真实执行。
"""

from __future__ import annotations

import asyncio

import asyncpg
import httpx
import pytest
import pytest_asyncio
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import shared.db as shared_db
from auth_server import _bridge, keys
from auth_server.clients import ClientStore
from auth_server.server import OAuthAuthorizationServer
from shared.auth import AgentAuthMiddleware
from shared.config import settings
from shared.context import current_session_id, current_user_email, current_user_role
from shared.oauth.verifier import RS256Verifier

pytestmark = pytest.mark.integration

ISSUER = "http://test-auth-server"


@pytest_asyncio.fixture(autouse=True)
async def _db_pool(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> asyncpg.Pool:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    return clean_db


@pytest.fixture(autouse=True)
async def _bind_loop():
    _bridge.bind_main_loop()


async def _seed_client(pool, client_id, secret, grants, scopes, audiences):
    from shared.jwt_utils import hash_password

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


def _basic_auth_header(client_id: str, secret: str) -> httpx.Headers:
    import base64

    token = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    return httpx.Headers({"Authorization": f"Basic {token}"})


@pytest.fixture
async def server(clean_db):
    kid, signing_key = await keys.ensure_active_key(clean_db)
    store = ClientStore()
    await store.load(clean_db)
    srv = OAuthAuthorizationServer(client_store=store, pool=clean_db, issuer=ISSUER, kid=kid, signing_key=signing_key)
    return srv, signing_key


@pytest.fixture
def verifier(server, monkeypatch):
    """真实 RS256 校验器，从本进程授权服务器密钥获取 JWKS，无网络调用。"""
    _srv, signing_key = server
    monkeypatch.setattr(settings, "AUTH_SERVER_ISSUER", ISSUER)
    monkeypatch.setattr(settings, "AUTH_AGENT_AUDIENCE", "ecommerce-agents")
    monkeypatch.setattr(settings, "AUTH_ORCH_AUDIENCE", "ecommerce-orchestrator")
    v = RS256Verifier()
    public_jwks = {"keys": [signing_key.as_dict(private=False)]}
    monkeypatch.setattr(v._jwks_client, "fetch_data", lambda: public_jwks)
    monkeypatch.setattr(settings, "AUTH_MODE", "oauth")
    monkeypatch.setattr("shared.factory.get_token_verifier", lambda: v)
    return v


async def _ok(request):
    return JSONResponse(
        {
            "email": current_user_email.get(),
            "role": current_user_role.get(),
            "session_id": current_session_id.get(),
        }
    )


def _client() -> TestClient:
    app = Starlette(routes=[Route("/x", _ok, methods=["POST"])])
    app.add_middleware(AgentAuthMiddleware, agent_name="product-discovery")
    return TestClient(app)


async def _issue_token(clean_db, srv, client_id, secret, grants, scopes, audiences, scope):
    await _seed_client(clean_db, client_id, secret, grants, scopes, audiences)
    await srv.client_store.load(clean_db)
    status, body, _headers = await asyncio.to_thread(
        srv.handle_token_request,
        {"grant_type": "client_credentials", "scope": scope},
        _basic_auth_header(client_id, secret),
    )
    assert status == 200, body
    return body["access_token"]


async def test_real_service_token_authenticates_inter_agent_call(clean_db, server, verifier):
    srv, _signing_key = server
    token = await _issue_token(
        clean_db,
        srv,
        "product-discovery",
        "topsecret",
        ["client_credentials"],
        ["agent:invoke"],
        ["ecommerce-agents"],
        "agent:invoke",
    )

    resp = _client().post(
        "/x",
        headers={
            "Authorization": f"Bearer {token}",
            "x-user-email": "alice@example.com",
            "x-user-role": "admin",
            "x-session-id": "sess-1",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert body["role"] == "admin"
    assert body["session_id"] == "sess-1"


async def test_real_service_token_with_no_user_headers_defaults_to_system(clean_db, server, verifier):
    srv, _signing_key = server
    token = await _issue_token(
        clean_db,
        srv,
        "product-discovery",
        "topsecret",
        ["client_credentials"],
        ["agent:invoke"],
        ["ecommerce-agents"],
        "agent:invoke",
    )

    resp = _client().post("/x", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "system"
    assert body["role"] == "system"


async def test_wrong_audience_token_rejected(clean_db, server, verifier):
    """api:chat 受众的有效令牌不能认证需要 agent:invoke 的智能体间请求。"""
    srv, _signing_key = server
    token = await _issue_token(
        clean_db,
        srv,
        "orchestrator",
        "orch-secret",
        ["client_credentials"],
        ["api:chat"],
        ["ecommerce-orchestrator"],
        "api:chat",
    )

    resp = _client().post("/x", headers={"Authorization": f"Bearer {token}", "x-user-email": "alice@example.com"})
    assert resp.status_code == 401


async def test_spoofed_role_rejected_under_strict_identity(clean_db, server, verifier, monkeypatch):
    srv, _signing_key = server
    monkeypatch.setattr(settings, "GUARDRAILS_STRICT_IDENTITY", True)
    token = await _issue_token(
        clean_db,
        srv,
        "product-discovery",
        "topsecret",
        ["client_credentials"],
        ["agent:invoke"],
        ["ecommerce-agents"],
        "agent:invoke",
    )

    resp = _client().post(
        "/x",
        headers={
            "Authorization": f"Bearer {token}",
            "x-user-email": "alice@example.com",
            "x-user-role": "superadmin",
        },
    )
    assert resp.status_code == 401


async def test_agent_secret_rejected_when_oauth_mode_active(clean_db, server, verifier):
    """oauth 模式即使共享密钥正确也必须拒绝，只接受服务令牌。"""
    resp = _client().post("/x", headers={"x-agent-secret": settings.AGENT_SHARED_SECRET})
    assert resp.status_code == 401
