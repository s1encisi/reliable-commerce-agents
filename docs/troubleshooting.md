# 故障排查

本地运行服务栈时的常见问题。用 `./scripts/dev.sh` 启动全部服务
（Windows 上为 `./scripts/dev.ps1` —— 下面每个 `dev.sh --flag` 都有对应的 `dev.ps1 -Flag`：
`--clean` → `-Clean`，`--seed-only` → `-SeedOnly`，`--infra-only` → `-InfraOnly`）
（另见 [deployment.md](./deployment.md)）。

## Docker 构建在 Python 智能体处失败（`agent-framework` 依赖解析）

现象：`uv sync` 期间出现大量 `agent-framework … cannot be used` /
`agent-framework-azure-ai-search` 冲突。原因：针对线上 PyPI 重新解析预发布的 MAF 依赖图。
**修复已包含在 Dockerfile 中** —— 它通过 `uv sync --frozen` 从已提交的
`agents/python/uv.lock` 同步。如果你遇到该问题，请确认 `uv.lock` 存在，并做一次干净构建：

```bash
./scripts/dev.sh --clean        # 清空数据卷 + 重建
# 或者
docker compose build --no-cache orchestrator
```

若要主动刷新依赖：`cd agents/python && uv lock`（提交新的 lock 文件）。

## 端口已被占用（5432 / 6379 / 8080 / 3000 / 16686）

被其他服务栈（或宿主机上的 Postgres/Redis）占用了。找到并停掉它：

```bash
lsof -nP -iTCP:5432 -sTCP:LISTEN     # 谁在监听
docker compose down                   # 停止本服务栈
```

Compose 服务使用的端口为 5432（Postgres）、6379（Redis）、8080（编排器）、
8081–8085（专业智能体）、3000（前端）、16686（Jaeger）。

## 对话返回错误 / 「encountered an issue」

LLM 从不使用模拟实现。请在仓库根目录的 `.env` 中填入真实密钥：

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4.1
```

（Azure 则使用 `AZURE_OPENAI_*` 系列变量）。随后重启编排器与各智能体。

## 登录失败并提示「Missing Authorization header」

你访问的是错误的后端，或者编排器指向了另一个数据库。确认 `:8080` 是电商编排器
（`curl localhost:8080/health` → `{"service":"orchestrator"}`），并确认数据库已灌入种子数据
（`./scripts/dev.sh --seed-only`）。

## 公开店铺不显示商品 / 跳转到登录页

商品浏览与对话通过 `optional_auth` 支持匿名访问。若匿名
`GET /api/products` 返回 401，说明编排器镜像早于该改动 —— 请重建：
`docker compose up -d --build --no-deps orchestrator`。

## 数据库连接被拒 / 数据为空

Postgres 尚未就绪或未灌入种子数据。先 `docker compose ps`（db 是否 healthy？），
然后执行 `./scripts/dev.sh --seed-only`。种子脚本是确定性的（`random.seed(42)`）。

## 向量嵌入缺失（语义检索为空）

```bash
cd agents/python && uv run python -m scripts.generate_embeddings
```

## `products.search_vector does not exist`

现象：所有商品检索都失败，智能体回复「there was an error
retrieving results from the database」。智能体日志中出现
`UndefinedColumnError: column p.search_vector does not exist`。

原因：全文检索给 `products` 增加了一个生成的 `tsvector` 列。
`docker/postgres/init.sql` 只在数据目录**为空**时执行，因此在该改动之前创建的数据库
永远不会获得这一列。

不丢数据的修复方式：

```bash
docker compose exec -T db psql -U ecommerce -d ecommerce_agents <<'SQL'
ALTER TABLE products ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(brand, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(description, '')), 'C')
    ) STORED;
CREATE INDEX IF NOT EXISTS idx_products_search ON products USING GIN (search_vector);
SQL
```

Postgres 会为所有既有数据行回填该列，因此无需重新灌入种子数据。用下面的命令验证：

```bash
docker compose exec -T db psql -U ecommerce -d ecommerce_agents \
  -c "SELECT count(*) AS products, count(search_vector) AS indexed FROM products;"
```

另一种方式是 `./scripts/dev.sh --clean` 从 `init.sql` 重建 —— 更简单，但会丢掉全部本地数据。

## UI 改动没有在运行中的服务栈里生效

前端是一个已构建的容器。请重建它：
`docker compose up -d --build --no-deps frontend`（或在空闲端口上运行 `cd web && pnpm dev`）。

## Jaeger 界面为空（没有追踪）

打开 http://localhost:16686。确认 `OTEL_ENABLED` 已开启，且 OTLP 端点指向 Jaeger 容器。
见 [telemetry.md](./telemetry.md)。
