#!/usr/bin/env bash
# Install development dependencies without logging in or making LLM calls.
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
      echo 'Usage: bash scripts/install-deps.sh [--core] [--images]'
      echo '--core installs only the Python + web demo; default includes tutorials, .NET, browsers and Hugging Face.'
      echo '--images also downloads the selected Docker infrastructure images.'
      exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done
for tool in uv node; do
  command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool" >&2; exit 1; }
done
if [[ "$CORE_ONLY" == false ]]; then
  for tool in dotnet rg; do
    command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool (or use --core)" >&2; exit 1; }
  done
fi
if ! command -v pnpm >/dev/null; then
  command -v corepack >/dev/null || { echo 'Install Corepack and pnpm 10.15.0 first.' >&2; exit 1; }
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
  dotnet restore agents/dotnet/ECommerceAgents.sln --verbosity minimal
  while IFS= read -r project; do
    dotnet restore "$project" --verbosity minimal
  done < <(rg --files tutorials -g '*.csproj' | sort)
fi

if [[ "$WITH_IMAGES" == true ]]; then
  images=(pgvector/pgvector:pg16 redis:7-alpine)
  if [[ "$CORE_ONLY" == false ]]; then
    images+=(mcr.microsoft.com/dotnet/aspire-dashboard:latest testcontainers/ryuk:0.8.1 testcontainers/ryuk:0.14.0)
  fi
  command -v docker >/dev/null || { echo 'Docker is required for --images.' >&2; exit 1; }
  if [[ -x "$REPO_ROOT/.local/bin/crane" ]]; then
    # These are public images. Keep broken platform-specific credential helpers
    # out of the proxy-aware download path without changing user Docker config.
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
echo 'Dependencies installed. See docs/zh-CN/environment-setup.md for credentials and startup.'
