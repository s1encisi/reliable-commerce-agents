# MCP 集成

可靠电商多智能体平台以可独立发布的 Python 包形式提供两个独立的 MCP 服务器。
它们通过 [Model Context Protocol](https://modelcontextprotocol.io)（MCP，模型上下文协议）的
streamable HTTP 传输暴露商品与库存数据，因此任何兼容 MCP 的客户端都能消费它们，
而无需了解本代码库的任何细节。

## 这展示了什么

默认架构下，专业智能体通过 `asyncpg` 直接访问 PostgreSQL：

```
专业智能体（MAF）
  → @tool 函数
  → asyncpg
  → PostgreSQL
```

启用 MCP 后，同一批智能体改为调用运行中的 MCP 服务器：

```
专业智能体（MAF）
  → MCPStreamableHTTPTool（MAF 内置的 MCP 客户端）
  → Streamable HTTP（MCP 协议）
  → MCP 服务器（FastMCP + asyncpg）
  → PostgreSQL
```

两种模式下智能体的行为 —— 提示词、路由、中间件、护栏 —— 完全一致。
只有数据访问层发生变化。

## MCP 服务器

| 服务器 | 端口 | 业务域 | 包 |
|--------|------|--------|---------|
| `mcp-product` | 9000 | 商品检索、详情、对比、热门、价格历史 | `packages/mcp-product` |
| `mcp-inventory` | 9001 | 库存量、仓库、运费、承运商 | `packages/mcp-inventory` |

两者都使用 [FastMCP](https://github.com/modelcontextprotocol/python-sdk)，并在 `/mcp` 暴露
MCP streamable HTTP 传输。MAF 的 `MCPStreamableHTTPTool` 连接到该端点。

## 启用 MCP 模式

### 1. 启动 MCP 服务器

```bash
# 与基础设施一起启动 MCP 服务器
docker compose --profile mcp --profile agents up
```

或在本地开发时直接运行：

```bash
cd agents/python

# 商品 MCP 服务器，监听 :9000
uv run uvicorn ecommerce_mcp_product.server:app --port 9000 --reload &

# 库存 MCP 服务器，监听 :9001
uv run uvicorn ecommerce_mcp_inventory.server:app --port 9001 --reload &
```

### 2. 设置环境变量

```bash
MCP_ENABLED=true
MCP_PRODUCT_SERVER_URL=http://localhost:9000/mcp    # Docker 内为 http://mcp-product:9000/mcp
MCP_INVENTORY_SERVER_URL=http://localhost:9001/mcp  # Docker 内为 http://mcp-inventory:9001/mcp
```

### 3. 重启专业智能体

`product-discovery` 与 `inventory-fulfillment` 在启动时读取 `MCP_ENABLED`，并据此选择对应的工具集。
无需改动代码。

## OAuth 2.1 资源服务器模式（可选）

默认情况下 MCP 服务器不做鉴权 —— 任何能访问 `:9000`/`:9001` 的人都可以调用工具。
设置 `MCP_AUTH_ENABLED=true`（需同时满足 `AUTH_MODE=oauth` 与 `MCP_ENABLED=true`）后，
每个服务器会按 [RFC 9728](https://datatracker.ietf.org/doc/html/rfc9728) 变成 OAuth 2.1 资源服务器，
并针对同一个自托管授权服务器（Authorization Server，AS）做校验 —— 该 AS 也用于用户登录与
智能体间调用，无需外部身份提供方。

### 本地运行

```bash
# 完整服务栈，oauth 模式，MCP 已启用且受保护
AUTH_MODE=oauth MCP_ENABLED=true MCP_AUTH_ENABLED=true \
  docker compose --profile agents --profile mcp up --build

# 灌入种子数据，然后重启 auth-server，使其内存中的客户端注册表
# 能加载到种子写入的 oauth_clients 行（它只在启动时加载一次）
docker compose --profile seed run --rm seeder
docker compose restart auth-server
```

### 各服务器的差异

- 一份内置的 `JwksTokenVerifier`
  （`packages/mcp-product/src/ecommerce_mcp_product/auth.py`、
  `packages/mcp-inventory/.../auth.py` —— 刻意**不**在两个包之间共享，也不与主应用的
  `shared/oauth/verifier.py` 共享，因为它们是可独立发布的 uv workspace 成员）
  通过 `PyJWKClient` 对 `AUTH_SERVER_JWKS_URL` 校验 bearer JWT：
  签名、签发方（`AUTH_SERVER_ISSUER`）、受众（`mcp-product` / `mcp-inventory`），
  以及必需 scope（`mcp:product` / `mcp:inventory`）。
- `FastMCP(token_verifier=..., auth=AuthSettings(issuer_url=..., resource_server_url=..., required_scopes=[...]))`
  —— MCP Python SDK 会自动挂载 `GET /.well-known/oauth-protected-resource/mcp`，
  并把 `POST /mcp` 包进 `RequireAuthMiddleware`。未鉴权或 scope 不正确的调用会得到 `401`，
  并附带符合规范形状的请求头：
  `WWW-Authenticate: Bearer error="invalid_token", error_description="...", resource_metadata="http://.../.well-known/oauth-protected-resource/mcp"`。
- 每个服务器都显式以 `host="0.0.0.0"` 提供服务。**易踩的坑**：只要 `host` 保持默认的
  `"127.0.0.1"`，FastMCP 就会自动启用 DNS 重绑定 Host 头保护，只把
  `localhost`/`127.0.0.1`/`::1` 加入白名单 —— 这会静默地让 Docker 网络上的每一次真实调用
  （例如 `http://mcp-product:9000/mcp`）返回 `421`，无论是否开启鉴权。两个 `server.py`
  都已修复该问题；不要移除显式的 `host` 参数。
- 当 `MCP_AUTH_ENABLED=false` 时，Dockerfile/compose 的健康检查请求 `/mcp`；
  当它为 `true` 时，改为请求自动挂载且无需鉴权的
  `/.well-known/oauth-protected-resource/mcp` —— 一旦开启鉴权，`/mcp` 总是返回 `401`，
  因此健康检查的目标也必须随之切换。

### 专业智能体如何获取它的资源令牌

当 `MCP_AUTH_ENABLED=true` 时，`product_discovery/agent.py` / `inventory_fulfillment/agent.py`
会向 `MCPStreamableHTTPTool` 传入一个 `header_provider`（MAF 官方文档中用于附加按请求请求头的机制）：

```python
mcp_product = MCPStreamableHTTPTool(
    name="product-mcp",
    url=settings.MCP_PRODUCT_SERVER_URL,
    header_provider=mcp_header_provider(settings.MCP_PRODUCT_REQUIRED_SCOPE, settings.MCP_PRODUCT_AUDIENCE),
)
```

`header_provider` 是**同步**调用的，且调用发生在已经运行的事件循环内部 —— 它自身无法执行
`client_credentials` 授权。每个专业智能体的异步启动钩子会先预热一次令牌缓存
（`await acquire_service_token(scope, audience)`）；随后 header provider 只做一次同步的、
仅读缓存的操作（`get_cached_service_token`），并附加 `Authorization: Bearer <token>`。
面向 `mcp:product` 的令牌无法通过 `mcp-inventory` 的鉴权，反之亦然 ——
每个服务器独立校验自己的受众。

### 在 oauth 模式下接入外部 MCP 客户端（例如 MCP Inspector）

通用 OAuth 2.1 客户端会走标准的受保护资源发现流程：

1. `GET http://localhost:9000/.well-known/oauth-protected-resource/mcp` → `{"resource": "...", "authorization_servers": ["http://localhost:8090/"], "scopes_supported": ["mcp:product"], ...}`
2. 发现授权服务器自身的元数据：`GET http://localhost:8090/.well-known/oauth-authorization-server`
3. 用种子客户端凭据（`scripts/seed.py::OAUTH_CLIENTS` —— 例如 `product-discovery`；
   开发密钥可用 `derive_client_secret(OAUTH_SEED_KEY, client_id)` 推导，
   生产环境则设置显式的 `OAUTH_CLIENT_SECRET`）从 `token_endpoint` 获取令牌
   （`client_credentials` 授权，scope 为 `mcp:product`）
4. 带上 `Authorization: Bearer <token>` 调用 `POST /mcp`

### 作为第三方 MCP 客户端获取凭据（动态注册）

上面的第 3 步假设你是一方预置客户端。当授权服务器运营方选择开启时，
真正的外部 MCP 客户端也可以自行注册（RFC 7591）：

1. 运营方在 auth-server 上设置 `AUTH_ALLOW_DYNAMIC_REGISTRATION=true`（默认关闭）。
2. 运营方用种子写入的 `auth-admin` 客户端，通过 `client_credentials` 获取一个带
   `client:register` scope 的令牌，并把得到的**注册令牌**通过带外方式交给第三方
   （这不是客户端自己能发现的东西）。
3. 客户端带上该 bearer 令牌和形如 `{"client_name": "...", "scope": "mcp:product"}` 的请求体
   调用 `POST /oauth/register`，拿回一对 `client_id`/`client_secret`（仅展示一次）——
   可立即用于上面的第 3 步。

注册被刻意收窄：只允许 `client_credentials` 授权，且只能申请两个 MCP 读取 scope
（`mcp:product`、`mcp:inventory`）—— 永远不能申请 `agent:invoke`、`api:chat`
或 `client:register` 本身。关于那个不太直观的实现细节（注册端点在进程内完成 bearer 令牌校验，
而不走其他资源服务器使用的 JWKS-over-HTTP 路径 —— 因为单 worker 服务器在自己的请求处理器里
去拉取自己的 JWKS 会发生死锁），见 `docs/security-guide.md` 的「已知问题」一节。

### 开关关闭时（`MCP_AUTH_ENABLED=false`，默认值）

两个 MCP 服务器的行为与该功能引入之前完全一致 —— 没有任何鉴权面，
并由 `tests/test_mcp_oauth_integration.py::test_mcp_auth_disabled_is_unchanged_regression_guard`
做逐字节的回归保护。

## 用 MCP Inspector 检视

[MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) 是用于测试 MCP 服务器的
交互式工具。在服务器运行状态下：

```bash
# 检视商品服务器
npx @modelcontextprotocol/inspector http://localhost:9000/mcp

# 检视库存服务器
npx @modelcontextprotocol/inspector http://localhost:9001/mcp
```

你可以借此浏览工具模式（schema）、调用单个工具，并查看原始的 MCP 协议消息。

## 智能体的工具选择逻辑

在 `product_discovery/agent.py` 与 `inventory_fulfillment/agent.py` 中：

```python
from agent_framework._mcp import MCPStreamableHTTPTool
from shared.config import settings

def create_product_discovery_agent() -> Agent:
    if settings.MCP_ENABLED:
        mcp_product = MCPStreamableHTTPTool(
            name="product-mcp",
            url=settings.MCP_PRODUCT_SERVER_URL,
            description="Product catalog data via MCP",
        )
        # 依赖用户上下文的工具（语义检索、价格历史）保持本地 ——
        # 它们依赖 pgvector / 不会传递到 MCP 服务器的 ContextVars。
        tools = [mcp_product, semantic_search, find_similar_products, ...]
    else:
        tools = AGENT_TOOLS  # 直连 asyncpg 的 @tool 函数

    return Agent(client=..., tools=tools, ...)
```

`MCPStreamableHTTPTool` 是 MAF 内置的 MCP 客户端。智能体初始化时，它会调用 MCP 服务器的
工具列表端点、发现可用工具，并把它们像原生 `@tool` 函数一样暴露给 LLM。
LLM 分辨不出二者的差别。

## 工具覆盖范围

并非所有工具都迁移到了 MCP。需要用户身份上下文（由认证中间件设置的 ContextVars）的工具，
或本平台特有的工具（语义向量检索、`place_backorder`），即使在 MCP 模式下也仍保持为直连的
`@tool` 函数。MCP 服务器覆盖的是真正可移植的纯数据访问工具。

| 工具 | MCP 模式 | 直连模式 |
|------|----------|-------------|
| `search_products` | product-mcp 服务器 | asyncpg `@tool` |
| `get_product_details` | product-mcp 服务器 | asyncpg `@tool` |
| `compare_products` | product-mcp 服务器 | asyncpg `@tool` |
| `get_trending_products` | product-mcp 服务器 | asyncpg `@tool` |
| `get_price_history` | product-mcp 服务器 | asyncpg `@tool` |
| `semantic_search` | 直连 `@tool`（pgvector） | asyncpg `@tool` |
| `find_similar_products` | 直连 `@tool`（pgvector） | asyncpg `@tool` |
| `check_stock` | inventory-mcp 服务器 | asyncpg `@tool` |
| `get_warehouse_availability` | inventory-mcp 服务器 | asyncpg `@tool` |
| `estimate_shipping` | inventory-mcp 服务器 | asyncpg `@tool` |
| `compare_carriers` | inventory-mcp 服务器 | asyncpg `@tool` |
| `get_restock_schedule` | inventory-mcp 服务器 | asyncpg `@tool` |
| `get_tracking_status` | 直连 `@tool` | asyncpg `@tool` |
| `place_backorder` | 直连 `@tool` | asyncpg `@tool` |

## 包结构

每个 MCP 服务器都是 `agents/python/packages/` 下独立的 Python 包：

```
agents/python/packages/
  mcp-product/
    pyproject.toml          # name = "ecommerce-mcp-product"
    src/ecommerce_mcp_product/
      server.py             # FastMCP 服务器 + ASGI 应用
    tests/
  mcp-inventory/
    pyproject.toml          # name = "ecommerce-mcp-inventory"
    src/ecommerce_mcp_inventory/
      server.py
    tests/
```

两者都是 `agents/python` uv workspace 的成员。一份 `uv.lock` 覆盖整个 workspace；
MCP 包共享已解析的依赖，无需重复锁定。

## 独立发布某个服务器

```bash
cd agents/python

# 构建 wheel + sdist
uv build --package ecommerce-mcp-product
uv build --package ecommerce-mcp-inventory

# 发布到 PyPI（或私有仓库）
uv publish dist/ecommerce_mcp_product-*.whl
uv publish dist/ecommerce_mcp_inventory-*.whl
```

发布之后，任何 MCP 客户端都可以安装并运行该服务器，而无需本仓库的其余部分：

```bash
pip install ecommerce-mcp-product
DATABASE_URL=postgresql://... ecommerce-mcp-product   # 在 :9000 上启动
```

## 新增一个 MCP 服务器

1. 创建一个新的 workspace 包：

```bash
mkdir -p agents/python/packages/mcp-<domain>/src/ecommerce_mcp_<domain>
```

2. 参照现有包编写 `pyproject.toml`（名称为 `ecommerce-mcp-<domain>`，
   依赖为 `mcp[cli]`、`asyncpg`、`uvicorn`，并配置控制台脚本入口点）。

3. 用 FastMCP 编写 `server.py`：

```python
from mcp.server.fastmcp import FastMCP
from typing import Annotated

mcp = FastMCP("my-domain-mcp", lifespan=_lifespan)

@mcp.tool()
async def my_tool(param: Annotated[str, "Description"]) -> dict:
    ...

app = mcp.streamable_http_app()  # 供 uvicorn 使用的 ASGI 入口点
```

4. 在 workspace 根 `pyproject.toml` 中注册该包：

```toml
[tool.uv.workspace]
members = ["packages/mcp-product", "packages/mcp-inventory", "packages/mcp-<domain>"]
```

5. 运行 `uv lock` 更新共享的 lock 文件。

6. 在 `docker-compose.yml` 的 `mcp` 档位下，用 `Dockerfile.mcp` 添加一个服务。

7. 把配置变量加入 `shared/config.py` 与 `.env.example`。

8. 把 `MCPStreamableHTTPTool` 接入相关的智能体工厂。

## 从外部 MCP 客户端使用

由于这些是标准 MCP 服务器，任何兼容 MCP 的客户端都能连接：

```json
// Claude Desktop —— claude_desktop_config.json
{
  "mcpServers": {
    "ecommerce-product": {
      "command": "ecommerce-mcp-product",
      "env": { "DATABASE_URL": "postgresql://..." }
    },
    "ecommerce-inventory": {
      "command": "ecommerce-mcp-inventory",
      "env": { "DATABASE_URL": "postgresql://..." }
    }
  }
}
```

```python
# LangGraph / LangChain
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "product": {"url": "http://localhost:9000/mcp", "transport": "streamable_http"},
    "inventory": {"url": "http://localhost:9001/mcp", "transport": "streamable_http"},
})
```

## 相关内容

- [`docs/architecture.md`](architecture.md) —— 完整系统架构
- [`docs/telemetry.md`](telemetry.md) —— OpenTelemetry + Langfuse 可观测性
- [`docs/maf-best-practices.md`](maf-best-practices.md) —— 所有智能体共用的 MAF 模式
