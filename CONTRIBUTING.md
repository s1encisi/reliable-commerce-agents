# 贡献指南

感谢你的关注。本仓库是微软智能体框架（Microsoft Agent Framework，MAF）v1 多智能体模式的展示项目；
欢迎能提升可读性、测试覆盖或演示体验的 PR。

## 环境搭建

```bash
./scripts/dev.sh            # 全栈（Postgres + Redis + Jaeger + 智能体 + Web）
./scripts/dev.ps1           # 同上，PowerShell 版（Windows，或 macOS/Linux 上的 pwsh 7）
# 也可以按需分步：
cd agents/python && uv sync --extra dev      # Python（用 uv，不用 pip/poetry）
cd web && pnpm install                        # 前端（用 pnpm，不用 npm/yarn）
```

需要 Docker、`uv` 与 `pnpm`。隔离的求职作品演示与确定性测试不需要任何模型凭据。真实模型对话需要显式配置提供方；见 `docs/portfolio-demo.md`。

## 约定

约定集中在 [`CLAUDE.md`](./CLAUDE.md)（权威指南）中。要点如下：

- **Python**：处处使用类型标注、`async`、`asyncpg`（不用 ORM）、`httpx`（不用
  `requests`）、Pydantic Settings、请求状态用 ContextVars、MAF `@tool`
  装饰器、YAML 提示词（不得硬编码提示词字符串）。用 `ruff` 做代码检查。
- **前端**：Next.js 16 App Router、Tailwind 4 + shadcn/ui、OKLCH **主题
  token**（绝不硬编码 slate/white —— 那会破坏暗色模式）、运行时校验用 Zod。
  改动框架代码前先读 `node_modules/next/dist/docs/`。
- 工作产物（记忆、规则、计划）存放在仓库内的 `.claude/` 下。

## 测试（硬性要求）

每一处改动都要随附测试。

- **Python**：`cd agents/python && uv run pytest`。集成测试使用
  **testcontainers**（真实 Postgres）。确定性的工作流/工具测试不发起真实模型调用；
  真实模型测试需显式开启，且无凭据时会被跳过。CI 通过 `.coveragerc.ci` 对可单测面
  强制覆盖率要求（本地/集成运行使用 `pyproject.toml` 中完整的 70% 门槛）。
- **前端**：`cd web && pnpm test`（vitest）+ `pnpm exec playwright test`
  （E2E；`ui-smoke.spec.ts` 不依赖后端）。

## 完成定义（开 PR 前执行）

```bash
# Python
cd agents/python && uv run ruff check . && uv run ruff format --check . && uv run pytest
# 前端
cd web && pnpm lint && pnpm exec tsc --noEmit && pnpm test && pnpm build
```

## PR

- 从 `main` 拉分支；保持 PR 聚焦。
- 说明改了什么、如何验证（命令 + 结果）。
- 行为变更时同步更新文档；补充/调整测试。
- CI（`.github/workflows/tests.yml`）必须全绿。
