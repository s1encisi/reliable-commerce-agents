"""真实 MCP 资源令牌与 FastMCP 应用的认证集成测试。

真实数据库和 authlib 签发 mcp:product 令牌，只替换 JWKS 获取。
验证正确令牌通过、缺令牌返回 401 与认证提示，以及关闭认证时的兼容行为。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import asyncpg
import httpx
import pytest
import pytest_asyncio
from ecommerce_mcp_product.auth import JwksTokenVerifier
from ecommerce_mcp_product.server import mcp as bare_mcp
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

import shared.db as shared_db
from auth_server import _bridge, keys
from auth_server.clients import ClientStore
from auth_server.server import OAuthAuthorizationServer
from shared.jwt_utils import hash_password

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
    """真实 JWKS 校验器使用本进程签名密钥，不访问网络。"""
    _srv, signing_key = server
    v = JwksTokenVerifier(
        jwks_url=f"{ISSUER}/.well-known/jwks.json",
        issuer=ISSUER,
        audience="mcp-product",
        required_scope="mcp:product",
    )
    public_jwks = {"keys": [signing_key.as_dict(private=False)]}
    monkeypatch.setattr(v._jwks_client, "fetch_data", lambda: public_jwks)
    return v


@asynccontextmanager
async def _noop_lifespan(_server):
    yield


def _build_test_app(verifier: JwksTokenVerifier) -> FastMCP:
    test_mcp = FastMCP(
        "test-product-mcp",
        lifespan=_noop_lifespan,
        host="0.0.0.0",
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=ISSUER,
            resource_server_url="http://localhost:9000/mcp",
            required_scopes=["mcp:product"],
        ),
    )

    @test_mcp.tool()
    def ping() -> str:
        return "pong"

    return test_mcp


_INIT_BODY = {
    "jsonrpc": "2.0",
    "method": "initialize",
    "id": 1,
    "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
}
_MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


async def test_real_as_token_authenticates_mcp_resource_call(clean_db, server, verifier):
    srv, _signing_key = server
    await _seed_client(
        clean_db, "product-discovery", "topsecret", ["client_credentials"], ["mcp:product"], ["mcp-product"]
    )
    await srv.client_store.load(clean_db)

    status, body, _headers = await asyncio.to_thread(
        srv.handle_token_request,
        {"grant_type": "client_credentials", "scope": "mcp:product"},
        _basic_auth_header("product-discovery", "topsecret"),
    )
    assert status == 200, body
    token = body["access_token"]

    with TestClient(_build_test_app(verifier).streamable_http_app()) as client:
        r = client.post("/mcp", json=_INIT_BODY, headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"})
        assert r.status_code == 200


async def test_unauthenticated_call_rejected(verifier):
    with TestClient(_build_test_app(verifier).streamable_http_app()) as client:
        r = client.post("/mcp", json=_INIT_BODY, headers=_MCP_HEADERS)
        assert r.status_code == 401
        assert "WWW-Authenticate" in r.headers


async def test_wrong_audience_real_token_rejected(clean_db, server, verifier):
    """编排器 api:chat 令牌不能访问 mcp:product 资源。"""
    srv, _signing_key = server
    await _seed_client(
        clean_db, "orchestrator", "orch-secret", ["client_credentials"], ["api:chat"], ["ecommerce-orchestrator"]
    )
    await srv.client_store.load(clean_db)

    status, body, _headers = await asyncio.to_thread(
        srv.handle_token_request,
        {"grant_type": "client_credentials", "scope": "api:chat"},
        _basic_auth_header("orchestrator", "orch-secret"),
    )
    assert status == 200, body
    token = body["access_token"]

    with TestClient(_build_test_app(verifier).streamable_http_app()) as client:
        r = client.post("/mcp", json=_INIT_BODY, headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"})
        assert r.status_code == 401


def test_mcp_auth_disabled_is_unchanged_regression_guard():
    """MCP_AUTH_ENABLED=false 时，不应安装令牌校验器或认证配置。"""
    assert bare_mcp._token_verifier is None
