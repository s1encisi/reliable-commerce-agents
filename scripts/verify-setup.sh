#!/usr/bin/env bash
# ============================================================
# verify-setup.sh — 可靠电商多智能体平台开发环境自检
# 用法: ./scripts/verify-setup.sh
# ============================================================
# 检查项:
#   - 已安装 uv（Python 包管理器）
#   - Python 3.12+
#   - Docker + docker compose
#   - 存在 .env 文件（否则提示 .env.example）
#   - 针对当前 LLM_PROVIDER 设置了必需的 LLM 环境变量
#
# 成功返回 0，遇到第一个失败项即返回非零。

set -o pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
RESET='\033[0m'

check_count=0
fail_count=0

check() {
    local label="$1"
    local cmd="$2"
    check_count=$((check_count + 1))
    if eval "$cmd" >/dev/null 2>&1; then
        printf "  ${GREEN}✓${RESET} %s\n" "$label"
    else
        printf "  ${RED}✗${RESET} %s\n" "$label"
        fail_count=$((fail_count + 1))
    fi
}

warn() {
    printf "  ${YELLOW}!${RESET} %s\n" "$1"
}

info() {
    printf "  ${GREEN}i${RESET} %s\n" "$1"
}

section() {
    printf "\n${GREEN}%s${RESET}\n" "$1"
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 2

section "工具链"
check "uv（Python 包管理器）" "command -v uv"
check "Python 3.12+" "python3 --version | grep -E 'Python 3\.(12|13|14)'"
check "Docker" "command -v docker"
check "Docker Compose v2" "docker compose version"
check "Node 20+（用于 Next.js 前端）" "node --version | grep -E 'v(20|21|22|23|24)'"
check "pnpm" "command -v pnpm"

section "仓库状态"
if [[ -f "$REPO_ROOT/.env" ]]; then
    info "已存在 .env"
    # shellcheck disable=SC1091
    set -a
    . "$REPO_ROOT/.env"
    set +a
elif [[ -f "$REPO_ROOT/.env.example" ]]; then
    warn "未找到 .env —— 请复制 .env.example → .env 并填入密钥"
    fail_count=$((fail_count + 1))
else
    printf "  ${RED}✗${RESET} 既没有 .env 也没有 .env.example\n"
    fail_count=$((fail_count + 1))
fi

section "LLM 提供商配置"
LLM_PROVIDER="${LLM_PROVIDER:-openai}"
echo "  LLM_PROVIDER=$LLM_PROVIDER"
case "$LLM_PROVIDER" in
    openai)
        if [[ -n "${OPENAI_API_KEY:-}" && "$OPENAI_API_KEY" != "sk-your-openai-api-key-here" ]]; then
            info "已设置 OPENAI_API_KEY"
        else
            printf "  ${RED}✗${RESET} OPENAI_API_KEY 为空或仍是占位值\n"
            fail_count=$((fail_count + 1))
        fi
        ;;
    azure)
        check "已设置 AZURE_OPENAI_ENDPOINT" "[[ -n \"\${AZURE_OPENAI_ENDPOINT:-}\" ]]"
        # 接受 AZURE_OPENAI_KEY（本仓库约定）或 AZURE_OPENAI_API_KEY（MAF 约定）
        if [[ -n "${AZURE_OPENAI_KEY:-}" || -n "${AZURE_OPENAI_API_KEY:-}" ]]; then
            info "已设置 Azure 密钥"
        else
            printf "  ${RED}✗${RESET} AZURE_OPENAI_KEY（或 AZURE_OPENAI_API_KEY）为空\n"
            fail_count=$((fail_count + 1))
        fi
        check "已设置 AZURE_OPENAI_DEPLOYMENT" "[[ -n \"\${AZURE_OPENAI_DEPLOYMENT:-}\${AZURE_OPENAI_DEPLOYMENT_NAME:-}\" ]]"
        ;;
    *)
        printf "  ${RED}✗${RESET} 未知的 LLM_PROVIDER: %s（应为 'openai' 或 'azure'）\n" "$LLM_PROVIDER"
        fail_count=$((fail_count + 1))
        ;;
esac

section "工作区结构"
check "存在 tutorials/ 目录" "[[ -d tutorials ]]"
check "存在 docker-compose.yml" "[[ -f docker-compose.yml ]]"
check "存在 agents/python/ 后端" "[[ -d agents/python ]]"
check "存在 web/ Next.js 前端" "[[ -d web ]]"

section "汇总"
passed=$((check_count - fail_count))
if [[ $fail_count -eq 0 ]]; then
    printf "  ${GREEN}全部 %d 项检查通过。${RESET}\n" "$check_count"
    printf "  可以开始学习教程了 —— 从 ${GREEN}tutorials/01-first-agent/${RESET} 开始。\n"
    exit 0
else
    printf "  ${RED}%d / %d 项检查失败。${RESET}\n" "$fail_count" "$check_count"
    printf "  请修复上方标记为 ${RED}✗${RESET} 的项，然后重新运行本脚本。\n"
    exit 1
fi
