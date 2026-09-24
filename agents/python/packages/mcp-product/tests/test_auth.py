"""商品 MCP 服务的 OAuth 2.1 资源服务器模式（阶段 D）。

分两个层次：
- 纯 ``JwksTokenVerifier`` 单元测试（不涉及 ASGI、不涉及 FastMCP）—— 接受/
  拒绝的各种形态，与主应用的 ``test_rs256_verifier.py`` 约定保持一致：
  每个测试单独的 RSA 密钥对，JWKS 获取被 monkeypatch（不发起真实网络调用）。
- 针对独立构建的 ``FastMCP`` 实例（而非 ``server.py`` 中的模块级单例）的
  服务级测试，这样它们就不依赖 ``MCP_AUTH_ENABLED`` 在测试会话中任何地方
  首次导入 ``ecommerce_mcp_product.server`` 之前被设置 —— 否则导入顺序会
  让这些测试变成顺序相关的，因为该模块在导入时只读取一次该标志。
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

import jwt as pyjwt
import pytest
from joserfc.jwk import RSAKey
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

from ecommerce_mcp_product.auth import JwksTokenVerifier

ISSUER = "http://test-auth-server"
AUDIENCE = "mcp-product"
REQUIRED_SCOPE = "mcp:product"


@pytest.fixture
def keypair():
    key = RSAKey.generate_key(2048, private=True)
    key.ensure_kid()
    return key


@pytest.fixture
def verifier(keypair) -> JwksTokenVerifier:
    v = JwksTokenVerifier(
        jwks_url=f"{ISSUER}/.well-known/jwks.json",
        issuer=ISSUER,
        audience=AUDIENCE,
        required_scope=REQUIRED_SCOPE,
    )
    public_jwks = {"keys": [keypair.as_dict(private=False)]}
    v._jwks_client.fetch_data = lambda: public_jwks
    return v


def _make_token(keypair, *, aud=AUDIENCE, iss=ISSUER, scope=REQUIRED_SCOPE, exp_delta=3600, **extra):
    now = int(time.time())
    payload = {
        "iss": iss,
        "aud": aud,
        "exp": now + exp_delta,
        "iat": now,
        "sub": "test-client",
        "scope": scope,
        **extra,
    }
    return pyjwt.encode(payload, keypair.as_pem(private=True), algorithm="RS256", headers={"kid": keypair.kid})


# ─────────────────────── JwksTokenVerifier（纯单元测试） ───────────────────


async def test_verifier_accepts_valid_token(keypair, verifier):
    token = _make_token(keypair)
    access = await verifier.verify_token(token)
    assert access is not None
    assert access.client_id == "test-client"
    assert access.scopes == [REQUIRED_SCOPE]


async def test_verifier_rejects_wrong_audience(keypair, verifier):
    token = _make_token(keypair, aud="some-other-resource")
    assert await verifier.verify_token(token) is None


async def test_verifier_rejects_wrong_issuer(keypair, verifier):
    token = _make_token(keypair, iss="http://not-the-real-as")
    assert await verifier.verify_token(token) is None


async def test_verifier_rejects_missing_scope(keypair, verifier):
    token = _make_token(keypair, scope="some-other-scope")
    assert await verifier.verify_token(token) is None


async def test_verifier_accepts_when_required_scope_present_among_several(keypair, verifier):
    token = _make_token(keypair, scope=f"{REQUIRED_SCOPE} agent:invoke")
    access = await verifier.verify_token(token)
    assert access is not None
    assert REQUIRED_SCOPE in access.scopes


async def test_verifier_rejects_expired_token(keypair, verifier):
    token = _make_token(keypair, exp_delta=-10)
    assert await verifier.verify_token(token) is None


async def test_verifier_rejects_token_signed_by_unknown_key(verifier):
    other_key = RSAKey.generate_key(2048, private=True)
    other_key.ensure_kid()
    token = _make_token(other_key)
    assert await verifier.verify_token(token) is None


async def test_verifier_rejects_malformed_token(verifier):
    assert await verifier.verify_token("not-a-jwt") is None


# ─────────────────────── 服务级（FastMCP + auth） ───────────────────


@asynccontextmanager
async def _noop_lifespan(_server):
    yield


def _build_app(verifier: JwksTokenVerifier) -> FastMCP:
    mcp = FastMCP(
        "test-product-mcp",
        lifespan=_noop_lifespan,
        # 避开 FastMCP 默认 host 下的 DNS 重绑定自动保护 —— 它只把
        # localhost/127.0.0.1/::1 加入白名单（参见在 server.py 中应用的同一
        # 修复）—— 而 TestClient 默认的 Host 头与之都不匹配。
        host="0.0.0.0",
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=ISSUER,
            resource_server_url="http://localhost:9000/mcp",
            required_scopes=[REQUIRED_SCOPE],
        ),
    )

    @mcp.tool()
    def ping() -> str:
        return "pong"

    return mcp


def _client(verifier: JwksTokenVerifier) -> TestClient:
    return TestClient(_build_app(verifier).streamable_http_app())


_INIT_BODY = {
    "jsonrpc": "2.0",
    "method": "initialize",
    "id": 1,
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
_MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def test_unauthenticated_request_rejected(verifier):
    with _client(verifier) as client:
        r = client.post("/mcp", json=_INIT_BODY, headers=_MCP_HEADERS)
        assert r.status_code == 401
        assert "WWW-Authenticate" in r.headers
        assert "invalid_token" in r.headers["WWW-Authenticate"]


def test_protected_resource_metadata(verifier):
    with _client(verifier) as client:
        r = client.get("/.well-known/oauth-protected-resource/mcp")
        assert r.status_code == 200
        body = r.json()
        assert body["resource"] == "http://localhost:9000/mcp"
        assert body["authorization_servers"] == [f"{ISSUER}/"]
        assert REQUIRED_SCOPE in body["scopes_supported"]


def test_valid_token_authenticates(keypair, verifier):
    token = _make_token(keypair)
    with _client(verifier) as client:
        r = client.post(
            "/mcp",
            json=_INIT_BODY,
            headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200


def test_wrong_scope_token_rejected_at_server(keypair, verifier):
    token = _make_token(keypair, scope="mcp:inventory")
    with _client(verifier) as client:
        r = client.post(
            "/mcp",
            json=_INIT_BODY,
            headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401
