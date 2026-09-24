# MAF 术语表

本系列涉及的技术术语的简短、可用定义。每个章节 README 都应在术语**首次出现时就地给出定义**，并用一行锚点与本文保持一致。每当引入或废弃一个术语时，请更新本文件。

## 框架原语

**微软智能体框架（Microsoft Agent Framework，MAF）** — 微软用于构建由 LLM 驱动的智能体与多智能体工作流的 SDK，提供 Python 实现（`agent-framework`）。它把 AutoGen（多智能体）与 Semantic Kernel（企业级集成）的设计取舍合并为单一抽象。

**智能体（Agent）** — 大语言模型加上指令、工具、可选会话与中间件。在 MAF 中，每个智能体最终都包装了一个 `ChatClient`。参见 `ChatAgent`。

**ChatAgent** — 默认的智能体实现。接收一个 chat client、指令、名称、描述以及工具列表。

**指令（Instructions）** — 即系统提示词。在 MAF 中，它是智能体上的一等字段，而不是你手动前置拼接的一条消息。

## 工具

**工具（Tool）** — 智能体可以调用的函数（或代码解释器之类的托管能力）。定义在用户代码中；由框架执行；结果回喂给 LLM。

**@tool 装饰器** — 把 Python 函数标记为工具。MAF 会依据 `Annotated[type, Field(description=...)]` 注解构建 JSON 模式（schema）。

**工具调用循环（Tool-calling loop）** — 指这样的循环：LLM 输出一个结构化 token，指明工具及其参数；框架解析它、调用对应函数、把结果回喂进对话，再向 LLM 索取最终回复。MAF 会替你跑这个循环。

**MCP（Model Context Protocol，模型上下文协议）** — 一种 JSON-RPC 协议，用于通过 stdio、HTTP 或 SSE 发布工具。它让智能体可以使用由其他进程或其他语言实现的工具。

**工具审批（Tool approval）** — 一种中间件模式：在框架执行工具调用之前，必须由人工确认。通过函数中间件加一道门控机制实现。

## 会话与记忆

**AgentSession** — 在多次 `run()` 调用之间承载对话状态的对象，是一个值对象，包含 `session_id`、可选的 `service_session_id` 以及一个 `state` 字典。

**ChatHistoryProvider** — 存储对话消息。`InMemoryChatHistoryProvider` 是默认实现。可替换为 Redis、Cosmos DB 或自定义后端。

**上下文提供器（Context provider）** — 一种类中间件对象，在每一轮智能体执行之前运行，用于注入额外上下文（用户画像、记忆、检索到的文档）。它能读取对话，并返回一个携带扩展指令、消息或工具的 `AIContext`。

**AIContextProviders** — 注册在智能体上的上下文提供器集合。在每次 LLM 调用之前按顺序执行。

**TextSearchProvider** — 用于 RAG 的上下文提供器。包装一个检索函数，在 LLM 运行之前把命中的文档注入上下文。

**RAG（Retrieval Augmented Generation，检索增强生成）** — 在运行时取回相关文档并作为上下文交给 LLM，而不是仅依赖训练数据。在 MAF 中通过上下文提供器实现。

**记忆（Memory）** — 关于用户或领域的长期事实，可跨会话回忆。存放在你的数据存储中，通过上下文提供器暴露出来。

## 中间件

**中间件（Middleware）** — 围绕智能体调用运行的代码。MAF 有三层：智能体运行、函数调用、chat client。

**AgentMiddleware / 智能体运行中间件** — 包装整个智能体轮次。用于日志、追踪、限流、高层短路。

**FunctionMiddleware / 函数调用中间件** — 包装每一次单独的工具调用。用于审批门控、按工具日志、审计、校验。

**ChatMiddleware / ChatClient 中间件** — 包装原始的 LLM 调用。用于 PII 脱敏、请求/响应改写、内容过滤。

**短路（Short-circuit）/ MiddlewareTermination** — 一种中间件：设置好结果后返回（Python 中为抛出异常），从而跳过下游中间件与 LLM 调用。

## 可观测性

**OpenTelemetry（OTel，开放遥测）** — 分布式追踪、指标与日志的标准。MAF 开箱即用地输出 OTel 跨度（span）。

**跨度（Span）** — 一个有名称的工作单元，带有开始时间、结束时间与属性。`invoke_agent`、`chat`、`execute_tool` 是三个主要的智能体跨度。

**TracerProvider** — 用于创建 tracer 的 OTel 对象。每个进程构建一个，并在其上配置导出器。

**导出器（Exporter）** — 跨度去向何处（控制台、基于 gRPC/HTTP 的 OTLP、Azure Monitor、Jaeger）。

**GenAI 语义属性** — AI 跨度的标准属性名（`gen_ai.operation.name`、`gen_ai.request.model`、`gen_ai.usage.input_tokens` 等）。MAF 会自动写入。

**Jaeger** — 本仓库使用的开发期遥测界面，展示 Compose 栈中各服务的分布式追踪；指标和结构化日志需要独立的兼容后端。在本仓库中运行于 `:16686`。

