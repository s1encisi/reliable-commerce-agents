# 微软智能体框架（MAF）v1 完整教程系列

[项目首页](../README.md) · [中文文档导航](../docs/zh-CN/README.md) · 中文学习指南 · [快速上手](../docs/zh-CN/quick-start.md)

逐章讲解**微软智能体框架（Microsoft Agent Framework，MAF）**，**每一章都配有可运行的 Python 示例**。
整个系列从单个智能体出发，逐步构建到本仓库中完整的多智能体作品项目。

每一章自成一体，位于 `tutorials/` 下各自的目录中，并包含：

- `README.md` —— 本章讲解正文。这是权威产物，其结构由 CI 中的
  `scripts/check_tutorial_readmes.py` 强制校验。
- `python/` —— 最小可运行示例，测试位于 `python/tests/`。

**状态表是唯一事实来源，而且它确实是自动生成的。**
`scripts/check_tutorial_coverage.py` 从磁盘上的文件推导出每一个单元格——某章被标记为「已测试」是因为存在测试工程，而不是因为有人在表里写了「已测试」——若已提交的表格与实际情况不一致，CI 会失败。

`tutorials/_template/PLAN.md` 是撰写新章节的模板；各章节自身不附带该文件。

---

## 学习路径

**下列每一章的代码都在 CI 中受门禁保护。** 状态列描述的是可运行示例的状态，而非讲解正文的状态。

- **可运行 · CI 已覆盖** —— 有可运行示例，且测试在每个拉取请求上都会执行
  （`.github/workflows/tutorials.yml`）。
- **可运行 · 测试待补** —— 代码可运行，测试尚未编写。
- **仅指南** / **计划中** —— 按设计不含代码，或尚未开始。

每一章自己的 `README.md` 才是权威且始终最新的来源。

<!-- BEGIN GENERATED COVERAGE TABLE -->
| # | 章节 | 状态 |
|---|------|------|
| 00 | [环境准备](./00-setup/) | 仅指南 |
| 01 | [第一个智能体](./01-first-agent/) | 可运行 · CI 已测试 |
| 02 | [添加工具](./02-add-tools/) | 可运行 · CI 已测试 |
| 03 | [流式输出与多轮对话](./03-streaming-and-multiturn/) | 可运行 · CI 已测试 |
| 04 | [会话持久化](./04-sessions/) | 可运行 · CI 已测试 |
| 05 | [上下文提供器](./05-context-providers/) | 可运行 · CI 已测试 |
| 06 | [中间件与智能体管线](./06-middleware/) | 可运行 · CI 已测试 |
| 07 | [基于 OpenTelemetry 的可观测性](./07-observability-otel/) | 可运行 · CI 已测试 |
| 08 | [MCP 工具](./08-mcp-tools/) | 可运行 · CI 已测试 |
| 09 | [工作流执行器与边](./09-workflow-executors-and-edges/) | 可运行 · CI 已测试 |
| 10 | [工作流事件与构建器](./10-workflow-events-and-builder/) | 可运行 · CI 已测试 |
| 11 | [工作流中的智能体](./11-agents-in-workflows/) | 可运行 · CI 已测试 |
| 12 | [顺序编排](./12-sequential-orchestration/) | 可运行 · CI 已测试 |
| 13 | [并发编排](./13-concurrent-orchestration/) | 可运行 · CI 已测试 |
| 14 | [移交式编排（Handoff）](./14-handoff-orchestration/) | 可运行 · CI 已测试 |
| 15 | [群聊编排](./15-group-chat-orchestration/) | 可运行 · CI 已测试 |
| 16 | [Magentic 编排](./16-magentic-orchestration/) | 可运行 · CI 已测试 |
| 17 | [人在回路](./17-human-in-the-loop/) | 可运行 · CI 已测试 |
| 18 | [状态与检查点](./18-state-and-checkpoints/) | 可运行 · CI 已测试 |
| 19 | [声明式工作流](./19-declarative-workflows/) | 可运行 · CI 已测试 |
| 20 | [工作流可视化](./20-visualization/) | 可运行 · CI 已测试 |
| 20b | [DevUI：智能体与工作流的交互式面板](./20b-devui/) | 可运行 · CI 已测试 |
| 21 | [完整项目导览](./21-capstone-tour/) | 规划中 |
| 22 | [群聊辩论（圆桌编排）](./22-group-chat-debate/) | 可运行 · CI 已测试 |
| 23 | [A2A 协议](./23-a2a-protocol/) | 可运行 · CI 已测试 |
| 24 | [检索与事实核验](./24-rag-and-grounding/) | 可运行 · CI 已测试 |
| 25 | [护栏](./25-guardrails/) | 可运行 · CI 已测试 |
| 26 | [智能体评估](./26-evals/) | 可运行 · CI 已测试 |
| 27 | [把智能体作为工具](./27-agent-as-tool/) | 可运行 · CI 已测试 |
| 28 | [反思与批评](./28-reflection-and-critique/) | 可运行 · CI 已测试 |
| 29 | [规划器与执行器](./29-planner-executor/) | 可运行 · CI 已测试 |
| 30 | [子工作流](./30-subworkflows/) | 可运行 · CI 已测试 |
| 31 | [重试与补偿（Saga 模式）](./31-retry-and-compensation/) | 可运行 · CI 已测试 |
| 32 | [成本控制与预算](./32-cost-control-and-budgets/) | 可运行 · CI 已测试 |
<!-- END GENERATED COVERAGE TABLE -->

> 表中「可运行」只表示磁盘上存在示例与测试工程，并不等于本机或线上已实际跑通。
> 00 与 21 两章不是独立可运行的小项目，其适用边界见各自 README。

