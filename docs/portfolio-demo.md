# 本地演示：申请、审批与结果核实

此演示使用独立的 `reliable-commerce-demo` Compose 项目、数据库、卷和端口，只有合成数据。
不用模型账号，也不会调用真实支付。这里展示真实 MAF 固定工作流和数据库执行，不展示实时模型对自由文本的泛化能力。

## 启动

先安装 Python 3.12、uv、Node.js 22、pnpm 10、.NET 10（仅其示例需要）和 Docker。

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
3. 回到客户页面，点击 **Check request status**，看到已创建的申请与退货信息。没有“退款已到账”的声明。
4. 尝试 `...aaa2` 缺签收证据订单，观察人工核实提示；`...aaa3` 已超过期限，观察拒绝且无业务写入。
5. 需要演示工作流时，进入 `/chat`，选择 **Return & Replace**，输入带明确订单 UUID 的退货请求，在 `/runs` 查看审批与恢复。

网络失败时，浏览器在请求发出前保存操作标识。重新查询同一标识即可核实结果；相同参数重试保留标识，
明确开始新意图时才更换。数据库记录仍是最终依据。

## 故障演示与复验

故障注入只存在于测试/评估进程，没有公开 HTTP 故障开关。

```bash
uv run --project agents/python --no-sync pytest agents/python/tests/test_after_sales_recovery.py -q
uv run --project agents/python python -m evals.after_sales --output docs/evaluation/after-sales-results.json

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
