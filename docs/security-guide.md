# 安全指南

面向可靠电商多智能体平台的纵深防御安全架构。本指南涵盖威胁模型、护栏（guardrail）技术栈、身份认证与身份传递、数据访问控制，以及加固路线图。

各智能体的状态快照参见 `docs/agent-audit-matrix.md`。

---

## 威胁模型

### 攻击面

```
AUTH_MODE=local (default):
Browser  ──JWT (HS256)──►  Orchestrator (:8080)  ──X-Agent-Secret──►  Specialists (:8081–8085)  ──►  MCP servers (:9000–9001, unauthenticated)
                                  │                                           │
                            PostgreSQL / pgvector                        Redis (cache)

AUTH_MODE=oauth (optional — self-hosted AS, no external IdP):
Browser  ──JWT (RS256)──►  Orchestrator  ──Bearer <agent:invoke>──►  Specialists  ──Bearer <mcp:product|mcp:inventory>──►  MCP servers
                                  │                                           │                                                │
                                  └──────────────────── all three validate against the AS's JWKS (:8090) ─────────────────────┘
```

平台有三条主要的信任边界：

1. **浏览器 → 编排器（orchestrator）** —— 未认证的 HTTP。攻击者可以是任意互联网客户端。`local` 模式：自签发的 HS256 JWT。`oauth` 模式：编排器以资源所有者密码凭据（Resource Owner Password Credentials，ROPC）授权模式向自建授权服务器（Authorization Server，AS）代理请求，并转发其签发的 RS256 访问令牌 / 刷新令牌——AS 是凭据的唯一权威来源。
2. **编排器 → 专业智能体（specialist agent）** —— 内网。`local` 模式：静态共享密钥。`oauth` 模式：每次调用都由 AS 签发一个短期的客户端凭据（client-credentials）服务令牌（`aud=ecommerce-agents`、`scope=agent:invoke`）——共享密钥会被直接拒绝，而不仅仅是弃用。攻击者可以是已失陷的容器：它知晓共享密钥、能够铸造或窃取令牌，但仍可能携带任意伪造的转发身份请求头。
3. **专业智能体 → MCP 服务器** —— 内网，仅在 `MCP_ENABLED=true` 时可访问。`local` / 默认模式：不认证。`oauth` 模式且 `MCP_AUTH_ENABLED=true` 时：每个 MCP 服务器都是一个 OAuth 2.1 资源服务器——每个资源使用各自独立的客户端凭据令牌（`scope=mcp:product` / `mcp:inventory`），独立获取与缓存，若被提交给错误的服务器则会被拒绝。

### 威胁类别

| 类别 | 攻击向量 | 已采取的防御措施 |
|----------|--------|-----------------|
| **直接提示词注入** | 攻击者构造一条包含 `ignore previous instructions` 或伪造轮次前缀的用户消息 | `InjectionDetectionChatMiddleware`（观察模式）+ `grounding-rules.yaml`（提示词层） |
| **存储型 / 间接注入** | 嵌入商品描述、评论正文或订单备注中的对抗性文本，以工具调用结果的形式重新进入模型 | `OutputSanitizationMiddleware` 在重新注入前将其中和 |
| **角色提权** | 攻击者在消息正文中自称管理员 / 卖家 | `grounding-rules.yaml` 的角色限定规则 + 特权工具上的 `@requires_role` 装饰器 + SQL 归属过滤 |
| **身份伪造（智能体间）** | 已失陷的调用方在持有有效密钥或服务令牌的同时，转发任意 `x-user-email` / `x-user-role` 请求头 | `_identity_anomaly()` 校验 + `GUARDRAILS_STRICT_IDENTITY` 开关（在 `local` 与 `oauth` 两种模式下都会检查） |
| **JWT 伪造 / 重放** | 攻击者提交已过期、被篡改或受众不匹配的 Bearer 令牌 | `local`：`decode_token()` 的 HS256 校验 + `jwt.ExpiredSignatureError` / `jwt.InvalidTokenError`。`oauth`：基于 AS 的 JWKS 校验 RS256 签名 + 签发方 + 受众 + 有效期（`RS256Verifier`）；为某个受众 / 作用域（如 `api:chat`）铸造的令牌在其他任何地方（如 `agent:invoke`、`mcp:product`）都会被拒绝 |
| **跨资源令牌复用（MCP）** | 为某个 MCP 服务器（如 `mcp:product`）签发的作用域令牌被重放到另一个服务器（`mcp:inventory`）或智能体间通路上 | 每个资源服务器各自校验自身的 `aud` + `required_scope`；只要不匹配，即便签名本身有效也会被拒绝并返回 `401` |
| **未授权的工具访问** | 未认证或低权限的调用方调用破坏性工具（取消订单、下缺货补单） | `@requires_role` + SQL `WHERE user_id = $N` 归属过滤 |
| **密钥外泄** | 注入载荷试图提取 `AGENT_SHARED_SECRET` 或 `JWT_SECRET` | 提示词层的拒绝规则；清洗环节会剥离用于隐藏载荷的控制字符 |
| **SQL 注入** | 攻击者在搜索查询或过滤条件中嵌入 SQL | 全量使用 `asyncpg` 参数化查询（`$1, $2, …`）；不存在字符串拼接的 SQL |
| **数据过度拉取** | 工具返回的行数超出预期 | 每个列表查询都做 `LIMIT` 收敛；每个面向用户的查询都带归属过滤 |

