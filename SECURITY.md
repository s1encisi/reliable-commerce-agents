# 安全策略

## 受支持的版本

| 版本 | 是否支持 |
|---------|-----------|
| 1.1.x   | 是 |
| 1.0.x   | 仅安全修复 |
| < 1.0   | 否 |

## 报告漏洞

**请不要为安全问题开公开 issue。**

请使用 GitHub 的私密漏洞报告通道：
[报告漏洞](https://github.com/s1encisi/reliable-commerce-agents/security/advisories/new)。
在修复发布之前，该报告只在您与维护者之间可见。

有帮助的信息包括：鉴权模式（`AUTH_MODE=local` 还是 `oauth`）、是否启用了 MCP，
以及您能给出的最小复现步骤。

预计会在几天内给出确认。这是一个个人开源项目，不是有资金支持的产品 ——
没有付费的分诊轮值，也没有赏金。

## 范围

这是一个**演示与教学仓库**。它的目的是展示多智能体系统如何组织，按发布状态而言
并未做生产加固。以下一些刻意的取舍在生产中会是错的，但在这里不算漏洞：

- `.env.example` 与 `.env.minimal` 附带占位密钥。只要 `ENVIRONMENT` 不是
  `development`，`shared/config.py` 就会拒绝它们。
- 默认的 `AUTH_MODE=local` 用智能体之间共享的密钥自行签发 JWT。
  `AUTH_MODE=oauth` 才是更贴近真实场景的路径。
- 护栏默认是「观察并记录」（`GUARDRAILS_FAIL_OPEN=true`）而非拦截，
  因为误报率尚未在多种环境下测量过。`GUARDRAILS_BLOCK_ON_INJECTION=true`
  可开启拦截。
- `docker-compose.yml` 把 Postgres 与 Redis 绑定到 localhost，并使用众所周知
  的开发凭据。

**属于**本范围的是：任何破坏代码所声明不变量的行为。例如租户或用户隔离被绕过、
提示词注入击穿了一个已开启的控制、审批门可被跳过、幂等键未能阻止重复退款，
或 `user_email` 作用域校验可被规避。

## 处理方式

确认的问题会在 `main` 上修复、发布补丁版本，并在
[CHANGELOG.md](CHANGELOG.md) 中记录。除非您希望匿名，否则会注明贡献者。

维护者：aria（GitHub: [@s1encisi](https://github.com/s1encisi)）。
