# 本地演示：申请、审批与结果核实

此演示使用独立的 `reliable-commerce-demo` Compose 项目、数据库、卷和端口，只有合成数据。
不用模型账号，也不会调用真实支付。这里展示真实 MAF 固定工作流和数据库执行，不展示实时模型对自由文本的泛化能力。

## 启动

先安装 Python 3.12、uv、Node.js 22、pnpm 10 和 Docker。

```bash
bash scripts/install-deps.sh --core --images
bash scripts/demo.sh
```

浏览器打开 **http://localhost:3010**。API 为 `http://localhost:8180`，其余五个 Python 服务使用 8181–8185。
PostgreSQL 使用 55432，Redis 使用 56379。端口只绑定本机。
演示凭据、随机内部密钥位于被 Git 排除的 `.env.demo.local`，访问权限为当前用户可读写。

| 角色 | 邮箱 | 演示密码 |
| --- | --- | --- |
| 客户 | customer@example.test | DemoPass123! |
| 审批人 | admin@example.test | DemoPass123! |

这些是公开的合成演示账户，不能用于生产部署。真实模型聊天使用正常 `.env` 配置和 `scripts/dev.sh`，
不要在离线演示里把回放当成实时推理。

## 三分钟讲解顺序

1. 客户登录，打开订单 `aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1`，填写原因并提交退货。页面显示等待审批，尚无退货记录。
2. 在另一个浏览器会话用审批人登录，进入 `/admin/approvals`，查看绑定的订单、原因与退款方式后批准。
3. 回到客户页面，点击 **查看请求状态**，看到已创建的申请与退货信息。没有“退款已到账”的声明。
4. 尝试 `...aaa2` 缺签收证据订单，观察人工核实提示；`...aaa3` 已超过期限，观察拒绝且无业务写入。
5. 需要演示工作流时，进入 `/chat`，选择 **退货与换货（顺序执行 + 工作流内人工参与）**，输入带明确订单 UUID 的退货请求，由管理员在 `/runs` 查看审批与恢复；客户不能自行授予管理审批。

网络失败时，浏览器在请求发出前保存操作标识。重新查询同一标识即可核实结果；相同参数重试保留标识，
明确开始新意图时才更换。数据库记录仍是最终依据。

## 故障演示与复验

故障注入只存在于测试/评估进程，没有公开 HTTP 故障开关。评估输出使用新的时间戳文件，保留历史报告及本轮全部失败。

```bash
# 不读取私人环境文件；所有模型密钥为空且禁止录制
export APP_ENV_FILE=/dev/null ENVIRONMENT=test RECORD=false LLM_PROVIDER=replay
export OPENAI_API_KEY= AZURE_OPENAI_KEY= AZURE_OPENAI_API_KEY=
export DEEPSEEK_API_KEY= MOONSHOT_API_KEY= TYPESAFE_API_KEY=
uv run --project agents/python --no-sync python -m pytest agents/python/tests/test_after_sales_recovery.py -q
evaluation_stamp=$(date +%Y%m%d-%H%M%S)
uv run --project agents/python --no-sync python -m evals.after_sales \
  --output "docs/evaluation/after-sales-$evaluation_stamp.json" \
  --log-dir ".local/after-sales-$evaluation_stamp-raw"

# 只重置合成演示数据；保持现有应用进程
bash scripts/demo.sh --seed-only --reset
source scripts/dev-env.sh
E2E_BASE_URL=http://localhost:3010 pnpm --dir web exec playwright test e2e/after-sales-reliability.spec.ts
```

首次运行 Next.js 开发服务器可能需要编译页面。Ctrl-C 停止应用进程；基础设施可单独停止：

```bash
docker compose --env-file .env.demo.local -f docker-compose.portfolio.yml stop
```

该命令不删除数据卷。需要重新演示时使用 `--reset`，其重置逻辑仅接受专用演示数据库名。

## 录制前追加新合成订单

已有演示订单已被测试使用时，不必重置数据库。下面的准备入口每次只追加一笔新订单、一个明细项和相应签收证据，保留旧订单、审批、任务、账号与商品。它只接受 `current_database() = reliable_commerce_demo`，并要求既有的固定合成客户 `customer@example.test` 和对应的合成商品，校验失败不创建记录。

```bash
# 宿主隔离演示（3010）：只读取其专用数据库地址
APP_ENV_FILE=/dev/null uv run --project agents/python --no-sync python -m scripts.prepare_demo_order \
  --demo --scenario normal

# 容器隔离展示（3020）：使用现有seed服务的只读脚本挂载与专用数据库环境
docker compose --env-file .env.showcase.local -f docker-compose.showcase.yml \
  run --rm --no-deps seed /app/.venv/bin/python -m scripts.prepare_demo_order --scenario normal
```

标准输出为一行 JSON，包含新的 `order_id` 和 `order_path`。先用客户账号打开该路径发起申请，再由管理员审批，最后回到客户查询回执。normal 的签收时间为当前 UTC 前5天；当前隔离演示开启 HITL，因此仍须管理员批准。这里只准备演示订单，不授予审批、不执行退货或支付。

可选固定场景只有 `normal`、`missing_delivery`、`expired`；后两者分别不写签收证据和写入45天前的签收记录。每次执行都会新增一笔合成订单，不提供任意用户、商品、表或删除参数。

相关隔离验证：`agents/python/tests/test_recording_order.py`，覆盖旧记录逐行保留、错误数据库拒绝、非合成身份/商品拒绝及实际 CLI 输出。

## 只读任务演示

打开 `/tasks`，点击“离线固定示例（不调用模型）”，创建“筛选两款商品并核对库存”。显式生成计划并执行两步，查看数据库事实与验收回执，然后刷新页面核实状态仍由服务器保存。此固定计划不声称模型推理能力。

任务允许修订目标与条件；旧计划及结果保留为失效历史。模型生成计划完成受控检查后，原始目标及文字要求仍需本人审阅确认。详见[任务工作台](task-workspace.md)。

运行 `scripts/benchmark_tasks.py` 可产生新的离线HTTP并发记录；这是固定计划的本机观察，不是生产容量或模型性能测试。

## 当前浏览器证据

[等待审批](images/upgrade-20261001/reliable-commerce-pending.png) · [管理员审批](images/upgrade-20261001/reliable-commerce-approval.png) · [已创建回执](images/upgrade-20261001/reliable-commerce-completed.png) · [手机页面](images/upgrade-20261001/reliable-commerce-mobile.png)
