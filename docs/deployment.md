# 部署与开发

## 1. 前置条件

| 工具 | 最低版本 | 说明 |
|------|----------------|-------|
| Docker | 24+ | 桌面版或 Engine |
| Docker Compose | v2 | 随 Docker Desktop 捆绑；用 `docker compose version` 校验 |
| OpenAI API key **或** Azure OpenAI 凭据 | -- | 至少需要配置一个 LLM 提供方 |
| Git | 2.x | 用于克隆仓库 |

本地（非 Docker）开发的可选项：

| 工具 | 版本 | 说明 |
|------|---------|-------|
| Python | 3.12 | 仅在 Docker 之外运行智能体时需要 |
| uv | latest | Python 包管理器（`pip install uv` 或 `brew install uv`） |
| Node.js | 22+ | 仅在 Docker 之外运行前端时需要 |
| pnpm | 9+ | 通过 `corepack enable && corepack prepare pnpm@latest --activate` 启用 |

## 2. Docker Compose 架构

平台运行 11 个服务，组织为 4 个 profile 分组。没有 profile 的服务默认启动。

```mermaid
graph TB
    subgraph defaultGroup["Default (always start)"]
        style defaultGroup fill:#f8fafc,stroke:#e2e8f0,stroke-width:2px
        db["PostgreSQL 16 + pgvector<br/>:5432"]
        redis["Redis 7<br/>:6379"]
        jaeger["Jaeger<br/>:16686"]
    end

    subgraph seed["Profile: seed"]
        style seed fill:#f8fafc,stroke:#e2e8f0,stroke-width:2px
        seeder["Seeder<br/>(run once, exits)"]
    end

    subgraph agents["Profile: agents"]
        style agents fill:#f8fafc,stroke:#e2e8f0,stroke-width:2px
        orchestrator["Orchestrator<br/>:8080"]
        product["Product Discovery<br/>:8081"]
        order["Order Management<br/>:8082"]
        pricing["Pricing & Promotions<br/>:8083"]
        review["Review & Sentiment<br/>:8084"]
        inventory["Inventory & Fulfillment<br/>:8085"]
    end

    subgraph frontend_profile["Profile: frontend"]
        style frontend_profile fill:#f8fafc,stroke:#e2e8f0,stroke-width:2px
        frontend["Next.js Frontend<br/>:3000"]
    end

    seeder -->|depends_on| db
    orchestrator -->|depends_on| db
    orchestrator -->|depends_on| redis
    orchestrator -->|depends_on| jaeger
    product -->|depends_on| db
    product -->|depends_on| jaeger
    order -->|depends_on| db
    order -->|depends_on| jaeger
    pricing -->|depends_on| db
    pricing -->|depends_on| jaeger
    review -->|depends_on| db
    review -->|depends_on| jaeger
    inventory -->|depends_on| db
    inventory -->|depends_on| jaeger
    frontend -->|depends_on| orchestrator

    orchestrator -- "A2A Protocol" --> product
    orchestrator -- "A2A Protocol" --> order
    orchestrator -- "A2A Protocol" --> pricing
    orchestrator -- "A2A Protocol" --> review
    orchestrator -- "A2A Protocol" --> inventory
```

## 3. 服务 Profile

Docker Compose profile 控制哪些服务启动。没有 profile 的服务总是启动。

| Profile | 服务 | 使用场景 |
|---------|----------|-------------|
| *（无）* | `db`、`redis`、`jaeger` | 总是启动 —— 基础设施基线 |
| `seed` | `seeder` | 用示例数据填充数据库。运行一次后退出。 |
| `agents` | `orchestrator`, `product-discovery`, `order-management`, `pricing-promotions`, `review-sentiment`, `inventory-fulfillment` | 6 个 AI 智能体微服务 |
| `frontend` | `frontend` | Next.js Web 应用 |

**用法示例：**

```bash
# Infrastructure only
docker compose up -d

# Infrastructure + agents
docker compose --profile agents up -d

# Everything
docker compose --profile agents --profile frontend up -d

# Run the seeder
docker compose --profile seed run --rm seeder
```

