"""开发环境的确定性客户端密钥派生。

用 OAUTH_SEED_KEY 与 client_id 计算相同密钥，种子脚本和服务无需
额外交换秘密即可一致。生产环境应逐服务显式设置 OAUTH_CLIENT_SECRET，
不能依赖开发派生方式，详见 docs/security-guide.md。
"""

from __future__ import annotations

import hmac
from hashlib import sha256


def derive_client_secret(seed_key: str, client_id: str) -> str:
    """计算 HMAC-SHA256(seed_key, client_id)，返回十六进制字符串。"""
    return hmac.new(seed_key.encode(), client_id.encode(), sha256).hexdigest()
