# E-Commerce Agents：中文学习与售后可靠性改进

[上游英文说明](README.en.md) · [中文文档导航](docs/zh-CN/README.md) · [Python 教程导航](tutorials/README.zh-CN.md) · [售后可靠性技术方案](docs/zh-CN/after-sales-reliability-plan.md)

这是基于 Microsoft Agent Framework（MAF）的开源电商多智能体项目。本地学习副本面向 Python 开发者，重点理解工具调用、业务规则、人工审批、执行恢复和评估。

**本地当前交付的是中文文档和技术方案。售后可靠性改造、故障实验和性能提升尚未实现或验证。** 源码沿用上游，不把上游功能或历史实验算作个人贡献。

## 项目版本与工作目录

| 对象 | 位置或标识 |
|---|---|
| 当前工作目录 | 本仓库根目录 |
| 当前分支 | main |
| 上游源码基线 | 26f47c494dd6b371312593e82f066713f6f56e9c |
| 中文文档整理日期 | 2026-09-07 |

仓库采用经过保密检查的当前快照作为独立初始版本。原始本地历史保留在本机备份中，不随此版本上传；上游作者、许可证和英文说明继续保留。

后续在仓库根目录阅读和开发。目录与版本说明见 [工作目录说明](docs/zh-CN/workspace.md)，上传范围见 [本地数据与上传边界](docs/local-privacy.md)。

## 这个项目做什么

一个协调智能体接收用户请求，通过 A2A HTTP 接口调用五个专业智能体。两套后端分别使用 Python 和 .NET / C#，共用 Next.js 前端、数据库和提示词资源。

| 智能体 | 主要职责 | Python 模块 |
|---|---|---|
| 协调器 | 意图分发、会话、API 与多种编排入口 | agents/python/orchestrator |
| 商品发现 | 商品查询、语义检索、比较和推荐 | agents/python/product_discovery |
| 订单管理 | 订单查询、修改、取消与售后工具 | agents/python/order_management |
| 价格与促销 | 定价、优惠、会员与促销查询 | agents/python/pricing_promotions |
| 评价与情感 | 商品评价汇总与情感分析 | agents/python/review_sentiment |
| 库存与履约 | 库存、仓库和配送信息 | agents/python/inventory_fulfillment |

主要组件是 Python 3.12+、MAF、FastAPI、asyncpg、PostgreSQL + pgvector、Redis、OpenTelemetry，以及 Next.js 前端。具体版本以各目录的 pyproject.toml、package.json 和锁文件为准。

## 建议从哪里开始

| 当前目的 | 阅读入口 |
|---|---|
| 只会 Python，希望先看懂一个智能体 | [Python 学习指南](docs/zh-CN/learning-guide.md) |
| 配置环境并运行一个小示例 | [中文快速开始](docs/zh-CN/quick-start.md) |
| 理解整条请求链路 | [架构导读](docs/zh-CN/architecture.md) |
| 找到售后相关代码 | [售后代码导航](docs/zh-CN/after-sales-code-map.md) |
| 理解已经有的防护及边界 | [安全与可靠性说明](docs/zh-CN/security-and-reliability.md) |
| 区分单元测试、回放和真实模型评估 | [评估说明](docs/zh-CN/evaluation.md) |
| 确定个人改进及验收方法 | [售后可靠性技术方案](docs/zh-CN/after-sales-reliability-plan.md) |

学习采用“老师 + 编程搭档”方式，从现有代码出发。每次只推进一个小阶段；修改前说明问题、文件和输入输出，修改后解释关键代码与验证结果。学习者参与需求、规则、结果和方案判断，不要求从空白手写函数。

## 最小 Python 运行方式

以下为后续执行命令，本轮文档整理没有安装依赖或调用模型。先确保 Python 3.12+ 与 uv 可用，在 Demo 根目录执行：

    uv sync --project tutorials --extra dev

使用已录制的模型响应运行第 02 章，避免真实模型调用：

    $env:LLM_PROVIDER = "replay"
    $env:RECORD = "false"
    uv run --project tutorials python tutorials/02-add-tools/python/main.py
    uv run --project tutorials pytest tutorials/02-add-tools/python/tests -m "not integration" -v

回放只适用于已有录制内容；换问题或提示词可能找不到录制记录。回放通过不代表真实模型对新问题也可靠。

## 完整平台运行方式

完整平台需要 Docker。配置本地模型服务或已授权使用的模型 API 后，在 Demo 根目录从源码启动：

    ./scripts/dev.ps1

预构建镜像体验可以使用 ./scripts/dev.ps1 -Demo，但该方式运行已发布镜像，不能验证本地 Python 修改。

| 入口 | 默认地址 |
|---|---|
| 网页 | http://localhost:3000 |
| 协调器 API | http://localhost:8080 |
| Aspire 观测面板 | http://localhost:18888 |

完整启动会创建容器、准备数据库并可能调用外部模型。先阅读 [快速开始](docs/zh-CN/quick-start.md)。多个副本不要直接共用同一套容器、端口或测试数据库。

## 当前已有能力与个人改进边界

上游已有五类编排模式、人工审批、检查点、角色权限、工具输入校验、HTTP 重试与熔断、幂等防重、限流、事实核验、成本记录和评估框架。代码存在不等于所有异常组合均已可靠处理。

个人改进聚焦一条退货申请流程：

    识别请求 → 确认订单 → 校验资格 → 必要审批 → 执行前复核 → 提交 → 核实最终状态

技术方案提出统一业务规则、按失败类型恢复、请求幂等与故障回归评估。每项改进都先建立原版反例，再给出对照与消融；当前不宣称已产生任何提升。

## 中文化范围

本轮中文化覆盖核心阅读入口及 Python 第 01、02、24、26 章。其余章节在中文总目录中有主题导航，详细原文仍为英文。接口字段、标识符、提示词、数据库内容和前端文案没有翻译，以免文档任务改变模型行为或 API 契约。

## 来源与许可

原项目来自 [nitin27may/e-commerce-agents](https://github.com/nitin27may/e-commerce-agents)，许可证见 [LICENSE](LICENSE)。保留上游作者、许可证及英文原文。本仓库用于保留代码、经过检查的项目文档和合成测试样例，个人记录与真实数据保留在本机。
