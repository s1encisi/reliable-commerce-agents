# 任务工作台：查询、验收与恢复

任务工作台把复杂查询分成可核实的只读步骤。访问 `/tasks`，填写目标、文字要求和结构化条件，再显式生成计划、执行下一步。它不拥有订单、退货或支付的写权限。

![真实API与数据库的任务工作台](images/upgrade-20261001/reliable-commerce-tasks-desktop.png)

## 两种执行来源

- **离线固定示例**：在 `LLM_PROVIDER=replay` 且目标精确为“筛选两款商品并核对库存”时，使用标注为 `deterministic_demo` 的固定计划。工具实际查询 PostgreSQL，并走正常验收逻辑。这不是实时模型规划。
- **模型计划**：规划器返回受控 JSON，最多八步，依赖按拓扑顺序出现。模型不能改变用户目标或结构化条件。实际调用需要提供方配置与已确认费用范围；缺配置不能伪造计划。

## 完成的含义

工具 HTTP 成功、非空回答和核验过一个价格，都不足以证明整个任务完成。

受控验收目前覆盖商品筛选、商品对象对比、库存核实、本人订单查询。验证器从服务端工具溯源取得对象 ID，再按当前用户权限读取数据库事实，核对对象、数量、价格上限、评分、品类及库存条件。不同合法工具路径可以产生相同的有效证据，例如逐个读取商品详情可以代替对比工具。

自然语言目标和文字要求不能被这些字段完整证明。模型生成的计划完成结构化步骤后进入 `needs_review`，界面列出原始目标及待核实要求；用户审阅证据并显式确认，才标记完成。确认来源保存为 `user_confirmed`，不是模型自动核验。固定离线示例的精确目标可在证据齐备时自动完成。

价格使用目录现有货币单位，不进行隐式汇率换算。检索得到候选商品不等于它一定满足未结构化的语义偏好。

## 状态与版本

| 状态 | 下一步 |
|---|---|
| `pending` | 显式规划 |
| `planning` | 等待；进程中断后显式恢复 |
| `ready` | 执行下一个依赖已满足的步骤 |
| `running` | 等待；中断后核实并恢复只读步骤 |
| `needs_replan` | 查看失败证据，在剩余规划预算内重规划 |
| `needs_review` | 用户审阅目标与文字条件后确认 |
| `completed` | 查看证据；过期的只读结果再次使用前重新核实 |

所有变更携带 `revision`。409 表示状态已变化，先读取最新状态，不自动重发旧操作。数据库 session advisory lock 防止仍在运行的规划/执行被第二个请求接管；进程退出后锁随连接释放。恢复不重置最多两次规划的计数，也不重置模型费用。

修改目标或条件会使旧计划、完成结果和确认失效，旧回执保存在历史中。模型结果在提交时再次核对版本，不能覆盖更新后的任务。

## 接口

所有接口复用用户认证并验证任务归属。

| 方法与路径 | 行为 |
|---|---|
| `POST /api/tasks` | 创建 `{goal, constraints, requirements}` |
| `GET /api/tasks/{id}` | 读取本人任务与回执 |
| `PATCH /api/tasks/{id}` | 携带 revision 修订目标、文字和结构化条件 |
| `POST /api/tasks/{id}/plan` | 携带 revision 规划 |
| `POST /api/tasks/{id}/next` | 携带 revision 执行下一步 |
| `POST /api/tasks/{id}/recover` | 显式恢复中断的规划或只读执行 |
| `POST /api/tasks/{id}/confirm` | `{revision, confirmed:true}`，确认待核实目标与文字要求 |

## 可追踪性与测量

任务页保存本人最近任务标识，刷新后从服务器读取状态；它不会根据浏览器按钮状态推断任务完成。每步展示验收范围、事实证据及回执有效期。

任务 API 返回 `Server-Timing`。启用 OpenTelemetry 后产生 `task.create/plan/next/...` span 与 Trace ID；指标只使用 phase、状态类别等低基数标签。HTTP 200 不代表任务业务成功，业务结果仍看状态和回执。观测故障不会重新执行业务。

在本机离线演示运行后，可测量固定计划的实际 HTTP 与数据库链路：

```bash
uv run --project agents/python --no-sync python scripts/benchmark_tasks.py \
  --samples 12 --concurrency 1 3 5 --output .local/task-benchmark-new.json
```

工具在创建任务前检查目标为本机离线环境，拒绝覆盖既有报告。它测量排队、各 API 阶段、端到端时延及业务结果；没有模型延迟、真实用户流量或生产 SLA 含义。

相关源码：`shared/task_state.py`、`shared/task_acceptance.py`、`orchestrator/routes/tasks.py`、`shared/task_observability.py`。相关测试：`tests/test_task_acceptance.py`、`tests/test_task_state.py`、`tests/test_task_observability.py`。
