# 能力矩阵

本文件按能力逐项说明可靠电商多智能体平台当前的实现覆盖情况。矩阵中的每一行都直接对照代码核实过，而不是沿用更早的描述。

各智能体安全维度的专门拆解（注入防御、角色强制、评测/红队覆盖）见 [`docs/agent-audit-matrix.md`](agent-audit-matrix.md) —— 本文件覆盖更广的功能面，两者在护栏部分有重叠，互相交叉引用而不重复描述。

**状态图例**

| 状态 | 含义 |
|--------|---------|
| 已实现 | 已落地并接入线上请求路径 |
| 部分实现 | 已具备但不完整（行内已注明） |
| 计划中 | 尚未实现，已列入后续规划 |
| 设计如此 | 有意不实现，并已在行内说明理由 |

---

## 矩阵

| # | 能力 | 实现位置 | 状态 | 说明 |
|---|---------|--------|--------|-------|
| 1 | 挂载到智能体上的中间件 / 上下文提供器流水线 | `shared/middleware.py` 的 `build_specialist_middleware()`，接入每个专业智能体的 `Agent(...)` 构造过程 | 已实现 | 中间件按固定顺序组合，涵盖日志、审计、护栏与步骤记录 |
| 2 | MCP 服务端协议 | `packages/mcp-product`、`packages/mcp-inventory` —— 两个真实的 FastMCP 服务，streamable HTTP 传输，真实 JSON-RPC | 已实现 | 可独立发布、独立部署 |
| 3 | 面向专业智能体的流式对话（`/message:stream`） | `shared/agent_host.py` 在每个专业智能体上同时暴露 `/message:send` 与 `/message:stream`（SSE）；`orchestrator/agent.py` 的 `call_specialist_agent` 消费专业智能体的流，并在工具调用进行中实时转发 `event: delta` 帧 | 已实现 | 与 `tool` 编排模式配合，实现实时预览 |
| 4 | 入口侧提示词注入检测 | `shared/guardrails/injection_middleware.py`，通过共享中间件流水线挂载 | 已实现 | 默认仅观测（`GUARDRAILS_BLOCK_ON_INJECTION` 可升级为硬拒绝） |
| 5 | 存储内容净化（工具结果重新进入模型时） | `shared/guardrails/output_middleware.py` | 已实现 | 按工具白名单逐字段中和用户生成的自由文本 |
| 6 | 输出内容审核（自伤 / 仇恨 / 暴力措辞筛查） | `shared/guardrails/moderation.py` + `moderation_middleware.py` | 已实现 | `OUTPUT_MODERATION_MODE=enforce` 可替换非流式回复；流式回复只能在事后标记（分片已发出）—— 这一取舍已在文档中说明 |
| 7 | 步骤记录器 → 实时智能体时间线 | `shared/agent_observability.py` 的 `StepRecorderMiddleware`，挂载到每个智能体，按请求导出为 SSE 的 `event: step` 帧与 `/runs` 界面 | 已实现 | 时间线在前端逐条实时呈现 |
| 8 | 扇出/扇入 + 顺序的 HITL 门控工作流 | `workflows/pre_purchase.py` 与 `workflows/return_replace.py` 均使用 MAF 的 `WorkflowBuilder`，是真实的执行器图；`return_replace.py` 的 HITL 门控通过 `ctx.request_info` 暂停 | 已实现 | 两个工作流都注册到了实时路由（`orchestrator/modes/`） |
| 9 | 以中间件形式实现人工参与（HITL） | `shared/hitl.py` 在中间件层拦截五个破坏性工具，独立于每个工具自身的实现 | 已实现 | 被门控的调用在工具方法执行前即被短路，并返回统一的 `{status, message, request_id}` 结构 |
| 10 | 共享工具库 | `shared/tools/` —— 8 个模块，由需要的专业智能体导入（`cart_tools.py`、`return_tools.py`、`seller_tools.py`、`loyalty_tools.py`、`inventory_tools.py`、`user_tools.py`、`memory_tools.py`、`pricing_tools.py`） | 已实现 | 领域专属逻辑仍保留在各专业智能体自己的 `tools/` 目录中 |
| 11 | 处理权交接编排 | `orchestrator/handoff.py` 的 `HandoffBuilder` 网格，构建在 `RemoteSpecialistChatClient` 之上 | 已实现 | 网格从一个**不带工具的分诊智能体**启动：若用会调用工具的编排器作为起点，它会既路由又作答，而不是交接 |
| 12 | 群聊编排 | `workflows/group_chat.py` —— 两个智能体参与者 + 一个主持人 | 已实现 | 顺序化会议记录形式 |
| 13 | Magentic 动态编排 | **未实现。** `orchestrator/modes/` 中只有 `base`、`tool_router`、`handoff_mode`、`workflow_mode` 与 `group_chat_mode`，没有 `magentic_mode.py` | 计划中 | 属于尚未构建的能力，而非不可用 |
| 14 | 评测运行框架 | `evals/harness.py` 的 `ProductionRunner`，真实评分器、已提交的基线、纳入 CI 的冒烟套件 | 已实现 | 评测跑的是生产路径而非其副本 |
| 15 | 长期记忆 —— 写入路径 | `shared/tools/memory_tools.py` —— 智能体可调用的保存/更新工具 | 部分实现 | 身份来自 `RequestContext`，因此模型无法写到其他用户的档案上 |
| 16 | 教程章节测试覆盖 | 32 章均提供代码，且全部带测试（非集成套件在 CI 中为绿） | 已实现 | 第 21 章尚在规划中 |
| 17 | 服务端事实核验（grounding） | `shared/grounding/{ledger,extractor,verifier,middleware}.py` —— 三层机制（按请求的工具结果台账、批量数据库查询、一致性检查），`GROUNDING_MODE` 支持 `off`/`observe`/`annotate`/`enforce` | 已实现 | `enforce` 模式会校验答案中的结论与数据库记录是否一致 |
| 18 | 资金路径的幂等性 | `shared/idempotency.py` + `idempotency_keys` 表，应用于 `initiate_return`、`process_refund`、`execute_approved_action` 与结算 | 已实现 | 通过 `ON CONFLICT DO NOTHING` 预留，重放已完成的预留，拒绝进行中的重复请求，回收超过 60 秒的旧预留，失败时释放 |
| 19 | 限流 | `shared/rate_limit.py` —— 两条对话路由上的 Redis 滑动窗口，按用户计，匿名流量按 IP 计 | 已实现 | 失败时放行 |
| 20 | 成本估算与预算上限 | `shared/cost.py` + `cost_budget_middleware.py`，`COST_BUDGET_MODE` 默认 `observe` | 已实现 | 按轮次估算 token 与成本 |
| 21 | 遥测深度 | `shared/telemetry.py` —— 追踪发往 Jaeger；指标与日志导出需要兼容的独立接收端；对 httpx、asyncpg 与 OpenAI 自动埋点；采用 `invoke_agent` 这一 GenAI 跨度约定；`trace_id` 关联写入 `usage_logs`。本项目自有的自定义指标只有 `ecommerce.llm.cost.usd`，以及按方向拆分的 token 计数，二者复用预算上限已经算出的按轮次估算。其余全部来自自动埋点 | 已实现（可选的 Langfuse 导出器除外） | 同一套指标命名可覆盖全部服务 |
| 22 | 已注册的编排模式 | 五种：`tool`、`handoff`、`workflow:pre-purchase`、`workflow:return-replace`、`group-chat` | 已实现 | 已在运行中的服务栈上实测：同一个问题用三种模式作答，分别得到编排器自身的综合结果、专业智能体未经改写的回答，以及一份圆桌记录 |
| 23 | `/api/orchestration/*` 路由 | `modes`、`modes/{name}/graph`、`compare`、`{run_id}/resume` | 已实现 | `resume` 从检查点恢复暂停的工作流，并在执行前**先认领待处理行**，避免两次点击同时释放一笔退款 |
| 24 | MCP 消费（客户端侧） | `product-discovery` 与 `inventory-fulfillment` 可以把直连 asyncpg 的工具替换为针对两个 FastMCP 服务的 `MCPStreamableHTTPTool`，由 `MCP_ENABLED` 控制 | 已实现 | 默认关闭；开启后走 MCP 数据层 |
| 25 | 实际使用的会话与检查点后端 | `MAF_SESSION_BACKEND` 与 `MAF_CHECKPOINT_BACKEND` 驱动真实的后端实现 | 已实现 | 工作流在运行期间通过 MAF 的 `CheckpointManager` 写入检查点，这正是 `GET /api/runs/{id}/checkpoints` 能返回内容的原因 —— 仅有依赖注入注册是不够的 |
| 26 | 种子数据与认证服务 | `scripts/seed.py`（确定性，`random.seed(42)`）与 `auth_server` 镜像 | 已实现 | 种子脚本是演示数据的唯一来源，确定性保证重复运行产出同样的数据行 |
| 27 | 匿名多轮记忆 | 不持久化 —— 匿名店铺会话在任何层级都没有上下文 | 设计如此 | 匿名访问不落库，避免产生无主数据 |
| 28 | Langfuse 导出器 | 与 OTel 并行的可选附加导出器 | 设计如此 | OTel 是主通道，已把 GenAI 跨度送到 Jaeger；再加一个导出器只会重复已有内容 |

---

## 不在本表范围内的部分

平台已完整实现该业务域 —— 一个编排器加五个专业智能体、A2A 路由、工具路由模式、基于 Postgres 的检查点、OAuth2/JWT 认证、护栏、资金路径的幂等性、限流，以及基于 OpenTelemetry 的遥测流水线。本矩阵只跟踪**缺口**，共同的基础能力不在这里逐行重复。

## 关于本文档的历史说明

本文档此前曾作出两项与代码不符的断言，现已更正：一是工作流未接入实时路由（实际已接入），二是存在 `magentic_mode.py`（实际并不存在）。

## 相关文档

- [`docs/agent-audit-matrix.md`](agent-audit-matrix.md) —— 各智能体的安全审计矩阵
- [`docs/agent-quality.md`](agent-quality.md) —— 评测方法论、数据集与 CI 门禁
- [`docs/architecture.md`](architecture.md) —— 系统总览与请求链路
