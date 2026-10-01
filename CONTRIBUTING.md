# 贡献指南

本仓库是基于 Microsoft Agent Framework 的 Python 电商 Agent 项目。改动应提升业务正确性、可解释性或演示体验，并保留已有数据与用户改动。

## 环境搭建

需要 Python 3.12、uv、Node.js 22、pnpm 10 和 Docker。在仓库根目录同步锁定依赖：

```bash
uv sync --project agents/python --frozen --all-packages --extra dev
pnpm --dir web install --frozen-lockfile
bash scripts/demo.sh
```

`--all-packages` 包含两个 MCP workspace 成员，避免测试收集时缺少包。Python 使用 uv，前端使用 pnpm。隔离演示不需要模型凭据，数据和端口说明见[演示指南](docs/portfolio-demo.md)；独立容器路径见[容器展示](docs/local-showcase.md)。

真实模型开发环境使用 `scripts/dev.sh` 或 PowerShell 版 `scripts/dev.ps1`，需单独配置提供方。不要用主数据库运行会重置数据的测试或演示种子命令。

## 开发约定

- **Python**：类型标注、async/await、asyncpg 原生 SQL、httpx、Pydantic Settings；可信身份保存在请求 ContextVars。工具参数不能替代认证或审批。
- **提示词**：遵循现有 YAML 提示词配置。提示词变化可能影响模型行为，不能与界面文案翻译混作同一种验证。
- **前端**：Next.js App Router、Tailwind、shadcn/ui 与现有主题 token；使用 Zod 验证接口结果。修改框架代码前阅读 `web/node_modules/next/dist/docs/`。
- **变更范围**：补充行为变更所需的测试，优先验证权限、边界与恢复；不为无关文件做全仓格式化。冻结基线源码和历史结果不能为获得更好的指标而重写。
- **记录与凭据**：内部工作产物保存在仓库外；密钥、本地环境文件和真实用户数据不提交。公开材料只保存合成数据、必要日志与脱敏截图。

## 确定性验证环境

在 Bash 中先设置本轮测试环境，避免自动读取私人 `.env` 或录制真实模型调用：

```bash
export APP_ENV_FILE=/dev/null ENVIRONMENT=test RECORD=false LLM_PROVIDER=replay
export OPENAI_API_KEY= AZURE_OPENAI_KEY= AZURE_OPENAI_API_KEY=
export DEEPSEEK_API_KEY= MOONSHOT_API_KEY= TYPESAFE_API_KEY=
uv run --project agents/python --no-sync python -m pytest agents/python/tests -q
```

涉及数据库和 Redis 的集成测试使用真实隔离 testcontainers，保持默认 reaper 清理。真实模型试验必须另行配置并明确费用范围；跳过的测试要如实记录。用 `python -m pytest` 调用当前虚拟环境解释器，避免移动目录后的旧脚本 shebang。

前端单元测试使用 Vitest；Playwright 默认不启动服务，需先运行隔离演示。`ui-smoke.spec.ts` 使用模拟接口，不能代替真实后端验收。CI 的 `.coveragerc.ci` 对限定模块设置覆盖率门槛，不代表所有 HTTP、数据库和端到端路径都经过相同覆盖验证。

## 开 PR 前

先运行与改动相关的测试，集成收尾时再执行必要的整体检查：

```bash
uv run --project agents/python --no-sync python -m ruff check agents/python
uv run --project agents/python --no-sync python -m ruff format --check agents/python
uv run --project agents/python --no-sync python -m pytest agents/python/tests -q
pnpm --dir web lint
pnpm --dir web exec tsc --noEmit
pnpm --dir web test
pnpm --dir web build
uv run --project agents/python --no-sync python scripts/build_docs_site.py --check
git diff --check
```

上述 Python 测试沿用前一节的隔离环境变量。评估应生成新的带日期文件，保留全部失败及旧报告；工作树未提交时记录实际源码散列，不能只写 Git HEAD。复验协议见[验证说明](docs/verification.md)。

PR 从 main 建分支，说明问题、实际行为变化、验证命令与结果。行为变化同步更新公开文档和 API；远端 CI 未运行时不能仅凭配置文件声称通过。
