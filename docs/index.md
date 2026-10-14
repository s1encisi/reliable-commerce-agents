# 可靠电商多智能体平台

Reliable Commerce Agents 是一个基于 Microsoft Agent Framework、FastAPI 和 Next.js 的电商多智能体应用，覆盖商品查询、订单管理、促销咨询和售后处理，重点实现带人工审批、幂等执行和中断恢复的退货流程。

[项目仓库](https://github.com/s1encisi/reliable-commerce-agents) · [快速开始]({{ site.baseurl }}/getting-started/quick-start.html) · [系统架构]({{ site.baseurl }}/architecture/)

## 主要功能

- **多智能体协作**：编排器连接商品、订单、定价、评论和库存服务，支持工具路由、Handoff、工作流与群聊编排。
- **统一业务规则**：聊天、API 与工作流共用售后服务，校验用户身份、订单归属、签收信息和退货期限。
- **人工审批**：审批绑定申请参数、订单状态与有效期，执行前重新校验。
- **可靠执行**：退货申请、订单状态与操作回执在同一 PostgreSQL 事务内提交；通过操作 ID 查询结果并处理重试。
- **可观测性**：前端展示审批队列和运行记录，OpenTelemetry 记录智能体与服务的调用链路。

## 售后体验

客户在订单页提交申请，审批人查看申请内容并作出决定，客户随后查询处理结果。签收信息缺失或冲突时进入人工核实，超过退货期限则拒绝申请。

![退货审批队列]({{ site.baseurl }}/docs/images/reliable-commerce-approval.png)

LLM 负责理解请求和编排工具，业务规则与执行结果由服务端管理。当前售后流程处理退货申请及其数据库状态，未接入真实支付、物流和邮件服务。

## 本地运行

准备 Python 3.12、uv、Node.js 22、pnpm 10 和 Docker，在 Linux、macOS 或 Windows WSL 中运行：

```bash
git clone https://github.com/s1encisi/reliable-commerce-agents.git
cd reliable-commerce-agents
bash scripts/install-deps.sh --core --images
bash scripts/demo.sh
```

打开 **http://localhost:3010**。演示使用合成订单与固定工作流，无需模型 API Key。

| 角色 | 邮箱 | 密码 |
| --- | --- | --- |
| 客户 | customer@example.test | DemoPass123! |
| 审批人 | admin@example.test | DemoPass123! |

[演示指南](portfolio-demo.md)介绍了完整操作步骤。接入真实模型对话时，按 `.env.example` 配置模型提供方，并使用 `scripts/dev.sh` 启动；Windows PowerShell 使用 `scripts/dev.ps1`。

## 技术实现

后端采用 Python、Microsoft Agent Framework、FastAPI 与 asyncpg；数据层使用 PostgreSQL、pgvector 和 Redis。前端采用 Next.js、React、TypeScript 与 Tailwind CSS，提供聊天、订单、审批和运行记录页面。

退货流程的实现集中在统一售后服务、审批管理和工作流三个模块，详细说明见 [售后架构](after-sales-architecture.md)。

## 文档导航

| 主题 | 入口 |
| --- | --- |
| 安装与运行 | [快速开始]({{ site.baseurl }}/getting-started/quick-start.html) |
| 系统设计 | [架构]({{ site.baseurl }}/architecture/) |
| 智能体基础 | [核心概念]({{ site.baseurl }}/concepts/) |
| 代码示例 | [Python 教程]({{ site.baseurl }}/tutorials/) |
| 接口与数据 | [API 参考](api-reference.md) · [数据库结构](database-schema.md) |

基于 [nitin27may/e-commerce-agents](https://github.com/nitin27may/e-commerce-agents) 构建，采用 [MIT 许可证](https://github.com/s1encisi/reliable-commerce-agents/blob/main/LICENSE)。
