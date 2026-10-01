# 本地追踪与指标验收

此验收通过本地 OpenTelemetry Collector 接收真实 OTLP 导出，检查应用实际产生的追踪和指标。它复用[容器展示环境](local-showcase.md)，不调用模型、不接入支付，也不重置数据库。

## 核验范围

| 路径 | 证据要求 |
|---|---|
| 固定任务创建、规划、两次执行 | 实际 HTTP SERVER span → `task.*` span → asyncpg PostgreSQL span，检查同一 trace ID 内的真实 parent/span ID 关系 |
| 跨服务只读探针 | 明确标为合成探针的父跨度 → 自动插桩 HTTPX CLIENT span → 商品发现服务的 agent-card SERVER span |
| 任务指标 | `commerce.task.requests` 和 `commerce.task.duration` 的阶段计数覆盖实际请求；应用标签只有 `phase`、`status_class`，不放任务 UUID、用户或原文 |
| 内容过滤 | 唯一文字标记、Bearer 前缀和本次访问令牌不出现在导出内容中 |

跨服务探针读取 `/.well-known/agent-card.json`，用于验证 HTTP 上下文传播；它不是一次模型或业务工具执行。固定任务仍调用真实数据库，额外加入的文字标记保留为待人工核实约束，因此最终状态应为 `needs_review`，不能把 HTTP 200 解释为整个任务完成。

## 本次实测（2026-10-01）

[验收摘要](evaluation/local-telemetry-20261001.json)记录了实际任务与跨度关系：共接收 95 个 span；创建、规划和两次执行均有 HTTP SERVER → task → asyncpg SQL 父子链，四阶段分别包含 2、10、17、19 个 SQL span。独立 agent-card 只读探针也通过 HTTPX CLIENT → 下游 SERVER 的 parent ID 核验。

计数器与耗时直方图的实际样本数均为创建 1、规划 1、执行 2，Prometheus 导出存在。唯一文字标记、Bearer 前缀及该次访问令牌均未出现；认证日志中的合成邮箱有出现，报告保留了这一事实。未产生 GenAI 操作跨度。验收结束后已移除临时 Collector，六服务遥测关闭，默认九服务健康且仅发布本机 3020。

## 执行

先确保默认九个展示服务健康。官方 `opentelemetry-collector-contrib:0.123.0` 镜像需已在本机缓存，覆盖配置设置 `pull_policy: never`，不自动下载镜像。

```bash
source scripts/dev-env.sh
uv run --project agents/python --no-sync python scripts/verify_local_telemetry.py \
  --output .local/telemetry-check/report-r1.json \
  --capture-dir .local/telemetry-check/raw-r1
```

每次使用新的输出和采集目录，脚本不会覆盖已有证据。可选覆盖文件是 `docker-compose.telemetry-check.yml`，Collector 配置是 `docker/observability/local-verification.yml`。

运行时六个 Python 服务暂时启用 OTLP，并显式设置 `GENAI_CAPTURE_CONTENT=false`，禁用 HTTP 请求/响应头内容捕获。健康检查的追踪排除保持不变。Collector 的 OTLP、健康检查和 Prometheus 端点只位于 Compose 内部网络，没有新增宿主端口。

## 输出与恢复

报告记录任务 UUID、阶段 trace ID、带插桩来源的实际父子链、指标标签和计数、隐私检查、镜像/源码摘要及恢复状态。原始 traces、metrics、logs JSONL 和 Prometheus 文本保存在单独采集目录。脚本只把该目录下新建的 `collector-files` 子目录挂给 Collector；因此无须假设官方镜像自带可写的 `/tmp`，容器停止后证据仍保留。

认证日志可能保留合成账户邮箱，报告会单独指出；不能因为关闭模型内容捕获就宣称全部日志不含身份信息。原始采集不作为默认公开产物，公开说明以不含令牌和请求原文的摘要为准。

成功与失败路径都会先恢复应用的默认 `OTEL_ENABLED=false`，再收集导出并移除本次临时 Collector。恢复验证要求默认九服务健康，且仍仅发布 `127.0.0.1:3020`。原有订单、退货、审批数量必须保持不变，只新增一项合成验证任务。

如果进程被系统强制终止，应先保存残留 Collector 的 `/var/lib/otel-verification` 导出（也保留在本次采集目录的 `collector-files` 中），再手动恢复应用并停止该临时服务；不要删除展示数据卷：

```bash
# 必须指向需要保留的那次原始采集目录。
export LOCAL_TELEMETRY_EXPORT_DIR="$PWD/.local/telemetry-check/raw-r1/collector-files"
docker compose --env-file .env.showcase.local -f docker-compose.showcase.yml \
  up -d --no-deps --wait orchestrator product-discovery order-management pricing-promotions review-sentiment inventory-fulfillment
docker compose --env-file .env.showcase.local -f docker-compose.showcase.yml \
  -f docker-compose.telemetry-check.yml stop telemetry-check
```

`passed=true` 同时要求真实链路、指标、内容过滤和恢复检查通过。仅看到 span 名称、页面成功或 Collector 启动，不足以证明应用已接入观测。
