"""自托管 OAuth2 授权服务器（AUTH_MODE=oauth）。

为用户登录（编排器代理的资源所有者密码凭据）、智能体间 A2A 调用以及 MCP
资源访问（客户端凭据）签发 RS256 访问/刷新令牌——不依赖外部身份提供方。
设计见 ``docs/security-guide.md``；剩余的 OAuth 工作见
``.claude/plans/remaining-work.md``。
"""

import os

# authlib 默认拒绝在非「安全」URI 上构建请求（它自己的定义是
# https:// 或 http://localhost）——见
# authlib.common.security.is_secure_transport。本平台在私有
# Docker/AKS 网络内以明文 HTTP 运行每个内部服务，包括这一个
# （目前没有 pod 间 TLS——见 docs/security-guide.md 加固清单中
# 的「Enable HTTPS everywhere」一行），因此这是 authlib 自己
# 有文档记载的逃生开关，而不是绕行手段。在包导入时设置，以便
# 无论跑在 uvicorn、docker-compose 还是 pytest 下都统一生效。
os.environ.setdefault("AUTHLIB_INSECURE_TRANSPORT", "1")