**DevUI** — MAF 原生的浏览器面板，用于交互式地测试单个智能体或工作流。它与 Jaeger 相互独立：Jaeger 可视化生产遥测，DevUI 则是交互式测试运行框架。

## 工作流

**工作流（Workflow）** — 由边（edge）连接起来的执行器（executor）有向图。编排是确定性的、路由是类型安全的、状态可打检查点。与之相对，智能体由 LLM 驱动。

**执行器（Executor）** — 工作流中的一个节点，处理带类型的消息，然后要么路由到下游执行器，要么产出输出。

**边（Edge）** — 两个执行器之间的连接。可以是带条件的（由谓词门控）。

**WorkflowBuilder** — 用于组装工作流的流式 API。

**WorkflowContext** — 传入每个执行器处理函数。暴露 `send_message`、`yield_output` 与 `emit`。

**超步（Superstep）** — 工作流调度器的一轮。所有待处理消息会被并发派发；本超步中每个执行器都完成后，下一个超步才会触发。基于 Pregel 模型。

**@handler 装饰器** — 把执行器方法标记为特定输入类型的处理函数，在运行时生效。

**WorkflowEvent** — 执行期间发出的事件。带有类型（`executor_invoked`、`executor_completed`、`output`、自定义）与数据负载。用于可观测性以及为界面接线。

**AgentExecutor** — 内置执行器，把智能体当作工作流中的一个步骤来运行。接收 `AgentExecutorRequest`，产出 `AgentExecutorResponse`。

**InputAdapter / OutputAdapter** — 小型执行器，在领域类型与 `AgentExecutorRequest` / `AgentExecutorResponse` 之间转换。被便利构建器（`SequentialBuilder`、`ConcurrentBuilder`）隐藏起来。

## 编排模式

**SequentialBuilder** — 构建按顺序运行智能体的工作流。每个智能体都能看到此前的轮次。

**ConcurrentBuilder** — 构建并行运行智能体的工作流。用聚合器合并输出。

**HandoffBuilder** — 构建一张可以互相交接控制权的智能体网。智能体会发出合成的 `handoff_to_<name>` 工具调用。通过 `turn_limits` 防止成环。

**GroupChatBuilder** — 构建多轮讨论。由管理者（`RoundRobinGroupChatManager`、`PromptDrivenGroupChatManager` 或自定义实现）在每一轮挑选下一位发言者。

**Magentic / StandardMagenticManager** — 一种由 LLM 驱动的编排模式：管理者维护一份**事实账本**（它了解到的事情）和一份**计划**（下一步行动），向工作者委派任务、观察结果，并迭代直到满意。

**事实账本（Facts ledger）** — Magentic 管理者就任务收集到的信息滚动列表。

**计划（Plan）** — Magentic 管理者当前对剩余步骤的纲要。

**轮次上限（Turn limits）** — 对智能体可以交接多少次、或管理者可以迭代多少轮施加的预算。用于防止失控循环。

**聚合器（Aggregator）** — 把若干并行智能体的输出归约为单一结果的函数。

## 人工参与与检查点

**人工参与（Human-in-the-loop，HITL）** — 一种工作流模式：执行暂停以向人工提问，待人工回答后再恢复。

**request_info** — 工作流为等待人工输入而暂停时发出的事件类型。它携带一个 `request_id`，调用方必须把它与用户的回复配对。

**@response_handler 装饰器** — 标记在人工回复到达后恢复工作流的方法。

**检查点（Checkpoint）** — 在超步边界处对工作流状态做的序列化快照。用于崩溃后恢复或跨暂停恢复。

**CheckpointStorage** — 保存与加载检查点的接口。实现有：`InMemory`、`File`、`Postgres`、`Cosmos`。

**on_checkpoint_save / on_checkpoint_restore** — 执行器钩子，用于定制序列化哪些内容以及如何恢复。

## 声明式与可视化

**声明式工作流（Declarative workflow）** — 用 YAML 而非代码定义的工作流。运行时由 `WorkflowFactory` 加载，它解析规约、实例化执行器并接好边。

**算子注册表（Op registry）** — 从 YAML 中的字符串标识（`"upper"`、`"non_empty"`、`"prefix"`）到执行器实现的映射。

**可视化（Visualization）** — 把工作流图渲染成 Mermaid 或 Graphviz DOT。输出是确定性的，因此 PR 中的 diff 才有意义。

## 认证与请求上下文

**A2A（Agent-to-Agent，智能体间通信协议）** — 本仓库中用于智能体之间通信的 HTTP 协议。使用 `/message:send` 端点、`x-agent-secret` 请求头，以及转发的用户身份请求头。

**ContextVar** — Python 的 `contextvars.ContextVar`——异步安全的按请求状态。在完整项目中用于 `current_user_email`、`current_session_id`，而不必把参数一路穿过每个函数。

**JWT（JSON Web Token）** — 携带在 `Authorization: Bearer …` 请求头中的用户访问令牌。由 `AgentAuthMiddleware` 校验。

---

**维护说明：** 当你在某一章引入新术语时，请用同样的措辞把它加到这里。当某个术语不再使用时，标注 `(retired)` 而不是删除。