## 4. dev.sh 脚本

`scripts/dev.sh` 脚本是启动开发环境的推荐方式。它处理构建顺序、健康检查、种子数据填充，并在就绪时打印摘要。

**在 Windows 上请使用 `scripts/dev.ps1`** —— 一个行为完全相同的 PowerShell 脚本：相同的 profile、相同的顺序、相同的健康检查关卡，以及以 PowerShell 形式表达的相同参数（`--clean` → `-Clean`，`--seed-only` → `-SeedOnly`，`--infra-only` → `-InfraOnly`）。它也能在 PowerShell 7 下的 macOS 和 Linux 上运行，不过在那里 `dev.sh` 是更地道的选择。本节记录的所有内容对两者都适用。

### 参数

| 参数 | 说明 |
|------|-------------|
| *（无参数）* | 完整重建：停止已有容器、构建所有镜像、启动基础设施、填充数据库种子数据、启动全部智能体、启动前端 |
| `--clean` | 核选项：移除所有容器、卷（含数据库数据）和孤儿资源，然后执行完整重建 |
| `--seed-only` | 确保基础设施在运行，然后针对已有数据库重新运行种子数据生成器。在模式变更后或需要重置示例数据时很有用。 |
| `--infra-only` | 只启动 `db`、`redis` 和 `jaeger`。不启动智能体和前端。当你用 `uvicorn` 在本地运行智能体时使用。 |
| `--help`、`-h` | 打印用法信息 |

### 脚本流程

```mermaid
flowchart TD
    start([dev.sh]) --> check_prereqs{Docker +<br/>Compose installed?}
    check_prereqs -- No --> fail_exit([Exit with error])
    check_prereqs -- Yes --> check_env{.env exists?}
    check_env -- No --> copy_env[Copy .env.example to .env]
    check_env -- Yes --> parse_flags
    copy_env --> parse_flags

    parse_flags --> is_clean{--clean?}
    is_clean -- Yes --> clean[docker compose down -v<br/>Remove volumes + orphans]
    is_clean -- No --> is_seed_only

    clean --> is_seed_only{--seed-only?}
    is_seed_only -- Yes --> start_infra_seed[Start db + redis + jaeger]
    start_infra_seed --> health_infra_seed[Wait for health checks]
    health_infra_seed --> run_seeder_only[Run seeder]
    run_seeder_only --> exit_seed([Exit])

    is_seed_only -- No --> stop_existing[Stop existing containers]
    stop_existing --> build[Build agent images]
    build --> start_infra[Start db + redis + jaeger]
    start_infra --> health_infra[Wait for health checks]
    health_infra --> run_seeder[Run database seeder]

    run_seeder --> is_infra_only{--infra-only?}
    is_infra_only -- Yes --> summary_infra[Print infrastructure summary]
    summary_infra --> exit_infra([Exit])

    is_infra_only -- No --> start_agents[Start all 6 agents]
    start_agents --> health_agents[Wait for agent /health endpoints]
    health_agents --> start_frontend[Start frontend]
    start_frontend --> health_frontend[Wait for frontend :3000]
    health_frontend --> summary_full[Print full summary]
    summary_full --> done([Done])
```

## 5. 环境配置

把 `.env.example` 复制为 `.env` 并配置：

```bash
cp .env.example .env
```

### LLM 提供方

| 变量 | 必填 | 默认值 | 说明 |
|----------|----------|---------|-------------|
| `LLM_PROVIDER` | 是 | `openai` | LLM 提供方：`openai`、`azure` 或 `replay`（回放录制的 fixture，无需凭据 —— 见 `shared/replay_client.py`） |

### OpenAI 配置

