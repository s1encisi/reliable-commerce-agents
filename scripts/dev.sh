#!/usr/bin/env bash
# ============================================================
# 可靠电商多智能体平台 — 开发环境启动脚本
# 用法：
#   ./scripts/dev.sh              完整重建并启动全部服务（Python 后端）
#   ./scripts/dev.sh --clean      删除数据卷，从零重建
#   ./scripts/dev.sh --seed-only  对已有数据库重跑种子数据
#   ./scripts/dev.sh --infra-only 只启动 db + redis + jaeger
#   ./scripts/dev.sh --demo       拉取预构建镜像启动演示栈（不本地构建）
#
# 各参数可自由组合，例如 --clean --demo。
# ============================================================

set -euo pipefail

# ── 颜色 ─────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── 辅助函数 ─────────────────────────────────────────────────

info()    { echo -e "${BLUE}[信息]${NC}  $*"; }
success() { echo -e "${GREEN}[完成]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[警告]${NC}  $*"; }
error()   { echo -e "${RED}[错误]${NC}  $*"; }
step()    { echo -e "\n${BOLD}${CYAN}── $* ──${NC}\n"; }

wait_for_health() {
    local name="$1"
    local check_cmd="$2"
    local max_retries="${3:-30}"
    local retry=0

    info "等待 ${name} 就绪..."
    while [ $retry -lt $max_retries ]; do
        if eval "$check_cmd" > /dev/null 2>&1; then
            success "${name} 已就绪"
            return 0
        fi
        retry=$((retry + 1))
        sleep 1
    done
    error "${name} 在 ${max_retries} 秒内未就绪"
    return 1
}

wait_for_http() {
    local name="$1"
    local url="$2"
    local max_retries="${3:-60}"
    local retry=0

    info "等待 ${name}（${url}）..."
    while [ $retry -lt $max_retries ]; do
        if curl -sf "$url" > /dev/null 2>&1; then
            success "${name} 已就绪"
            return 0
        fi
        retry=$((retry + 1))
        sleep 1
    done
    error "${name} 在 ${max_retries} 秒内未响应（${url}）"
    return 1
}

print_summary() {
    echo ""
    echo -e "${BOLD}${CYAN}============================================================${NC}"
    echo -e "${BOLD}${CYAN}  可靠电商多智能体平台 — 服务已启动${NC}"
    echo -e "${BOLD}${CYAN}============================================================${NC}"
    echo ""
    echo -e "  ${BOLD}基础设施${NC}"
    echo -e "    PostgreSQL        http://localhost:5432"
    echo -e "    Redis             http://localhost:6379"
    echo -e "    ${GREEN}Jaeger（追踪）    http://localhost:16686${NC}"
    echo ""

    if [ "${INFRA_ONLY:-false}" = "false" ] && [ "${SEED_ONLY:-false}" = "false" ]; then
        echo -e "  ${BOLD}智能体${NC}"
        echo -e "    编排器            http://localhost:8080"
        echo -e "    商品发现          http://localhost:8081"
        echo -e "    订单管理          http://localhost:8082"
        echo -e "    定价与促销        http://localhost:8083"
        echo -e "    评论情感分析      http://localhost:8084"
        echo -e "    库存与履约        http://localhost:8085"
        echo ""
        echo -e "  ${BOLD}前端${NC}"
        echo -e "    Next.js           http://localhost:3000"
        echo ""
    fi

    echo -e "${BOLD}${CYAN}============================================================${NC}"
    echo ""
}

# ── 解析参数 ─────────────────────────────────────────────────

CLEAN=false
SEED_ONLY=false
INFRA_ONLY=false
DEMO=false