---

## 分层结构

- **第 1 层 —— 核心智能体**（第 01–04 章）：从空白编辑器走到可用的智能体所需的最小集合。
- **第 2 层 —— 智能体内部机制**（第 05–08 章）：记忆、中间件、遥测、MCP。
- **第 3 层 —— 工作流基础**（第 09–11 章）：执行器、边、事件、把智能体包进工作流。
- **第 4 层 —— 编排模式**（第 12–16 章）：五种内置的多智能体模式。
- **第 5 层 —— 进阶主题**（第 17–20 章）：人工参与、检查点、声明式工作流、可视化。
- **作品项目导览**（第 21 章）：带读本仓库，指出每一个概念的实际落点。
- **附加模式**（第 22 章）：第六种编排模式——圆桌式群聊——在作品项目导览之后追加。本章的 Python 侧直接导入生产代码中的 `workflows/group_chat.py` 模块，带读真实上线代码；因此它与前几章「最小可运行示例」的定位不同，属于「导览 + 真实代码」。
- **第 6 层 —— 补全缺失概念**（第 23–27 章）：这些模式在本仓库的生产代码中已经存在，但此前从未在教程中讲解——A2A 协议、检索与事实核验、护栏、评测、智能体即工具。每一章都自成一体、可独立运行且不依赖额外依赖，并交叉链接到 `docs/concepts/` 下对应的概念页，而不是重复推导「为什么」。
- **第 7 层 —— 尚未接入生产链路的模式**（第 28–31 章）：反思与批评、规划器与执行器、子工作流、重试与补偿（Saga）。它们均以独立、无额外依赖的示例形式讲解，而不新增编排模式——因为模式注册表每新增一个模式，都要连带处理 SSE、界面与测试，对一个章节而言成本不成比例。第 29 章明确交叉引用了尚未实现的 Magentic 模式，作为规划器与执行器思路最终的生产形态，这样将来就不必再把一个临时实现与它对齐。第 30 章讲解 MAF 真正的嵌套原语——Python 中的 `WorkflowExecutor`——并如实说明 `return_replace.py` 目前并未使用它。第 31 章属于完全从零开始的内容——本仓库此前不存在任何 Saga / 补偿相关代码。
- **第 32 章 —— 成本控制与预算**：第 7 层「仅独立示例」原则的唯一例外。它包含少量、规模相称的生产代码——`CostBudgetMiddleware`（`agents/python/shared/guardrails/cost_budget_middleware.py`）——补上了一个真实缺口（`estimate_cost()` 此前没有任何运行时调用方，只有事后评测的报表在用），且不新增编排模式、不涉及任何界面或 SSE 面，因此能容纳在一章之内。

**本系列声明暂不覆盖的范围**：多租户、模型微调、智能体市场、语音。这不是遗漏，而是有意暂不涉及。

---

## 前置条件

- Python 3.12+ 与 [`uv`](https://docs.astral.sh/uv/)
- Docker + Docker Compose
- 一个 OpenAI 或 Azure OpenAI 密钥（配置在仓库根目录的 `.env` 中）

分步安装说明见 [第 00 章 —— 环境准备](./00-setup/)。

---

## 运行某一章

所有 Python 章节共用 `tutorials/pyproject.toml` 这一个 uv 工程——执行一次
`uv sync --project tutorials` 即可装齐所有章节所需的依赖，并且所有命令都在
**仓库根目录**下执行，而不是进入章节目录内部：

```bash
uv sync --project tutorials --extra dev
uv run --project tutorials python tutorials/01-first-agent/python/main.py
uv run --project tutorials pytest tutorials/01-first-agent/python/tests -v
```

每一章的测试都跑在脚本化的回放客户端（`tutorials/_shared/replay_client.py`）之上，
因此测试无需任何密钥、也不发起网络请求。它会记录真正送达模型的内容，正因如此，
编排类章节才能断言那些从源码本身看不出来的性质——例如第 13 章的智能体在时间上确实
重叠、而第 12 章的确实不重叠，或者一个处理权交接的目标仅仅通过工具的*描述*被识别出来。

只想运行当前章节、且不调用真实模型的测试：

```bash
uv run --project tutorials pytest tutorials/02-add-tools/python/tests -m "not integration" -v
```

第 20b 章（DevUI）自带独立的 `pyproject.toml`，沿用
`cd tutorials/20b-devui/python && uv sync` 的流程——详见该章 README。第 22 章的 Python
侧直接导入 `agents/python` 下生产代码的 `workflows.group_chat` 模块；其测试会把该目录
加入 `sys.path`，以便仍能在 `tutorials/` 工程下被收集。第 00 与第 21 章没有独立可运行
代码，应运行什么请见各自 README。

涉及真实模型的测试与录制，必须事先明确模型、输入内容与费用预算。

## 重新生成状态表

```bash
python scripts/check_tutorial_coverage.py            # 打印磁盘上的实际情况
python scripts/check_tutorial_coverage.py --write    # 重写上方的状态表
python scripts/check_tutorial_coverage.py --check    # CI 实际执行的检查
```

为某一章新增测试工程，就足以改变该章对应的单元格——不存在第二个需要同步修改的地方；
若忘记重新生成，CI 会失败。

## 建议阅读路径

主线按顺序阅读即可。若时间有限、需要按任务取舍，可参考这条精简路径：

01 → 02 → 17 / 18 → 23 / 31 → 24 / 26

即：先掌握单个智能体与工具调用，再理解人工参与审批与状态检查点，然后是智能体间通信协议
与重试补偿，最后是检索事实核验与评测。顺序可按实际需要调整，不必先读完所有章节。