| 变量 | 必填 | 默认值 | 说明 |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | 是（当 `openai` 时） | -- | 你的 OpenAI API 密钥。对不校验密钥的本地服务器（Ollama、LM Studio）而言，任何非空字符串都可以 |
| `LLM_MODEL` | 否 | `gpt-4.1` | 对话补全模型名称 |
| `LLM_BASE_URL` | 否 | 未设置（使用 `api.openai.com`） | 仅在 `LLM_PROVIDER=openai` 时生效。改为把 `OpenAIChatClient` 指向任何 OpenAI 兼容端点 —— GitHub Models、OpenRouter、vLLM、LM Studio，或本地 Ollama 服务器（`http://localhost:11434/v1`）。在选择本地模型之前，请先看 `tutorials/00-setup/README.md` 中的示例，以及一个关于工具调用支持的易错点。 |

### Azure OpenAI 配置

| 变量 | 必填 | 默认值 | 说明 |
|----------|----------|---------|-------------|
| `AZURE_OPENAI_ENDPOINT` | 是（当 `azure` 时） | -- | Azure OpenAI 资源端点 URL |
| `AZURE_OPENAI_KEY` | 是（当 `azure` 时） | -- | Azure OpenAI API 密钥 |
| `AZURE_OPENAI_DEPLOYMENT` | 是（当 `azure` 时） | -- | 用于对话补全的部署名称 |
| `AZURE_OPENAI_API_VERSION` | 否 | `2024-12-01-preview` | Azure OpenAI API 版本 |

### 嵌入（Embeddings）

| 变量 | 必填 | 默认值 | 说明 |
|----------|----------|---------|-------------|
| `EMBEDDING_MODEL` | 否 | `text-embedding-3-small` | 用于商品语义检索（pgvector）的 OpenAI 嵌入模型 |
| `AZURE_EMBEDDING_DEPLOYMENT` | 否（当 `azure` 时） | -- | 用于嵌入的 Azure OpenAI 部署 |

### 数据库

| 变量 | 必填 | 默认值 | 说明 |
|----------|----------|---------|-------------|
| `POSTGRES_DB` | 否 | `ecommerce_agents` | PostgreSQL 数据库名 |
| `POSTGRES_USER` | 否 | `ecommerce` | PostgreSQL 用户 |
| `POSTGRES_PASSWORD` | 否 | `ecommerce_secret` | PostgreSQL 密码 |
| `DATABASE_URL` | 否 | `postgresql://ecommerce:ecommerce_secret@db:5432/ecommerce_agents` | 完整连接字符串。在 Docker 中，`db` 解析到 Compose 服务。本地开发请使用 `localhost`。 |

### Redis

| 变量 | 必填 | 默认值 | 说明 |
|----------|----------|---------|-------------|
| `REDIS_URL` | 否 | `redis://redis:6379` | Redis 连接字符串。在 Docker 中，`redis` 解析到 Compose 服务。 |

### 认证

| 变量 | 必填 | 默认值 | 说明 |
|----------|----------|---------|-------------|
| `JWT_SECRET` | 是 | `change-me-...` | 用于签名 JWT 的密钥。生成方式：`python -c "import secrets; print(secrets.token_hex(32))"` |
| `AGENT_SHARED_SECRET` | 否 | `agent-internal-shared-secret` | 用于智能体间认证的共享密钥（编排器到专业智能体） |

### 遥测

| 变量 | 必填 | 默认值 | 说明 |
|----------|----------|---------|-------------|
| `OTEL_ENABLED` | 否 | `true` | 启用/禁用 OpenTelemetry 导出 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 否 | `http://jaeger:4317` | OTLP 接收端端点（Jaeger） |
| `OTEL_SERVICE_NAME` | 否 | `ecommerce.orchestrator` | 上报给 OTLP 的服务名。每个智能体在 `docker-compose.yml` 中覆盖此项。 |

### 通用

| 变量 | 必填 | 默认值 | 说明 |
|----------|----------|---------|-------------|
| `ENVIRONMENT` | 否 | `development` | 运行时环境标识 |
| `AGENT_REGISTRY` | 否 | *（JSON 映射）* | 把智能体名称映射到其内部 Docker 网络 URL 的 JSON 对象。编排器用它来发现专业智能体。 |
| `ORCHESTRATOR_URL` | 否 | `http://localhost:8080` | 前端服务端 `/api/*` 代理转发到的目标。在运行时读取，因此同一个镜像可在任何环境工作。浏览器永远看不到它。 |

