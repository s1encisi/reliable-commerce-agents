#!/usr/bin/env bash
#
# 针对 Python 后端运行整套 Playwright 端到端测试。
#
# 前端只有一个，它由 Docker Compose 拉起并指向编排器，所以「界面到底能不能
# 用」这个问题只能靠真正驱动它来回答。本脚本就是这道闸门：每修好一个缺陷就
# 从 `web/e2e/parity-gaps.ts` 里删掉一行，当该文件里的记录清空时，一致性对齐
# 工作即告完成。
#
# 串行运行，不并行：Docker Compose 会绑定固定端口（3000、5432、6379、
# 8080-8085、8090、9001），同一时刻只能有一套栈在运行。
#
# 以前每切换一次后端都要重新构建前端，因为 API 地址是构建期内联进 JS chunk
# 的 NEXT_PUBLIC_* 变量 —— 用不同的值启动已有构建毫无作用，而错配会表现为
# 令人困惑的 401（把一个后端的 token 递给另一个后端）。现在不再是问题：前端
# 在服务端代理 /api/*，并逐请求读取 `ORCHESTRATOR_URL`，所以同一个镜像可以
# 服务后端，只是环境变量不同而已。下面的 `up --build` 现在只针对后端。
#
# 它引发的那类故障并没有消失，只是换了位置。前端若带着错误的
# `ORCHESTRATOR_URL` 启动，登录仍会命中真正的后端，而所有 API 层断言却去查询
# 另一个后端；基址覆盖变量是 `E2E_BASE_URL` —— 一次运行若设置了别的变量，就会
# 静默地驱动 :3000 上的那个前端。两种情况都会在登录时被一致性对齐 spec 里的
# `assertFrontendTalksToOrchUrl` 捕获。
#
# 用法:
#   scripts/e2e-stack.sh                        # 整套测试
#   scripts/e2e-stack.sh -- e2e/orchestration-parity.spec.ts
#
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PLAYWRIGHT_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --) shift; PLAYWRIGHT_ARGS=("$@"); break ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

teardown() {
  echo "--- 正在拆除容器栈"
  docker compose -f docker-compose.yml --profile seed --profile agents --profile mcp --profile frontend down --remove-orphans >/dev/null 2>&1 || true
}

echo "======================================================================"
echo "  python 技术栈"
echo "======================================================================"

# 先确保上一次运行没有留下容器占着端口。
teardown

echo "--- 正在启动技术栈"
if ! docker compose -f docker-compose.yml --profile seed --profile agents --profile mcp --profile frontend up -d --build; then
  echo "!!! 技术栈启动失败" >&2
  exit 1
fi

# 端口 3000 不一定就是前端 —— 别的服务可能已经占用了它；这种情况下 compose
# 会启动前端但不发布端口，于是所有 spec 都会因为无关原因在登录环节失败。
# E2E_BASE_URL 允许开发者指向前端真正所在的地址。
BASE_URL="${E2E_BASE_URL:-http://localhost:3000}"

echo "--- 正在等待前端 ($BASE_URL) 与编排器就绪"
ready=0
for _ in $(seq 1 90); do
  if curl -sf -m2 "$BASE_URL" >/dev/null 2>&1 \
     && curl -sf -m2 http://localhost:8080/health >/dev/null 2>&1; then
    ready=1; break
  fi
  sleep 2
done

if [[ "$ready" -ne 1 ]]; then
  echo "!!! 技术栈始终未能就绪" >&2
  docker compose -f docker-compose.yml ps
  teardown
  exit 1
fi

echo "--- 正在针对 python 运行 Playwright"
if (cd web && BACKEND_STACK="python" E2E_BASE_URL="$BASE_URL" npx playwright test "${PLAYWRIGHT_ARGS[@]}"); then
  result="PASSED"
else
  result="FAILED"
fi

teardown

echo
echo "======================================================================"
echo "  汇总"
echo "======================================================================"
if [[ "$result" == "PASSED" ]]; then
  echo "  python   通过"
  exit 0
else
  echo "  python   失败"
  exit 1
fi
