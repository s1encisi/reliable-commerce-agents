"""授权服务器 RSA 密钥初始化与 JWKS 测试。

通过 clean_db 使用真实隔离 PostgreSQL，不访问模型。
"""

from __future__ import annotations

import logging

import pytest

from auth_server import keys
from shared.config import settings

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_key_cache():
    keys.reset_cache_for_tests()
    yield
    keys.reset_cache_for_tests()


async def test_ensure_active_key_generates_on_first_boot(clean_db):
    kid, key = await keys.ensure_active_key(clean_db)

    assert kid
    assert key.is_private

    row = await clean_db.fetchrow("SELECT COUNT(*) AS n FROM oauth_signing_keys WHERE is_active = TRUE")
    assert row["n"] == 1


async def test_ensure_active_key_is_idempotent(clean_db):
    kid1, key1 = await keys.ensure_active_key(clean_db)
    keys.reset_cache_for_tests()  # 绕过进程缓存，强制重新从数据库读取。
    kid2, key2 = await keys.ensure_active_key(clean_db)

    assert kid1 == kid2
    assert key1.as_pem(private=True) == key2.as_pem(private=True)

    count = await clean_db.fetchval("SELECT COUNT(*) FROM oauth_signing_keys")
    assert count == 1


async def test_get_jwks_returns_public_key_only(clean_db):
    kid, _key = await keys.ensure_active_key(clean_db)
    document = await keys.get_jwks(clean_db)

    assert "keys" in document
    assert len(document["keys"]) == 1
    jwk = document["keys"][0]
    assert jwk["kid"] == kid
    assert jwk["kty"] == "RSA"
    assert "n" in jwk and "e" in jwk
    assert "d" not in jwk  # 不得泄露私钥指数。


async def test_unencrypted_key_at_rest_warns(clean_db, monkeypatch, caplog):
    monkeypatch.setattr(settings, "AUTH_SIGNING_KEY_ENCRYPTION_KEY", "")
    with caplog.at_level(logging.WARNING):
        await keys.ensure_active_key(clean_db)
    assert any("unencrypted_key_at_rest" in rec.getMessage() for rec in caplog.records)


async def test_key_round_trips_through_encryption(clean_db, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_SIGNING_KEY_ENCRYPTION_KEY", "x" * 32)
    kid1, key1 = await keys.ensure_active_key(clean_db)
    keys.reset_cache_for_tests()
    kid2, key2 = await keys.ensure_active_key(clean_db)

    assert kid1 == kid2
    assert key1.as_pem(private=True) == key2.as_pem(private=True)
