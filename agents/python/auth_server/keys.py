"""自托管 auth-server 的 RSA 签名密钥引导与 JWKS 服务。

首次启动时（``oauth_signing_keys`` 中没有活跃行），会生成并持久化一对新的
RSA 密钥——公开 JWK 加上一份私有 PEM，在设置了
``AUTH_SIGNING_KEY_ENCRYPTION_KEY`` 时静态加密（开发环境之外为必需，见
``docs/security-guide.md``）。之后每次启动都复用已有的活跃密钥，以便已签发
的令牌继续可校验。轮换（插入新的活跃密钥，同时把旧密钥保留在 JWKS 中直到
其最长寿的令牌过期）是已记录在案的后续工作，此处未实现——目前始终恰好只有
一个活跃密钥。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging

import asyncpg
from cryptography.fernet import Fernet, InvalidToken
from joserfc.jwk import RSAKey

from shared.config import settings

logger = logging.getLogger(__name__)

_active_kid: str | None = None
_active_key: RSAKey | None = None


def _fernet_key_from(secret: str) -> bytes:
    """把任意长度的密钥拉伸为 32 字节的 urlsafe-base64 Fernet 密钥。"""
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _encrypt_private_pem(pem: bytes) -> bytes:
    key_material = settings.AUTH_SIGNING_KEY_ENCRYPTION_KEY
    if not key_material:
        logger.warning(
            "auth_server.unencrypted_key_at_rest — AUTH_SIGNING_KEY_ENCRYPTION_KEY is "
            "unset; the RSA private key is being stored in plaintext. Set it before "
            "running this service outside development (see docs/security-guide.md)."
        )
        return pem
    return Fernet(_fernet_key_from(key_material)).encrypt(pem)


def _decrypt_private_pem(blob: bytes) -> bytes:
    key_material = settings.AUTH_SIGNING_KEY_ENCRYPTION_KEY
    if not key_material:
        return blob
    try:
        return Fernet(_fernet_key_from(key_material)).decrypt(blob)
    except InvalidToken:
        # 以明文存储，因为创建这一行时加密密钥尚未设置
        # （例如一个开发库后来才配了真实密钥）。把这个
        # 二进制块当作原始 PEM 处理，而不是让启动失败。
        return blob


def reset_cache_for_tests() -> None:
    """清空进程内密钥缓存。仅测试用。"""
    global _active_kid, _active_key
    _active_kid, _active_key = None, None


async def ensure_active_key(pool: asyncpg.Pool) -> tuple[str, RSAKey]:
    """返回 ``(kid, private_key)`` 对，首次启动时引导生成一个。

    幂等且进程内缓存：首次之后的重复调用返回同一个缓存密钥，不访问数据库。
    """
    global _active_kid, _active_key
    if _active_key is not None and _active_kid is not None:
        return _active_kid, _active_key

    row = await pool.fetchrow("SELECT kid, private_pem_enc FROM oauth_signing_keys WHERE is_active = TRUE LIMIT 1")
    if row is not None:
        pem = _decrypt_private_pem(bytes(row["private_pem_enc"]))
        key = RSAKey.import_key(pem)
        key.ensure_kid()
        _active_kid, _active_key = row["kid"], key
        logger.info("auth_server.signing_key_loaded kid=%s", _active_kid)
        return _active_kid, _active_key

    key = RSAKey.generate_key(settings.AUTH_RSA_KEY_SIZE, private=True)
    key.ensure_kid()
    kid = key.kid
    pem = key.as_pem(private=True)
    public_jwk = key.as_dict(private=False)

    await pool.execute(
        """INSERT INTO oauth_signing_keys (kid, alg, public_jwk, private_pem_enc, is_active)
           VALUES ($1, 'RS256', $2::jsonb, $3, TRUE)
           ON CONFLICT (kid) DO NOTHING""",
        kid,
        json.dumps(public_jwk),
        _encrypt_private_pem(pem),
    )
    _active_kid, _active_key = kid, key
    logger.info("auth_server.signing_key_generated kid=%s", kid)
    return _active_kid, _active_key


async def get_jwks(pool: asyncpg.Pool) -> dict:
    """返回公开的 JWKS 文档：活跃密钥加上所有尚未过期的退役密钥。"""
    rows = await pool.fetch("SELECT public_jwk FROM oauth_signing_keys WHERE is_active = TRUE OR retired_at IS NULL")

    def _as_dict(value):
        return json.loads(value) if isinstance(value, str) else value

    return {"keys": [_as_dict(row["public_jwk"]) for row in rows]}