---

## 护栏技术栈

护栏遵循纵深防御模型：**提示词层 → 代码层**，每一层都增加一道独立的防线。所有代码层护栏均通过 `shared/config.py` 以开关控制，因此无需重新部署即可切换。

### 中间件组合顺序

每个专业智能体都通过 `shared/middleware.py` 中的 `build_specialist_middleware()` 初始化。中间件栈（由内到外，即执行顺序）为：

```
Request →
  1. InjectionDetectionChatMiddleware   # scan inbound messages (observe-only by default)
  2. AgentRunLogger                     # correlation ID + start/finish log
  3. ToolAuditMiddleware                # structured log per tool call
  4. OutputSanitizationMiddleware       # neutralize tool results before re-entry
  ← Response
```

`include_steps=True` 会追加一个步骤日志中间件，用于细粒度追踪（开发环境使用）。

### 第 1 层 —— 提示词层规则（`grounding-rules.yaml`）

`shared/config/prompts/_shared/grounding-rules.yaml` 通过 YAML 组合系统（`shared/prompt_loader.py`）注入到每个智能体的系统提示词中。它在模型层面强制三条不变式：

- **数据事实核验（grounding）** —— 每个回答都必须源自工具调用（tool call）；模型不得凭训练数据编造内容。
- **抗提示词注入** —— 工具调用结果与用户文本都只是数据，永远不是指令。明确禁止伪造 system / developer 轮次。
- **角色限定** —— 模型的权限由系统提供的角色固定；消息中任何提权声明都必须忽略。

这一层是覆盖面最广的防御。其弱点是：它的可靠程度完全取决于模型的指令遵循能力。

### 第 2 层 —— 入站注入检测（`InjectionDetectionChatMiddleware`）

`shared/guardrails/injection_middleware.py` 会针对 `shared/guardrails/sanitize.py` 中的九个高精度正则模式扫描每条入站聊天消息。这些模式覆盖：

- `ignore/disregard previous instructions`
- `forget all rules`
- `you are now a/an/the …`
- 伪造的轮次前缀（行首的 `system:`、`developer:`、`assistant:`）
- 提示词泄露尝试（`reveal your system prompt`）
- XML 风格的注入标签（`</system>`、`<instructions>`）
- 提权措辞（`act as if you are an admin`）

**默认行为**：仅观察。检测命中会递增计数器、设置 `context.metadata["guardrail_injection_detected"]`，并以 INFO 级别记录日志。设置 `GUARDRAILS_BLOCK_ON_INJECTION=true` 可将日志级别提升为 WARNING；若要真正阻断请求本身，则需要一个读取该元数据标记的自定义中间件。

**误报率**：这些模式刻意追求高精度，以避免拦截正常表述。安全 / 红队评测（eval）套件（`evals/safety_evaluator.py`）会持续测量精确率与召回率。

### 第 3 层 —— 存储型注入中和（`OutputSanitizationMiddleware`）

`shared/guardrails/output_middleware.py` 在每次工具调用之后执行。若工具名出现在 `SANITIZE_TOOLS`（`shared/guardrails/config.py`）中，`neutralize_value()` 会在结果返回给模型之前就地重写它。

`neutralize_value()`（`shared/guardrails/sanitize.py`）做两件事：

