#!/usr/bin/env bash
# 安装开发依赖，无需登录，也不会调用大模型。
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CORE_ONLY=false
WITH_IMAGES=false
for arg in "$@"; do
  case "$arg" in
    --core) CORE_ONLY=true ;;
    --images) WITH_IMAGES=true ;;
    --help|-h)
      echo '用法: bash scripts/install-deps.sh [--core] [--images]'
      echo '--core 只安装 Python + Web 演示所需依赖；默认还会安装教程、浏览器与 Hugging Face 相关依赖。'
      echo '--images 额外下载所需的 Docker 基础设施镜像。'
      exit 0 ;;
    *) echo "未知参数: $arg" >&2; exit 2 ;;
  esac
done
for tool in uv node; do
  command -v "$tool" >/dev/null || { echo "缺少前置依赖: $tool" >&2; exit 1; }
done
if [[ "$CORE_ONLY" == false ]]; then
  for tool in rg; do
    command -v "$tool" >/dev/null || { echo "缺少前置依赖: $tool（或使用 --core）" >&2; exit 1; }
  done
fi
if ! command -v pnpm >/dev/null; then
  command -v corepack >/dev/null || { echo '请先安装 Corepack 与 pnpm 10.15.0。' >&2; exit 1; }
  mkdir -p "$HOME/.local/bin"
  corepack enable --install-directory "$HOME/.local/bin"
  corepack prepare pnpm@10.15.0 --activate
  export PATH="$HOME/.local/bin:$PATH"
fi

uv sync --project agents/python --frozen --all-packages --extra dev
pnpm --dir web install --frozen-lockfile
if [[ "$CORE_ONLY" == false ]]; then
  uv sync --project tutorials --frozen --extra dev
  uv tool install 'huggingface_hub==1.31.0' --with 'socksio==1.0.0'
  pnpm --dir web exec playwright install chromium
fi

if [[ "$WITH_IMAGES" == true ]]; then
  images=(pgvector/pgvector:pg16 redis:7-alpine)
  if [[ "$CORE_ONLY" == false ]]; then
    images+=(testcontainers/ryuk:0.8.1 testcontainers/ryuk:0.14.0)
  fi
  command -v docker >/dev/null || { echo '使用 --images 需要 Docker。' >&2; exit 1; }
  if [[ -x "$REPO_ROOT/.local/bin/crane" ]]; then
    # 这些都是公共镜像。在不改动用户 Docker 配置的前提下，把平台相关的
    # 损坏凭据助手排除在感知代理的下载路径之外。
    image_tmp="$(mktemp -d)"
    trap 'rm -rf "$image_tmp"' EXIT
    mkdir -p "$image_tmp/config"
    for image in "${images[@]}"; do
      if docker image inspect "$image" >/dev/null 2>&1; then
        continue
      fi
      DOCKER_CONFIG="$image_tmp/config" "$REPO_ROOT/.local/bin/crane" pull \
        "$image" "$image_tmp/image.tar"
      docker load -i "$image_tmp/image.tar"
    done
  else
    for image in "${images[@]}"; do
      docker pull "$image"
    done
  fi
fi
echo '依赖安装完成。凭据配置与启动方式参见 docs/zh-CN/environment-setup.md。'
