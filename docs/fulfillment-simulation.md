# 本地履约模拟：outbox、幂等投递与对账

这是一个独立的跨系统实验：退货服务把“创建模拟取件请求”的意图与退货记录一起提交，worker 通过真实 HTTP 投递给另一个进程。供应商使用自己的 SQLite 文件保存受理回执。它不处理真实物流或支付，也不修改退款到账状态。

默认关闭 `FULFILLMENT_SIMULATION_ENABLED`，现有退货流程保持原行为。打开后，业务返回的 `SUCCEEDED` 仍只代表退货申请已创建；其中的 `fulfillment.status=PENDING` 只代表已入队。供应商受理结果需要另行查询。

## 启动与演示

沿用 `docs/portfolio-demo.md` 的 Python 3.12、uv、Node、pnpm 与 Docker 环境。在仓库根目录执行，使用专用合成演示数据库：

```bash
# 准备隔离基础设施和合成数据，不重置已有数据
bash scripts/demo.sh --seed-only
uv run --project agents/python --no-sync python scripts/migrate_fulfillment.py --demo

# 终端 1：启动应用；如果已有演示应用进程，应先正常停止再以该开关启动
FULFILLMENT_SIMULATION_ENABLED=true bash scripts/demo.sh
```

```bash
# 终端 2：独立模拟供应商，回执持久保存在项目 .local 目录
APP_ENV_FILE=/dev/null uv run --project agents/python --no-sync python -m shared.after_sales.simulator \
  --database .local/fulfillment-demo.sqlite --port 8199
```

在 `http://localhost:3010` 用客户账号申请退货，再用管理员账号批准。批准前不生成 outbox；批准后退货和 outbox 在同一事务中保存，尚未向供应商承诺成功。

```bash
# 终端 3：投递一条；去掉 --once 可持续轮询
uv run --project agents/python --no-sync python scripts/run_fulfillment_worker.py --demo --once
```

有任务时输出其 outbox ID 与状态，例如 `DELIVERED`。无可认领任务时不输出记录。客户端携带自己的访问令牌调用 `GET /api/returns/{return_id}/fulfillment`，能看到当前状态、尝试次数和供应商回执；其他客户得到 404。原始退货操作回执保留提交时的 `PENDING`，不会被异步投递结果改写。

两个新增进程均可用 Ctrl-C 停止。重启供应商时使用同一个 SQLite 文件；重启 worker 时继续使用同一个演示数据库。worker 崩溃后，默认等待 30 秒租约过期即可重新认领，并先查询供应商回执。不要删除 SQLite 文件来模拟重启，否则会丢失供应商侧幂等记录。

迁移与 worker 的 `--demo` 只读取 `.env.demo.local` 中的专用数据库地址，不加载私人 `.env`，且校验数据库名为 `reliable_commerce_demo`。不使用 `--demo` 时必须显式提供 `DATABASE_URL`。增量迁移可重复执行，不清表，不重置业务数据。

## 状态与恢复

| 状态 | 含义 | 下一步 |
| --- | --- | --- |
| `PENDING` | 业务事务已保存待投递意图 | 启动 worker |
| `PROCESSING` | 某个 worker 已持有租约 | 等待；进程退出后由新 worker 在租约过期后接手 |
| `UNKNOWN` | 没有确认供应商是否已受理 | 下一轮先 GET 对账；查无回执才用同一幂等键 POST |
| `DELIVERED` | 已核实模拟供应商的持久受理回执 | 仅表示模拟取件申请受理，不表示实际取件或退款到账 |
| `NEEDS_REVIEW` | 参数冲突、回执不匹配，或自动尝试耗尽 | 检查 `last_error`，修复故障后显式只读对账 |

默认每条记录最多进行 5 次自动投递尝试；之后允许一次收尾查询，再进入人工复核。worker 每轮每条记录只做一次查询和最多一次 POST，不叠加隐藏 HTTP 重试。

```bash
# 仅查询指定未知/待复核事件，绝不新增供应商取件请求
uv run --project agents/python --no-sync python scripts/run_fulfillment_worker.py \
  --demo --reconcile <outbox-id>
```

若供应商已有匹配回执，对账会更新为 `DELIVERED`；查无回执或仍不可达时保留 `NEEDS_REVIEW`。此命令不会重置投递预算或替代业务审批。

## 关键边界

- 退货、订单状态和 outbox 在同一 PostgreSQL 事务中提交；回滚不会留下待发送任务。
- 认领使用 `FOR UPDATE SKIP LOCKED`、有限租约和随机认领令牌，认领事务结束后才进行 HTTP。旧 worker 不能覆盖新认领者的结果。
- 供应商将固定幂等键、参数哈希和回执一起保存。相同参数重放返回原回执；同一键改参数返回 409。
- 丢响应不直接推断失败。恢复先按幂等键读取回执，并核对参数哈希、受理状态与模拟标识，再标记投递完成。
- HTTP 客户端只接受本机 HTTP 地址，禁用代理与重定向；供应商命令固定绑定 `127.0.0.1`。该服务没有公网鉴权设计，不能按生产物流服务部署。
- 故障注入只存在于测试进程的函数边界，没有公开 HTTP 故障开关。

## 验证

```bash
APP_ENV_FILE=/dev/null ENVIRONMENT=test RECORD=false LLM_PROVIDER=replay \
  OPENAI_API_KEY= AZURE_OPENAI_KEY= AZURE_OPENAI_API_KEY= \
  DEEPSEEK_API_KEY= MOONSHOT_API_KEY= TYPESAFE_API_KEY= \
  uv run --project agents/python --no-sync python -m pytest \
  agents/python/tests/test_fulfillment_outbox.py -q
```

测试使用真实隔离 PostgreSQL/Redis 容器、loopback HTTP 子进程及独立 SQLite 文件；包含事务回滚、审批前无入队、并行认领、供应商幂等与参数冲突、提交后丢响应、供应商重启、worker 在认领后/确认后退出、过期租约隔离、有限重试、只读人工对账、跨用户读取和重复迁移。

这些结果验证的是本地模拟契约。真实供应商还需要稳定的幂等键保留期、查询接口、鉴权和业务状态约定；本实验不证明真实物流或支付系统具有相同保证。