## 6. Dockerfile 架构

### 智能体 Dockerfile（多目标）

全部 6 个智能体共用同一个 `agents/Dockerfile`。构建目标由两个 `ARG` 值控制：

| ARG | 默认值 | 用途 |
|-----|---------|---------|
| `AGENT_NAME` | `orchestrator` | 要复制的 Python 包目录（例如 `product_discovery`、`order_management`） |
| `AGENT_PORT` | `8080` | 智能体监听的端口 |

**构建流程：**

1. **基础镜像**：`python:3.12-slim`，含系统依赖（`gcc`、`libpq-dev`、`curl`）
2. **安装 uv**：从官方 `ghcr.io/astral-sh/uv` 镜像复制
3. **创建非 root 用户**：`agent` 用户与用户组
4. **安装 Python 依赖**：`uv sync --no-dev --no-install-project`（带缓存的层 —— 仅在 `pyproject.toml` 变化时重跑）
5. **复制共享库**：所有智能体共用的 `shared/` 目录
6. **复制智能体模块**：只复制该智能体对应的 `${AGENT_NAME}/` 目录
7. **切换到非 root 用户**
8. **健康检查**：`curl -f http://localhost:${AGENT_PORT}/health`
9. **入口点**：`uv run uvicorn ${AGENT_NAME}.main:app --host 0.0.0.0 --port ${AGENT_PORT}`

seeder 服务复用 orchestrator 镜像，但覆盖了 `command` 以运行 `uv run python -m scripts.seed`，并把 `scripts/` 目录以只读卷挂载。

### 前端 Dockerfile（多阶段）

`web/Dockerfile` 使用 3 阶段构建以获得最小的生产镜像：

| 阶段 | 基础镜像 | 用途 |
|-------|------|---------|
| `deps` | `node:22-alpine` | 用 `pnpm install --frozen-lockfile` 安装依赖 |
| `builder` | `node:22-alpine` | 构建 Next.js（`pnpm build`）。不会编译进任何后端地址 —— 见上文 `ORCHESTRATOR_URL` |
| `runner` | `node:22-alpine` | 仅含 standalone 产物的生产运行时。非 root 的 `nextjs` 用户。 |

最终镜像只包含 standalone 服务器、静态资源和 public 目录 —— 不含 `node_modules` 或源代码。

## 7. 本地开发

为了更快迭代，把基础设施放在 Docker 中运行，智能体/前端在本地运行。

**启动基础设施：**

```bash
./scripts/dev.sh --infra-only
```

**运行单个智能体：**

```bash
cd agents
export DATABASE_URL=postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents
export REDIS_URL=redis://localhost:6379
export OPENAI_API_KEY=sk-your-key
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317

uv run uvicorn product_discovery.main:app --port 8081 --reload
```

**运行编排器**（需要 `AGENT_REGISTRY` 指向本地端口）：

```bash
cd agents
export AGENT_REGISTRY='{"product-discovery":"http://localhost:8081","order-management":"http://localhost:8082","pricing-promotions":"http://localhost:8083","review-sentiment":"http://localhost:8084","inventory-fulfillment":"http://localhost:8085"}'

uv run uvicorn orchestrator.main:app --port 8080 --reload
```

**运行前端：**

```bash
cd web
pnpm install
pnpm dev
```

前端在 `http://localhost:3000` 启动，并把其 `/api/*` 调用转发到 `http://localhost:8080`（可通过 `ORCHESTRATOR_URL` 配置）。

**在本地填充数据库种子数据：**

```bash
cd agents
DATABASE_URL=postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents \
  uv run python -m scripts.seed
```

**在本地生成嵌入：**

```bash
cd agents
DATABASE_URL=postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents \
OPENAI_API_KEY=sk-your-key \
  uv run python -m scripts.generate_embeddings
```

## 8. 端口映射