for arg in "$@"; do
    case $arg in
        --clean)      CLEAN=true ;;
        --seed-only)  SEED_ONLY=true ;;
        --infra-only) INFRA_ONLY=true ;;
        --demo)       DEMO=true ;;
        --help|-h)
            echo "用法：./scripts/dev.sh [选项]"
            echo ""
            echo "选项："
            echo "  --demo        拉取 GHCR 预构建镜像，不进行本地构建"
            echo "  --clean       删除数据卷并从零重建"
            echo "  --seed-only   对已有数据库重跑种子数据"
            echo "  --infra-only  只启动 db + redis + jaeger"
            echo "  --help        显示本帮助"
            echo ""
            echo "  --demo 是最快的路径：无需本地构建，一条命令，约 2 分钟。"
            echo "  可用 IMAGE_TAG 指定镜像标签（默认 latest）："
            echo "    IMAGE_TAG=main ./scripts/dev.sh --demo"
            exit 0
            ;;
        *)
            error "未知参数：$arg"
            exit 1
            ;;
    esac
done

# ── 技术栈选择 ───────────────────────────────────────────────
# docker-compose.yml 把 agents / seeder / frontend 放在 profile 之后，
# 只有基础设施（db、redis、jaeger）是无条件启动的。下面用数组承载
# compose 命令与 profile 参数，避免在脚本各处硬编码。
#   APP_PROFILES — down / build：全部受 profile 控制的服务（含 seed）
#   RUN_PROFILES — 最终 `up -d`：只启动 agents 与 frontend。种子数据已在
#                  前面的 `run --rm` 一次性步骤中执行，这里排除以免重复播种。

COMPOSE=(docker compose)
APP_PROFILES=(--profile seed --profile agents --profile frontend)
RUN_PROFILES=(--profile agents --profile frontend)
PGDATA_VOLUME_PATTERN='_pgdata$'
PROJECT_NAME="reliable-commerce-agents"

# ── 切换到项目根目录 ─────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ── 检查前置依赖 ─────────────────────────────────────────────

if ! command -v docker &> /dev/null; then
    error "未检测到 Docker，或 Docker 不在 PATH 中"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    error "需要 Docker Compose v2"
    exit 1
fi

# ── 检查 .env 文件 ───────────────────────────────────────────

if [ ! -f .env ]; then
    warn "未找到 .env 文件，正在从 .env.example 复制..."
    if [ -f .env.example ]; then
        cp .env.example .env
        warn "已从 .env.example 创建 .env —— 请填入你的 API 密钥"
    else
        error "也没有找到 .env.example。请先手动创建 .env 文件。"
        exit 1
    fi
fi

# ── 清理（按需）─────────────────────────────────────────────

if [ "$CLEAN" = true ]; then
    step "清理中（删除容器、数据卷与孤儿容器）"
    "${COMPOSE[@]}" "${APP_PROFILES[@]}" down -v --remove-orphans
    # Jaeger 的 all-in-one 默认使用内存存储，没有持久卷；
    # 显式删除容器以确保历史追踪数据被清空。
    "${COMPOSE[@]}" rm -f jaeger 2>/dev/null || true
    success "清理完成 —— 容器、数据卷与 Jaeger 追踪数据均已清除"
fi

# ── 演示模式 ─────────────────────────────────────────────────
#
# 刻意做成带独立退出的自包含快速路径，而不是把条件分支穿插到下面的
# 构建 / 播种 / 启动流程里。原因：那条流程里的绝大多数步骤在这里都不适用 ——
# 没有构建步骤，docker-compose.demo.yml 不带 agents/frontend/seed profile，
# 且它用 `depends_on: service_completed_successfully` 让 compose 自己
# 安排种子数据的顺序。独立开来可以确保 --demo 不会影响源码构建路径。

