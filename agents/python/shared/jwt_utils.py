"""JWT 创建、校验与密码哈希工具。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from shared.config import settings

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 7


def hash_password(password: str) -> str:
    """使用 bcrypt 对密码计算哈希。"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """用 bcrypt 哈希验证密码。"""
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(
    email: str,
    role: str,
    user_id: str,
    expires_delta: timedelta | None = None,
) -> str:
    """创建带签名的 JWT 访问令牌。"""
    expire = datetime.now(UTC) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload = {
        "sub": email,
        "role": role,
        "user_id": user_id,
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def create_refresh_token(email: str) -> str:
    """创建带签名的 JWT 刷新令牌。"""
    expire = datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": email,
        "exp": expire,
        "type": "refresh",
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """解码并校验 JWT；失败时抛出 jwt.InvalidTokenError。"""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
