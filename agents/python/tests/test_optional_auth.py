"""公开店铺可选认证测试：无令牌允许匿名，有效令牌返回身份，非法令牌返回 401。"""

from __future__ import annotations

import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import orchestrator.routes.legacy as routes_module
from orchestrator.routes import optional_auth
from shared.jwt_utils import create_access_token


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw})


async def test_anonymous_when_no_authorization_header():
    user = await optional_auth(_request())
    assert user["anonymous"] is True
    assert user["role"] == "anonymous"
    assert user["sub"] == ""
    assert user["user_id"] == ""


async def test_returns_payload_for_valid_token():
    token = create_access_token("alice@example.com", "customer", "u-123")
    user = await optional_auth(_request({"Authorization": f"Bearer {token}"}))
    assert user.get("anonymous") is not True
    assert user["sub"] == "alice@example.com"
    assert user["role"] == "customer"
    assert user["user_id"] == "u-123"


async def test_rejects_present_but_invalid_token():
    with pytest.raises(HTTPException) as exc:
        await optional_auth(_request({"Authorization": "Bearer not-a-jwt"}))
    assert exc.value.status_code == 401


class _StubVerifier:
    """RS256 校验器替身，仅验证 require_auth 的 oauth 分支。"""

    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error

    def decode(self, token, *, audience, required_scope=None):
        if self._error is not None:
            raise self._error
        return self._payload


async def test_oauth_mode_accepts_valid_token(monkeypatch):
    monkeypatch.setattr(routes_module.settings, "AUTH_MODE", "oauth")
    monkeypatch.setattr(
        routes_module,
        "get_token_verifier",
        lambda: _StubVerifier(payload={"sub": "alice@example.com", "role": "admin", "scope": "api:chat"}),
    )
    user = await optional_auth(_request({"Authorization": "Bearer whatever"}))
    assert user["sub"] == "alice@example.com"
    assert user["role"] == "admin"


async def test_oauth_mode_rejects_invalid_token(monkeypatch):
    monkeypatch.setattr(routes_module.settings, "AUTH_MODE", "oauth")
    monkeypatch.setattr(
        routes_module, "get_token_verifier", lambda: _StubVerifier(error=jwt.InvalidTokenError("bad token"))
    )
    with pytest.raises(HTTPException) as exc:
        await optional_auth(_request({"Authorization": "Bearer whatever"}))
    assert exc.value.status_code == 401


async def test_oauth_mode_rejects_expired_token(monkeypatch):
    monkeypatch.setattr(routes_module.settings, "AUTH_MODE", "oauth")
    monkeypatch.setattr(
        routes_module,
        "get_token_verifier",
        lambda: _StubVerifier(error=jwt.ExpiredSignatureError("expired")),
    )
    with pytest.raises(HTTPException) as exc:
        await optional_auth(_request({"Authorization": "Bearer whatever"}))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Token expired"


async def test_oauth_mode_anonymous_unchanged(monkeypatch):
    """没有 Authorization 时，应在调用校验器前直接走匿名路径。"""
    monkeypatch.setattr(routes_module.settings, "AUTH_MODE", "oauth")
    user = await optional_auth(_request())
    assert user["anonymous"] is True
