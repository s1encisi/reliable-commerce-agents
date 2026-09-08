# Python 教程中文导航

[项目首页](../README.md) · [英文教程总览](README.md) · [中文学习指南](../docs/zh-CN/learning-guide.md)

本仓库按小示例讲解 Microsoft Agent Framework，再对应完整电商项目。每章通常包含 README、Python 示例和测试，以及 .NET 对照实现。当前学习主线集中在 Python。

当前提供第 01、02、24、26 章的中文教学版；其他章节保留英文详细内容，本页提供中文主题导航。中文版本不改变示例问题、工具名和 fixture，以维持回放兼容性。

## 章节目录

| 编号 | 中文主题 | 阅读入口 |
|---|---|---|
| 00 | 环境准备 | [英文](00-setup/README.md) / [中文运行指南](../docs/zh-CN/quick-start.md) |
| 01 | 第一个智能体 | [中文](01-first-agent/README.zh-CN.md) |
| 02 | 添加工具 | [中文](02-add-tools/README.zh-CN.md) |
| 03 | 流式输出与多轮对话 | [英文](03-streaming-and-multiturn/README.md) |
| 04 | 会话 | [英文](04-sessions/README.md) |
| 05 | 上下文提供器 | [英文](05-context-providers/README.md) |
| 06 | 中间件与执行管线 | [英文](06-middleware/README.md) |
| 07 | OpenTelemetry 可观测性 | [英文](07-observability-otel/README.md) |
| 08 | MCP 工具 | [英文](08-mcp-tools/README.md) |
| 09 | 工作流执行器与边 | [英文](09-workflow-executors-and-edges/README.md) |
| 10 | 工作流事件与构建器 | [英文](10-workflow-events-and-builder/README.md) |
| 11 | 工作流中的智能体 | [英文](11-agents-in-workflows/README.md) |
| 12 | 顺序编排 | [英文](12-sequential-orchestration/README.md) |
| 13 | 并发编排 | [英文](13-concurrent-orchestration/README.md) |
| 14 | 处理权交接 | [英文](14-handoff-orchestration/README.md) |
| 15 | 群聊编排 | [英文](15-group-chat-orchestration/README.md) |
| 16 | Magentic 动态编排 | [英文](16-magentic-orchestration/README.md) |
| 17 | 人工参与与审批 | [英文](17-human-in-the-loop/README.md) |
| 18 | 状态与检查点 | [英文](18-state-and-checkpoints/README.md) |
| 19 | 声明式工作流 | [英文](19-declarative-workflows/README.md) |
| 20 | 工作流可视化 | [英文](20-visualization/README.md) |
| 20b | DevUI 调试界面 | [英文](20b-devui/README.md) |
| 21 | 完整项目导览 | [英文](21-capstone-tour/README.md) |
| 22 | 圆桌讨论 | [英文](22-group-chat-debate/README.md) |
| 23 | A2A 通信协议 | [英文](23-a2a-protocol/README.md) |
| 24 | 检索与事实核验 | [中文](24-rag-and-grounding/README.zh-CN.md) |
| 25 | 防护机制 | [英文](25-guardrails/README.md) |
| 26 | 智能体评估 | [中文](26-evals/README.zh-CN.md) |
| 27 | 把智能体作为工具 | [英文](27-agent-as-tool/README.md) |
| 28 | 反思与批评 | [英文](28-reflection-and-critique/README.md) |
| 29 | 规划器与执行器 | [英文](29-planner-executor/README.md) |
| 30 | 子工作流 | [英文](30-subworkflows/README.md) |
| 31 | 重试与补偿 | [英文](31-retry-and-compensation/README.md) |
| 32 | 成本控制与预算 | [英文](32-cost-control-and-budgets/README.md) |

上游状态表由脚本检查文件覆盖情况；有测试项目不等于本机或线上已经运行通过。00、21 等章节不是独立可运行小项目；一些后期章节演示的机制没有接入主应用，阅读各章的适用边界。

## 统一 Python 环境

从仓库根目录执行：

    uv sync --project tutorials --extra dev
    $env:LLM_PROVIDER = "replay"
    $env:RECORD = "false"
    uv run --project tutorials python tutorials/02-add-tools/python/main.py

每次只运行当前章节的非真实模型测试：

    uv run --project tutorials pytest tutorials/02-add-tools/python/tests -m "not integration" -v

全部教程一次收集还有模块名隔离等细节，先逐章学习。20b 的独立依赖见原文。真实模型测试及录制必须明确模型、输入和费用预算。

## 本次个人改进的阅读路径

01 → 02 → 售后代码导航 → 17 / 18 → 23 / 31 → 24 / 26。

顺序按任务需要安排，无需先读完全部章节。[售后可靠性技术方案](../docs/zh-CN/after-sales-reliability-plan.md)定义了当前实施边界和验收方法；阅读完教程不自动代表方案已完成。
