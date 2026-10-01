#!/usr/bin/env bash
# 本地完整门禁。运行前停止同一目录的Next开发服务器，避免与生产构建共享.next。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/dev-env.sh
export APP_ENV_FILE=/dev/null RECORD=false
export OPENAI_API_KEY= AZURE_OPENAI_KEY= AZURE_OPENAI_API_KEY=
export DEEPSEEK_API_KEY= MOONSHOT_API_KEY= TYPESAFE_API_KEY=
uv sync --project agents/python --frozen --all-packages --extra dev
uv run --project agents/python --no-sync python -m ruff check agents/python
uv run --project agents/python --no-sync python -m ruff format --check agents/python
uv run --project agents/python --no-sync python -m pytest agents/python/tests -q
pnpm --dir web exec tsc --noEmit
pnpm --dir web lint
pnpm --dir web test --maxWorkers=1
pnpm --dir web build
uv run --project agents/python --no-sync python scripts/build_docs_site.py --check
git diff --check
printf '本地代码、测试、构建与文档检查通过。浏览器与评测结果请按对应指南独立复验。\n'