| 端口 | 服务 | 协议 | 说明 |
|------|---------|----------|-------|
| 3000 | Next.js 前端 | HTTP | 面向浏览器的 UI |
| 5432 | PostgreSQL | TCP | 已启用 pgvector |
| 6379 | Redis | TCP | 会话缓存（限流尚未实现 —— 已规划） |
| 8080 | 编排器（客户支持智能体） | HTTP | API 网关 —— 所有用户请求从这里进入 |
| 8081 | 商品发现智能体 | HTTP | A2A 端点，由编排器调用 |
| 8082 | 订单管理智能体 | HTTP | A2A 端点，由编排器调用 |
| 8083 | 定价与促销智能体 | HTTP | A2A 端点，由编排器调用 |
| 8084 | 评论与情感分析智能体 | HTTP | A2A 端点，由编排器调用 |
| 8085 | 库存与履约智能体 | HTTP | A2A 端点，由编排器调用 |
| 16686 | Jaeger UI | HTTP | OpenTelemetry 分布式追踪界面 |
| 4317 | Jaeger OTLP 接收端 | gRPC | OTLP gRPC 接收端口 |
| 4318 | Jaeger OTLP 接收端 | HTTP | OTLP HTTP 接收端口 |

## 9. 健康检查

### Docker 健康检查

所有服务都在 `docker-compose.yml` 或 Dockerfile 中定义了内置健康检查：

| 服务 | 检查 | 间隔 | 超时 | 重试次数 |
|---------|-------|----------|---------|---------|
| PostgreSQL | `pg_isready -U ecommerce` | 5s | 3s | 5 |
| Redis | `redis-cli ping` | 5s | 3s | 5 |
| 所有智能体 | `curl -f http://localhost:{PORT}/health` | 15s | 5s | 3（30s 启动期） |

### 手动验证

```bash
# Check all container statuses
docker compose --profile agents --profile frontend ps

# Check individual agent health
curl http://localhost:8080/health   # Orchestrator
curl http://localhost:8081/health   # Product Discovery
curl http://localhost:8082/health   # Order Management
curl http://localhost:8083/health   # Pricing & Promotions
curl http://localhost:8084/health   # Review & Sentiment
curl http://localhost:8085/health   # Inventory & Fulfillment

# Check PostgreSQL connectivity
docker compose exec db pg_isready -U ecommerce

# Check Redis
docker compose exec redis redis-cli ping

# View OpenTelemetry traces
open http://localhost:16686
```

## 10. 故障排查

常见问题 —— 端口冲突、缺少 API 密钥、数据为空、Jaeger 追踪、构建失败以及前端错误 —— 请参见 **[troubleshooting.md](./troubleshooting.md)**。

部署特有的问题见下文。

### 模式变更后数据库卷过期

**原因**：`init.sql` 只在卷首次创建时运行。卷已存在后再修改模式不会有任何效果。

**修复**：销毁卷并重新初始化：

```bash
./scripts/dev.sh --clean
```

这会移除 `pgdata` 卷、根据 `init.sql` 重建数据库并重新填充种子数据。

### 种子数据生成器报 "relation does not exist"

**原因**：同样的根因 —— 数据库卷是在最新 `init.sql` 之前创建的。

**修复**：`./scripts/dev.sh --clean`。

### Docker 构建缓慢 —— 依赖层缓存失效

**原因**：修改 `pyproject.toml` 会触发完整的 `uv sync` 重装。

**修复**：Dockerfile 的结构保证依赖层独立于源代码。如果你只改了 `.py` 文件，`uv sync` 会复用缓存。除非要增删依赖，否则避免改动 `pyproject.toml`。

### 运行 dev.sh 时出现 "Permission denied"

```bash
chmod +x scripts/dev.sh
```

---

## 相关文档

- [`docs/troubleshooting.md`](./troubleshooting.md) —— 运行时问题（端口冲突、LLM 错误、数据库连接、Jaeger 追踪）
- [`docs/architecture.md`](./architecture.md) —— 系统概览与智能体模式
- [项目 README](../README.md)