1. **剥离（Strip）** —— 移除 C0 控制字符（TAB/LF/CR 除外）、DEL、零宽 Unicode 标记、行 / 段分隔符以及 BOM。这些都是常见的隐藏载荷载体。
2. **钝化（Defang）** —— 将模式命中替换为 `[neutralized]`，同时保留结构与长度，以便下游分析（虚假评论检测、情感分析）仍能看出此处原本有内容。

只有结果中携带用户生成自由文本的工具才会被列入 `SANITIZE_TOOLS`。每个条目可选地指定字段白名单，从而确保结构化字段（价格、SKU）不会被破坏。

**失败行为**：由 `GUARDRAILS_FAIL_OPEN` 控制（默认 `true`）。发生意外异常时，会返回原始结果并记录错误。在高安全要求的部署中，可设置 `GUARDRAILS_FAIL_OPEN=false` 以采用失败关闭（fail-closed）行为。

### 第 4 层 —— 工具级角色授权（`@requires_role`）

`shared/guardrails/roles.py` 提供两种形式：

```python
# Decorator — place directly under @tool so MAF introspects the original signature
@tool(name="get_my_products", description="…")
@requires_role("seller", "admin")
async def get_my_products(…): …

# Guard clause — for retrofitting existing tools without re-ordering decorators
denied = ensure_role("seller", "admin", tool="get_my_products")
if denied:
    return denied
```

无论 `roles` 参数如何，`admin` 始终被允许（超级用户）。身份信息从 `current_user_role` ContextVar 读取，该变量由 `AgentAuthMiddleware` 设置，绝不通过函数参数传递。

`shared/tools/seller_tools.py` 中的全部四个工具（`get_seller_products`、`update_product_price`、`get_seller_analytics`、`get_payout_summary`）都带有 `@requires_role("seller", "admin")`。剩余待办事项参见审计矩阵。

### 配置开关

| 开关 | 默认值 | 作用 |
|------|---------|--------|
| `GUARDRAILS_ENABLED` | `true` | 总开关。`false` 会禁用所有代码层护栏。 |
| `GUARDRAILS_OUTPUT_SANITIZATION` | `true` | 启用 / 禁用 `OutputSanitizationMiddleware`。 |
| `GUARDRAILS_BLOCK_ON_INJECTION` | `false` | 将注入检测的日志级别从 INFO 提升为 WARNING。 |
| `GUARDRAILS_FAIL_OPEN` | `true` | 清洗出错时返回原始结果而非抛出异常。 |
| `GUARDRAILS_STRICT_IDENTITY` | `false` | 拒绝转发身份格式错误的智能体间调用。 |
| `GUARDRAILS_INJECTION_PROVIDER` | `regex` | 为未来的 Azure AI Content Safety 集成预留。 |

---

## 身份认证与身份传递

### 外部请求（浏览器 → 编排器）

**`AUTH_MODE=local`（默认）：**

```
Authorization: Bearer <JWT>
```

`AgentAuthMiddleware`（`shared/auth.py`）使用 `decode_token()`（`shared/jwt_utils.py`）校验令牌：

1. 使用 `JWT_SECRET` 进行 HS256 解码
2. 校验 `type == "access"` 声明（刷新令牌会被拒绝）
3. 从载荷中提取 `sub`（邮箱）、`role` 与 `user_id`
4. 设置三个 ContextVar：`current_user_email`、`current_user_role`、`current_session_id`

路径 `/health` 与 `/.well-known/agent-card.json` 会跳过认证。

JWT 签名使用 `bcrypt` 处理密码，使用 `PyJWT` 生成令牌。默认 `JWT_SECRET` 为 `change-me-in-production`——若启动时未覆盖该值，配置校验器会发出警告。

**`AUTH_MODE=oauth`：** 编排器的 `/api/auth/login` 与 `/api/auth/refresh` 路由不再在本地签发令牌，而是将请求代理至自建授权服务器（AS，`agents/python/auth_server/`）：