if [ "$DEMO" = true ]; then
    DEMO_COMPOSE=(docker compose -f docker-compose.demo.yml)
    TAG="${IMAGE_TAG:-latest}"

    step "演示模式 —— 拉取预构建镜像（标签：${TAG}）"
    info "无需本地构建。镜像来自 ghcr.io/s1encisi/reliable-commerce-agents"

    if ! IMAGE_TAG="$TAG" "${DEMO_COMPOSE[@]}" pull; then
        error "有一个或多个镜像拉取失败。"
        echo ""
        echo "  最可能的原因："
        echo "    - 该标签尚不存在。请查看 https://github.com/s1encisi/reliable-commerce-agents/pkgs/container/reliable-commerce-agents%2Forchestrator"
        echo "    - 某个镜像包仍为私有。匿名拉取会返回看起来像网络问题的鉴权错误，"
        echo "      详见 docs/releasing.md。"
        echo ""
        echo "  如需改为从源码构建，去掉 --demo 参数即可："
        echo "    ./scripts/dev.sh"
        exit 1
    fi
    success "镜像已拉取"

    step "启动服务"
    IMAGE_TAG="$TAG" "${DEMO_COMPOSE[@]}" up -d

    wait_for_http "编排器" "http://localhost:8080/health" 120
    wait_for_http "前端" "http://localhost:3000" 120

    print_summary
    echo -e "  使用 ${BOLD}zhangwei@example.com${NC} / ${BOLD}customer123${NC} 登录"
    echo ""
    exit 0
fi

# ── 仅播种 ───────────────────────────────────────────────────

if [ "$SEED_ONLY" = true ]; then
    step "执行种子数据"

    # 确保基础设施已启动
    "${COMPOSE[@]}" up -d db redis jaeger
    wait_for_health "PostgreSQL" "${COMPOSE[*]} exec db pg_isready -h 127.0.0.1 -U ecommerce -d ecommerce_agents" 60
    wait_for_health "Redis" "${COMPOSE[*]} exec redis redis-cli ping"

    # 校验数据库凭据（用于发现过期的数据卷）
    if ! "${COMPOSE[@]}" exec -T db sh -c 'PGPASSWORD=ecommerce_secret psql -h 127.0.0.1 -U ecommerce -d ecommerce_agents -c "SELECT 1"' > /dev/null 2>&1; then
        warn "数据库鉴权失败 —— 数据卷已过期。正在重新初始化..."
        "${COMPOSE[@]}" stop db
        "${COMPOSE[@]}" rm -f db
        docker volume ls -q | grep "$PGDATA_VOLUME_PATTERN" | xargs docker volume rm 2>/dev/null || true
        "${COMPOSE[@]}" up -d db
        wait_for_health "PostgreSQL" "${COMPOSE[*]} exec db pg_isready -h 127.0.0.1 -U ecommerce -d ecommerce_agents" 60
        success "数据库已用正确凭据重新初始化"
    fi

    "${COMPOSE[@]}" --profile seed run --rm seeder
    success "种子数据执行完成"
    exit 0
fi

# ── 停止已有容器 ─────────────────────────────────────────────

step "停止已有容器"
"${COMPOSE[@]}" "${APP_PROFILES[@]}" down --remove-orphans 2>/dev/null || true

# ── 构建 ─────────────────────────────────────────────────────

step "构建镜像"
if [ "$CLEAN" = true ]; then
    "${COMPOSE[@]}" "${APP_PROFILES[@]}" build --no-cache
else
    "${COMPOSE[@]}" "${APP_PROFILES[@]}" build
fi

# ── 启动基础设施 ─────────────────────────────────────────────

step "启动基础设施（db、redis、jaeger）"
"${COMPOSE[@]}" up -d db redis jaeger

wait_for_health "PostgreSQL" "${COMPOSE[*]} exec db pg_isready -h 127.0.0.1 -U ecommerce -d ecommerce_agents" 60
wait_for_health "Redis" "${COMPOSE[*]} exec redis redis-cli ping"

