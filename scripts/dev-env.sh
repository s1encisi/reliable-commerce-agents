#!/usr/bin/env bash
# Source this file from any directory: source /path/to/Demo/scripts/dev-env.sh
# Add project tools and optional rootless Chromium libraries to this shell only.
_demo_env_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$_demo_env_root/.local/bin:$HOME/.local/bin:$PATH"
if [[ -d "$_demo_env_root/.local/browser-libs/usr/lib/x86_64-linux-gnu" ]]; then
  export LD_LIBRARY_PATH="$_demo_env_root/.local/browser-libs/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
export NEXT_TELEMETRY_DISABLED=1
if [[ -z "${TESTCONTAINERS_RYUK_CONTAINER_IMAGE:-}" && -f "$_demo_env_root/.local/verified-ryuk-image" ]]; then
  IFS= read -r _demo_ryuk_image < "$_demo_env_root/.local/verified-ryuk-image"
  case "$_demo_ryuk_image" in
    testcontainers/ryuk:verified-*) export TESTCONTAINERS_RYUK_CONTAINER_IMAGE="$_demo_ryuk_image" ;;
  esac
  unset _demo_ryuk_image
fi
unset _demo_env_root