- **登录（Login）** 以机密型第一方客户端身份向 AS 转发 `password`（ROPC）授权请求——AS 会基于同一张 `users` 表重新校验 bcrypt 哈希，因此这里不会重复密码校验逻辑。AS 返回一个 RS256 访问令牌（`aud=ecommerce-orchestrator`、`scope=api:chat`、`role`/`user_id` 自定义声明）以及一个不透明刷新令牌。
- **刷新（Refresh）** 转发 `refresh_token` 授权请求。AS 不会轮换刷新令牌（`INCLUDE_NEW_REFRESH_TOKEN=False`）——这是有意为之，因为前端从不重新持久化轮换后的刷新令牌。
- `AgentAuthMiddleware` 的 Bearer 分支会基于 AS 发布的 JWKS（`RS256Verifier` + `JwksKeyProvider`）校验 RS256 令牌：签名、签发方、受众、有效期与必需作用域。
- OAuth 工作的 A–D 阶段均已交付，并在实际运行环境中完成验证。剩余事项——密钥轮换、RFC 7591 动态客户端注册以及审计矩阵——记录在 [`.claude/plans/remaining-work.md`](https://github.com/s1encisi/reliable-commerce-agents/blob/26f47c494dd6b371312593e82f066713f6f56e9c/.claude/plans/remaining-work.md) 中。

### 智能体间请求（编排器 → 专业智能体）

**`AUTH_MODE=local`（默认）：**

```
X-Agent-Secret: <AGENT_SHARED_SECRET>
X-User-Email:   alice@example.com
X-User-Role:    customer
X-Session-ID:   <session>
```

共享密钥用于认证调用方（证明它是平台内的智能体，而不是外部客户端）。转发的 `X-User-Email` 与 `X-User-Role` 请求头则在整条智能体链路中携带终端用户的身份。

**`AUTH_MODE=oauth`：** 共享密钥不只是被弃用，而是会被主动**拒绝**——携带 `X-Agent-Secret` 的请求会直接收到 401，而不会静默放行。取而代之的是：

```
Authorization: Bearer <AS-issued service token, aud=ecommerce-agents, scope=agent:invoke>
X-User-Email:  alice@example.com
X-User-Role:   customer
X-Session-ID:  <session>
```

调用方通过 `client_credentials` 授权获取该服务令牌（`shared/oauth/service_client.py::acquire_service_token`，按 `(scope, audience)` 缓存，并带 30 秒的刷新偏移）。该令牌证明调用方是合法的第一方智能体；终端用户的真实身份仍与 `local` 模式一样，通过转发 `X-User-*` 请求头传递。来自系统 / 健康检查的调用只携带服务令牌、不携带任何转发请求头，映射为 `role=system`。

无论哪种模式，`_identity_anomaly()` 都以相同方式校验转发的请求头：

- `role` 必须是 `customer`、`seller`、`admin`、`system` 之一
- `email` 必须包含 `@`（除非其为哨兵值 `system`）

若 `GUARDRAILS_STRICT_IDENTITY=true`，异常情况会返回 401，而不是仅记录一条警告日志。

### MCP 资源服务器认证（专业智能体 → MCP 服务器，可选）

仅在 `MCP_ENABLED=true` 时相关（此时专业智能体通过 MCP 而非直接访问数据库来获取商品 / 库存数据）。当 `MCP_AUTH_ENABLED=true` 时，每个 MCP 服务器都会按 [RFC 9728](https://datatracker.ietf.org/doc/html/rfc9728) 成为一个 OAuth 2.1 资源服务器：

- **Python**（`packages/mcp-product`、`packages/mcp-inventory`）：内嵌的 `JwksTokenVerifier`（`mcp.server.auth.provider.TokenVerifier`）通过 `PyJWKClient` 校验 Bearer JWT——受众为 `mcp-product`/`mcp-inventory`，必需作用域为 `mcp:product`/`mcp:inventory`。它被接入 `FastMCP(token_verifier=..., auth=AuthSettings(...))`；SDK 会自动挂载 `GET /.well-known/oauth-protected-resource/mcp`，并用 `RequireAuthMiddleware` 包裹 `POST /mcp`，校验失败时返回 `401` 以及符合规范的 `WWW-Authenticate: Bearer error="invalid_token", ..., resource_metadata="..."` 响应头。
- 调用 MCP 的专业智能体以与智能体间场景相同的方式获取自己的资源令牌，并按 `(scope, audience)` 分别缓存——`mcp:product` 令牌无法通过 `mcp-inventory` 服务器的认证，反之亦然；`agent:invoke` 的智能体间令牌也不能复用到任一 MCP 服务器上。
- `MCPStreamableHTTPTool` 的 `header_provider` 回调（MAF 文档中用于按请求附加认证头的机制）是在一个已在运行的事件循环内部**同步**调用的，因此它自身无法完成令牌获取。每个专业智能体的异步启动钩子会预先预热一次令牌缓存（`await acquire_service_token(...)`）；此后 `header_provider` 只做同步的纯缓存读取。
- 关闭该开关（`MCP_AUTH_ENABLED=false`，默认值）→ 两个 MCP 服务器的行为与该特性引入前完全一致——完全不暴露认证面。

### 通过 ContextVar 传递身份

`shared/context.py` 暴露三个 `contextvars.ContextVar` 对象：`current_user_email`、`current_user_role`、`current_session_id`。认证中间件在请求边界处设置它们；每个工具直接读取。身份信息从不通过函数参数层层传递。

因此，身份链路为：

```
HTTP request → AgentAuthMiddleware.dispatch()
             → ContextVar.set(email, role, session_id)
             → tool function
             → current_user_role.get()
             → @requires_role check / SQL WHERE clause
```

---

## 数据访问控制

### SQL 归属过滤

每个面向用户的查询都使用参数化的 `WHERE user_id = $N` 或 `WHERE u.email = $N` 子句。没有任何查询会返回属于其他用户的行。这一约束在查询层面强制执行——而不是在工具的 Python 逻辑中——因此无法被提示词注入绕过。

示例（所有查询都遵循此模式）：

```sql
SELECT * FROM orders
WHERE user_id = (SELECT id FROM users WHERE email = $1)
ORDER BY created_at DESC
LIMIT 20
```

### LIMIT 收敛

所有列表查询都带有显式的 `LIMIT`。接受用户传入 `limit` 参数的工具会将其收敛到上限：

```python
async def get_user_orders(limit: Annotated[int, "Max results"] = 10) -> list[dict]:
    effective_limit = min(limit, 50)  # caller cannot exceed 50
    …
```

### 参数化查询

所有数据库访问都使用 `asyncpg` 的参数化查询语法（`$1, $2, …`）。构建 SQL 时不使用任何字符串拼接。这在查询构建层消除了 SQL 注入。

---

## Azure AI Content Safety —— 可选集成

配置开关 `GUARDRAILS_INJECTION_PROVIDER` 为 [Azure AI Content Safety Prompt Shields](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/jailbreak-detection) 预留了集成点。当它被设为 `azure_content_safety` 时，注入检测流水线会在正则层之前调用 Prompt Shields API，从而提供一个由云端支持的机器学习分类器，具备更高的召回率与持续更新的微软模型。

**当前状态**：开关已接入，但 Azure 后端尚未实现——`shared/guardrails/azure_shield.py` 并不存在。设置 `GUARDRAILS_INJECTION_PROVIDER=azure_content_safety` 会在启动时被拒绝并报「not implemented」错误（`shared/config.py::_validate_injection_provider`），而不会静默回退到正则提供方。基于正则的提供方（`GUARDRAILS_INJECTION_PROVIDER=regex`）仍是唯一可用且受支持的路径。

**何时启用**：在大规模处理不可信终端用户的生产部署中，尤其是当评测套件显示正则层漏检了新式措辞时。该 API 会引入延迟（每请求约 100–200 毫秒）；建议以 `GUARDRAILS_BLOCK_ON_INJECTION` 作为灰度开关，避免在 rollout 期间因误报而拦截请求。在 `azure_shield.py` 落地之前，这仍属于规划中的能力。

**实现示意**（尚未合并）：

```python
# shared/guardrails/injection_middleware.py — proposed extension
if settings.GUARDRAILS_INJECTION_PROVIDER == "azure_content_safety":
    from shared.guardrails.azure_shield import check_prompt_shields
    flagged = await check_prompt_shields(messages)
else:
    flagged = any(contains_injection_markers(m.text) for m in messages)
```

---

## 生产加固检查清单

| 项目 | 配置 / 操作 |
|------|----------------|
| 轮换 `JWT_SECRET` | 设为 256 位随机值；存放于 Azure Key Vault |
| 轮换 `AGENT_SHARED_SECRET` | 采用相同的轮换节奏；通过 Managed Identity 或 Key Vault 引用注入 |
| 启用严格身份校验 | `GUARDRAILS_STRICT_IDENTITY=true` |
| 启用失败关闭式清洗 | `GUARDRAILS_FAIL_OPEN=false` |
| 评估注入阻断 | 在预发布环境测出误报率后再设置 `GUARDRAILS_BLOCK_ON_INJECTION=true` |
| 全链路启用 HTTPS | 在 AKS Ingress 处终止 TLS；Pod 之间不使用明文 HTTP |
| 网络策略 | 将专业智能体端口（8081–8085）限制为仅编排器 Pod 可访问 |
| 完成角色强制 | 为审计矩阵中的待办项补充 `@requires_role` |
| 启用自建 OAuth 服务器 | `AUTH_MODE=oauth`——用户登录由编排器代理 ROPC，A2A 与 MCP 使用客户端凭据服务令牌，RS256 签名通过 JWKS（每个 `kid` 仅一个活跃密钥，尚不支持自动轮换——见「已知问题」）；同时退役 `JWT_SECRET` 与 `AGENT_SHARED_SECRET`（会被直接拒绝，而非仅弃用）。从密钥存储中设置 `AUTH_SIGNING_KEY_ENCRYPTION_KEY` 与各服务的 `OAUTH_CLIENT_SECRET`；切勿使用开发默认值 `OAUTH_SEED_KEY`。剩余 OAuth 工作记录在 [`.claude/plans/remaining-work.md`](https://github.com/s1encisi/reliable-commerce-agents/blob/26f47c494dd6b371312593e82f066713f6f56e9c/.claude/plans/remaining-work.md) |
| 保护 MCP 服务器 | `MCP_AUTH_ENABLED=true`（需要同时设置 `MCP_ENABLED=true`）——两个 Python MCP 服务器都会基于授权服务器的 JWKS 校验其 RS256 Bearer 令牌（受众 + 作用域，每个服务器一个专属资源作用域），暴露 `.well-known/oauth-protected-resource`，并对未认证或作用域错误的调用返回 `401` + `WWW-Authenticate` |

---

## 已知问题（自建 OAuth 服务器，可选特性）

- **没有签名密钥轮换。** `auth_server/keys.py::ensure_active_key` 以幂等方式引导生成一对 RSA 密钥，并在进程生命周期内一直复用——既没有定时轮换，也没有多密钥重叠窗口。每个令牌上都会写入 `kid` 头，以便未来的轮换机制可以在不立即作废现存令牌的前提下向 JWKS 添加密钥，但当前没有任何代码会生成第二把密钥。
- **动态客户端注册（RFC 7591）需显式开启，且作用域受限。** `POST /oauth/register` 已存在（`auth_server/register.py` + `auth_server/main.py`），由 `AUTH_ALLOW_DYNAMIC_REGISTRATION` 控制（默认 `false`）。即便开启，注册也需要一个作用域为 `client:register` 的 Bearer 令牌（由种子客户端 `auth-admin` 通过 `client_credentials` 获取——该凭据与 `orchestrator` 更宽泛的信任范围相互隔离）。注册出的客户端被限制为只能使用 `client_credentials`，且只能使用两个 MCP 只读作用域（`mcp:product`、`mcp:inventory`）——该端点无法铸造出能够请求 `agent:invoke`、`api:chat` 或 `client:register` 本身的客户端。第一方服务仍来自 `scripts/seed.py` 中的静态列表；此机制仅覆盖第三方 MCP 消费方。
  - **一个只有对真实运行中的服务器做实测才能发现、单靠单元测试无法暴露的真实缺陷**：最初的实现复用了 `shared/oauth/verifier.py::RS256Verifier` 来校验注册用的 Bearer 令牌——也就是所有*其他*资源服务器都在使用的那个基于 HTTP 获取 JWKS 的校验器。这在实践中会死锁：AS 是单 worker 的 asyncio 进程，其自身的请求处理逻辑通过 HTTP 同步获取自己的 JWKS，会阻塞那个本应处理这条入站连接的同一个事件循环，导致每次都超时。其他资源服务器不会遇到这个问题，因为它们都不是在获取 JWKS 的同时处理*来自自己*的请求。修复方式是改为完全在进程内校验——AS 内存中本就持有自己的签名密钥（`auth_server/main.py::_verify_registration_token`），因此完全没有网络往返，只有本地签名校验加上手动的 `iss`/`aud`/`scope`/`exp` 声明检查（`joserfc` 的 `decode` 只校验签名，这一点与 PyJWT 的 `jwt.decode` 不同）。

## 相关文档

- `docs/agent-audit-matrix.md` —— 各智能体的安全状态与待办项
- [`docs/agent-quality.md`](agent-quality.md) —— 评测方法与红队套件
- [`docs/maf-best-practices.md`](maf-best-practices.md) —— 所有智能体通用的 MAF 惯用法
- [`docs/architecture.md`](architecture.md) —— 完整的系统架构
