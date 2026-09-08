# 架构导读：模型怎样接入真实业务

[首页](../../README.md) · [上游架构文档](../architecture.md)

## 一次请求经过哪里

    网页 / API 客户端
        → FastAPI 协调器
        → 身份与会话上下文
        → 编排模式
        → 专业智能体或固定工作流
        → Python 工具
        → PostgreSQL / 外部服务
        → 结果核验、记录与响应

语言模型负责理解请求、提出工具调用和组织回答。框架负责工具调用循环；Python 代码负责参数验证、权限检查和真正的业务执行。数据库中的事实与业务规则不应由自然语言回复代替。

## 关键模块

| 路径 | 职责 | 建议阅读的问题 |
|---|---|---|
| [orchestrator/routes/chat.py](../../agents/python/orchestrator/routes/chat.py) | 聊天与 SSE 入口、模式分发 | 如何建立会话并结束一次请求 |
| [orchestrator/modes](../../agents/python/orchestrator/modes) | 编排适配层 | 哪些步骤固定，哪些由模型选择 |
| [orchestrator/agent.py](../../agents/python/orchestrator/agent.py) | 调用远端专业智能体 | 网络失败如何反馈给协调器 |
| [shared/agent_host.py](../../agents/python/shared/agent_host.py) | 专业智能体服务入口 | agent.run 如何接入 HTTP |
| [shared/agent_factory.py](../../agents/python/shared/agent_factory.py) | 模型客户端 | 模型和服务配置怎样生效 |
| [shared/middleware.py](../../agents/python/shared/middleware.py) | 中间件组合 | 运行、聊天、函数三层分别记录什么 |
| [shared/context.py](../../agents/python/shared/context.py) | ContextVars | 用户身份如何沿异步请求传递 |
| [shared/prompt_loader.py](../../agents/python/shared/prompt_loader.py) | YAML 提示词组合 | 角色规则和共享规则如何合成 |

## 五种已注册编排模式

| 模式名 | 含义 | 使用时需要注意 |
|---|---|---|
| tool | 协调器使用 call_specialist_agent 分发 | 协调器与专业智能体可能各调用模型 |
| handoff | 把处理权交给专业智能体 | 与“协调器重新组织所有回答”不同 |
| workflow:pre-purchase | 售前固定工作流 | 工具并发不等于多个模型推理 |
| workflow:return-replace | 退换货顺序工作流 | 存在工作流审批与恢复路径 |
| group-chat | 多个视角讨论后汇总 | 调用量增加，需要证明业务收益 |

当前部分固定工作流可以直接组合工具结果，没有调用模型。不能把这类流程与完成不同任务的多 Agent 流程仅按耗时排名。

## 数据与身份

Python 后端使用 asyncpg 访问 PostgreSQL；pgvector 用于向量检索，Redis 用于聊天限流等功能。前端的用户输入并不构成可信身份，权限应来自后端验证的用户上下文。

专业智能体通过 A2A HTTP 通信。身份、角色、会话可以通过现有调用路径传播；Python ContextVar 本身不会自动跨 HTTP 传播。全任务费用累计、截止时间和取消信号也需要显式设计。

## 两类容易混淆的审批

1. 工具层 HITL：敏感工具被拦截、写入审批记录，管理员批准后由执行入口处理。
2. 工作流层 HITL：固定工作流通过 request_info 暂停，再利用 checkpoint 和回复恢复。

这两种审批的触发位置和执行入口不同。不能因为某条路径有人工确认，就认为所有写入路径已经采用同一套业务约束。详见 [售后代码导航](after-sales-code-map.md)。

## 运行状态不等于业务成功

HTTP 200、没有抛异常、生成了流式文本，都不能单独证明退货申请完成。应一起检查工具结果、操作状态、审批状态和数据库记录。技术方案把这些观察统一到一次业务操作中。
