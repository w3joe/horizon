#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

if [[ ! -x .venv/bin/python || ! -d node_modules ]]; then
  printf 'Dependencies missing. Run ./scripts/bootstrap.sh first.\n' >&2
  exit 2
fi

VITE_HORIZON_API_URL=/api npm --workspace @horizon/console run build
exec .venv/bin/python scripts/launch.py "$@"
