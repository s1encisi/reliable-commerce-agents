"""RFC 7591 动态注册测试：默认关闭，受权限范围约束。

用真实 PostgreSQL 和 authlib 签发 client:register 令牌，再通过
ASGITransport 驱动实际 Starlette 路由。直接设置服务器和共享池，
绕开读取进程全局配置的 lifespan。
"""

from __future__ import annotations

import asyncio

import asyncpg
import bcrypt
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import auth_server.main as main
import shared.db as shared_db
from auth_server import _bridge, keys
from auth_server.clients import ClientStore
from auth_server.server import OAuthAuthorizationServer
from shared.config import settings
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


@pytest.fixture(autouse=True)
def _issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    # 校验器使用 AUTH_SERVER_ISSUER 检查真实 iss 声明，
    # 该声明来自下方服务器夹具的 issuer 参数，
    # 两处必须一致。
    monkeypatch.setattr(settings, "AUTH_SERVER_ISSUER", ISSUER)


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
async def server(clean_db, monkeypatch: pytest.MonkeyPatch):
    """构建真实授权服务器并注入 Starlette 模块全局变量，绕过 lifespan。"""
    kid, signing_key = await keys.ensure_active_key(clean_db)
    store = ClientStore()
    await store.load(clean_db)
    srv = OAuthAuthorizationServer(client_store=store, pool=clean_db, issuer=ISSUER, kid=kid, signing_key=signing_key)
    monkeypatch.setattr(main, "_server", srv)
    # 注册令牌在进程内使用当前签名密钥校验，
    # 不经 JWKS HTTP 查询，
    # 因此无需模拟网络密钥获取。
    monkeypatch.setattr(main, "_signing_key", signing_key)

    return srv, signing_key


async def _mint_token(srv, client_id: str, secret: str, scope: str) -> str:
    status, body, _headers = await asyncio.to_thread(
        srv.handle_token_request,
        {"grant_type": "client_credentials", "scope": scope},
        _basic_auth_header(client_id, secret),
    )
    assert status == 200, body
    return body["access_token"]


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as c:
        yield c


async def test_disabled_by_default(client, server) -> None:
    resp = await client.post("/oauth/register", json={"client_name": "x", "scope": "mcp:product"})
    assert resp.status_code == 403


async def test_missing_token_rejected(client, server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_ALLOW_DYNAMIC_REGISTRATION", True)
    resp = await client.post("/oauth/register", json={"client_name": "x", "scope": "mcp:product"})
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers


async def test_wrong_scope_token_rejected(client, server, clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_ALLOW_DYNAMIC_REGISTRATION", True)
    srv, _ = server
    await _seed_client(
        clean_db, "product-discovery", "topsecret", ["client_credentials"], ["agent:invoke"], ["ecommerce-agents"]
    )
    await srv.client_store.load(clean_db)
    token = await _mint_token(srv, "product-discovery", "topsecret", "agent:invoke")

    resp = await client.post(
        "/oauth/register",
        json={"client_name": "x", "scope": "mcp:product"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


async def test_valid_registration_full_round_trip(client, server, clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_ALLOW_DYNAMIC_REGISTRATION", True)
    srv, _ = server
    await _seed_client(
        clean_db,
        "auth-admin",
        "admin-secret",
        ["client_credentials"],
        ["client:register"],
        ["ecommerce-auth-server"],
    )
    await srv.client_store.load(clean_db)
    reg_token = await _mint_token(srv, "auth-admin", "admin-secret", "client:register")

    resp = await client.post(
        "/oauth/register",
        json={"client_name": "Third-Party MCP Client", "scope": "mcp:product"},
        headers={"Authorization": f"Bearer {reg_token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["client_id"].startswith("ext-")
    assert body["scope"] == "mcp:product"
    assert body["grant_types"] == ["client_credentials"]
    assert body["client_secret_expires_at"] == 0

    # 数据库哈希应匹配返回的明文密钥。
    row = await clean_db.fetchrow(
        "SELECT client_secret_hash, allowed_audiences FROM oauth_clients WHERE client_id = $1", body["client_id"]
    )
    assert row is not None
    assert bcrypt.checkpw(body["client_secret"].encode(), row["client_secret_hash"].encode())
    assert list(row["allowed_audiences"]) == ["mcp-product"]

    # 完整往返：新注册客户端可立即向同一授权服务器
    # 申请真实、范围正确的令牌。
    await srv.client_store.load(clean_db)
    new_token = await _mint_token(srv, body["client_id"], body["client_secret"], "mcp:product")
    assert new_token


async def test_non_registrable_scope_rejected(client, server, clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_ALLOW_DYNAMIC_REGISTRATION", True)
    srv, _ = server
    await _seed_client(
        clean_db,
        "auth-admin",
        "admin-secret",
        ["client_credentials"],
        ["client:register"],
        ["ecommerce-auth-server"],
    )
    await srv.client_store.load(clean_db)
    reg_token = await _mint_token(srv, "auth-admin", "admin-secret", "client:register")

    resp = await client.post(
        "/oauth/register",
        json={"client_name": "x", "scope": "agent:invoke"},
        headers={"Authorization": f"Bearer {reg_token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client_metadata"


async def test_missing_client_name_rejected(client, server, clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_ALLOW_DYNAMIC_REGISTRATION", True)
    srv, _ = server
    await _seed_client(
        clean_db,
        "auth-admin",
        "admin-secret",
        ["client_credentials"],
        ["client:register"],
        ["ecommerce-auth-server"],
    )
    await srv.client_store.load(clean_db)
    reg_token = await _mint_token(srv, "auth-admin", "admin-secret", "client:register")

    resp = await client.post(
        "/oauth/register",
        json={"scope": "mcp:product"},
        headers={"Authorization": f"Bearer {reg_token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client_metadata"


async def test_bad_grant_types_rejected(client, server, clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_ALLOW_DYNAMIC_REGISTRATION", True)
    srv, _ = server
    await _seed_client(
        clean_db,
        "auth-admin",
        "admin-secret",
        ["client_credentials"],
        ["client:register"],
        ["ecommerce-auth-server"],
    )
    await srv.client_store.load(clean_db)
    reg_token = await _mint_token(srv, "auth-admin", "admin-secret", "client:register")

    resp = await client.post(
        "/oauth/register",
        json={"client_name": "x", "scope": "mcp:product", "grant_types": ["authorization_code"]},
        headers={"Authorization": f"Bearer {reg_token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client_metadata"
