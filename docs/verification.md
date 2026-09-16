# 验证与复现记录

日期：2026-09-16。此记录区分实际运行、受控评估和未验证事项。

## 本轮执行结果

| 检查 | 实际结果 |
| --- | --- |
| Python 完整测试 | 906 passed，3 skipped，2 个依赖警告 |
| Python Ruff / 格式 | 全部通过；冻结的历史基线源文件明确排除格式重写 |
| 前端单元测试 | 179 passed，30 个文件 |
| TypeScript | 通过 |
| 前端 ESLint | 0 errors，64 条既有 warnings |
| Next.js 生产构建 | 通过 |
| 真实浏览器 | 3 个流程通过，桌面与手机布局检查通过 |
| .NET 基线兼容性 | 598 passed；没有移植 Python 的新恢复协议 |
| 文档 / 补丁 | 链接与 git diff 检查通过 |

真实模型的 3 项测试明确跳过；运行中显式清空云端模型凭据。没有真实支付、退款或外部业务数据。
本机 .NET 测试使用了经固定 manifest、全部层及运行配置核对的 Ryuk 本地引用，解决离线导入丢失 digest 索引的问题；
没有禁用资源清理器。配置接口见 [Testcontainers 官方说明](https://dotnet.testcontainers.org/custom_configuration/)。

## M4 受控对照

22 类场景，每类重复 3 次，每个版本 66 次 trial。每次重置专用 PostgreSQL 数据，固定业务时钟，
源文件在执行前后核对 LF 换行规范化散列；故障没有触发的 trial 不计为成功恢复。

| 版本 | 满足预定义场景条件 | 违规写入 trial | 重复业务效果 trial |
| --- | ---: | ---: | ---: |
| B0 | 37/66 | 15 | 0 |
| B1 | 51/66 | 0 | 0 |
| B2 | 66/66 | 0 | 0 |
| A_no_recheck | 45/66 | 18 | 0 |
| A_no_reconcile | 63/66 | 0 | 0 |
| A_no_budget | 63/66 | 0 | 0 |

- B0：冻结的上游退货工具基线。
- B1：M2 统一规则版本。
- B2：当前持久操作与恢复实现。
- A_no_recheck：在独立评估进程中关闭提交前政策复核。
- A_no_reconcile：关闭提交结果核实。
- A_no_budget：关闭统一尝试预算，由评估器外部时限终止。

这些是场景条件的通过计数，不是模型准确率或生产成功率。原版已经有行锁和部分防重，不能把所有
防重复能力归为本分支创新。主要新增证据是规则一致性、原子回执以及对已提交但未收到响应的确认能力。
并发场景受调度影响，基线计数可能在重跑时略变；保留逐次结果，不挑选最好一次。
重复运行具有相关性，不把 66 次 trial 当作 66 个独立用户样本。该集合是工程回归集，不是盲测模型保留集。

完整逐案例结果、故障触发数、基线散列和实际实现散列见 [JSON 证据](evaluation/after-sales-results.json)。

## 本地服务负载观察

每个并发条件使用 100 个不同的合成订单，测量提交服务与本地数据库。时延从进入并发槽位开始计算，
不包括客户端等待槽位、模型、公网或真实外部服务。

| 并发 | 成功 / 请求 | 观察吞吐（次/秒） | 服务 p50（ms） | 服务 p95（ms） |
| --- | ---: | ---: | ---: | ---: |
| 1 | 100/100 | 134.71 | 7.232 | 8.137 |
| 3 | 100/100 | 282.01 | 8.851 | 10.465 |
| 5 | 100/100 | 384.76 | 10.34 | 12.974 |

单机、单次有限样本不能外推生产容量或 SLA，部署前仍需独立负载与故障验证。

## 浏览器验收

环境：`http://localhost:3010`，API `http://localhost:8180`，独立演示数据库。
Browser 插件不可用，因此使用仓库 Playwright；桌面 1280×720、审批页面 1440×1000、手机 393×852。

| 项目 | 证据 |
| --- | --- |
| 页面身份与非空 | 实际登录、订单 URL 与页面内容断言通过 |
| 框架 / 运行错误 | 正常流程无未捕获页面异常，截图无框架错误覆盖层 |
| 申请与审批 | 提交时没有退货记录，管理员批准后出现确认回执 |
| 响应丢失 | 请求实际到达后端后丢弃响应，浏览器保留的操作标识仍可查到等待状态 |
| 缺签收证据 | 显示人工核实，未显示创建成功 |
| 响应式 | 手机视口无文档横向溢出，回执可滚动查看 |

HTTP 400/409 的业务拒绝和人为注入的网络失败属于预期验证事件，不当作应用未捕获异常。
截图仅含合成账户、订单、商品和地址。真实模型聊天、生产支付与公网部署未做浏览器验收。

[等待审批](images/reliable-commerce-pending.png) · [审批页面](images/reliable-commerce-approval.png) · [确认回执](images/reliable-commerce-completed.png) · [手机页面](images/reliable-commerce-mobile.png)

## 复验命令

```bash
OPENAI_API_KEY= AZURE_OPENAI_KEY= AZURE_OPENAI_API_KEY= RECORD=false uv run --project agents/python --no-sync pytest agents/python/tests -q
uv run --project agents/python --no-sync ruff check agents/python
uv run --project agents/python --no-sync ruff format --check agents/python
uv run --project agents/python --no-sync python -m evals.after_sales --output docs/evaluation/after-sales-results.json
pnpm --dir web exec vitest run --maxWorkers=1
pnpm --dir web exec tsc --noEmit
pnpm --dir web lint
pnpm --dir web build
```

浏览器复验先按 [演示说明](portfolio-demo.md) 启动并重置合成数据。真实模型试验需先确定服务与费用上限。

## 发布审查与未完成验证

Gitleaks 8.30.1 对待公开历史及源码快照未检出密钥；禁止路径、私有环境文件和符号链接检查通过。
25 个既有图像/资源与公开上游 Git 对象一致，5 个新增合成演示资源已检查。
公开前确认远端没有旧 issue、PR、发行附件、工作流产物、Wiki、Discussion 或 Pages 数据。
扫描和来源检查不能证明不存在所有潜在商业秘密，新增真实数据仍需人工审查。

GitHub Actions 当前仍关闭：提供 `portfolio-ci.yml` 配置，但没有宣称远端 CI 已执行。
真实模型保留集、模型成本、生产性能和跨系统退款均未验证；这些限制不被离线测试通过替代。
