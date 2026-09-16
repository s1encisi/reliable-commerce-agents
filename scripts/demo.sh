#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/dev-env.sh
exec uv run --project agents/python --no-sync python scripts/run_demo.py "$@"