# 校验数据库凭据（用于发现使用旧密码的过期数据卷）
if ! "${COMPOSE[@]}" exec -T db sh -c 'PGPASSWORD=ecommerce_secret psql -h 127.0.0.1 -U ecommerce -d ecommerce_agents -c "SELECT 1"' > /dev/null 2>&1; then
    warn "数据库鉴权失败 —— 检测到过期数据卷。正在重新初始化..."
    "${COMPOSE[@]}" stop db
    "${COMPOSE[@]}" rm -f db
    docker volume ls -q | grep "$PGDATA_VOLUME_PATTERN" | xargs docker volume rm 2>/dev/null || true
    "${COMPOSE[@]}" up -d db
    wait_for_health "PostgreSQL" "${COMPOSE[*]} exec db pg_isready -h 127.0.0.1 -U ecommerce -d ecommerce_agents" 60
    success "数据库已用正确凭据重新初始化"
fi

# 校验表结构是否匹配，而不只是凭据是否正确。
#
# 数据卷可能鉴权完全正常，但表结构仍是上一次 init.sql 变更之前的版本。
# 这是更常见的踩坑方式 —— 拉取新提交后重启同一个栈，而不是切换技术栈 ——
# 而且比鉴权失败更难排查，因为它不报任何错误：检索只是静默返回空结果，
# 智能体回复「没有找到相关商品」，读起来像是商品目录为空，而不是索引损坏。
#
# products.search_vector 是哨兵字段：它随全文检索功能一起引入，
# 任何早于该版本的数据卷都缺少它。这里刻意硬编码 —— 从 init.sql 动态推导
# 看似更聪明，但会随文件变化而漂移。
if ! "${COMPOSE[@]}" exec -T db sh -c 'PGPASSWORD=ecommerce_secret psql -h 127.0.0.1 -U ecommerce -d ecommerce_agents -tAc "SELECT 1 FROM information_schema.columns WHERE table_name = '"'"'products'"'"' AND column_name = '"'"'search_vector'"'"'"' 2>/dev/null | grep -q 1; then
    echo ""
    warn "数据库表结构已过期 —— 缺少 products.search_vector 字段。"
    echo ""
    echo "  你的 Postgres 数据卷早于全文检索功能。该栈仍会正常启动并显示健康，"
    echo "  但每次商品检索都会静默返回空结果。为避免进入这种状态，已拒绝启动。"
    echo ""
    echo "  两种处理方式："
    echo ""
    echo "    重建数据卷（会丢失本地数据，从零重新播种）："
    echo "      ./scripts/dev.sh --clean"
    echo ""
    echo "    或就地更新表结构，保留现有数据："
    echo "      docker compose exec -T db psql -U ecommerce -d ecommerce_agents < docker/postgres/init.sql"
    echo ""
    exit 1
fi

success "基础设施已就绪"

# ── 执行种子数据 ─────────────────────────────────────────────

step "执行数据库种子数据"
"${COMPOSE[@]}" --profile seed run --rm seeder
success "数据库播种完成"

# ── 仅基础设施模式 ───────────────────────────────────────────

if [ "$INFRA_ONLY" = true ]; then
    INFRA_ONLY=true print_summary
    success "仅基础设施模式 —— 未启动智能体"
    exit 0
fi

# ── 启动智能体 ───────────────────────────────────────────────

step "启动智能体与前端"
"${COMPOSE[@]}" "${RUN_PROFILES[@]}" up -d

wait_for_http "编排器"        "http://localhost:8080/health"
wait_for_http "商品发现"      "http://localhost:8081/health"
wait_for_http "订单管理"      "http://localhost:8082/health"
wait_for_http "定价与促销"    "http://localhost:8083/health"
wait_for_http "评论情感分析"  "http://localhost:8084/health"
wait_for_http "库存与履约"    "http://localhost:8085/health"

success "全部智能体已启动"

wait_for_http "前端" "http://localhost:3000" 90

success "前端已启动"

# ── 汇总 ─────────────────────────────────────────────────────

print_summary
success "可靠电商多智能体平台已就绪！"
