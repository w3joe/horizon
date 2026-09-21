#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

if [[ ! -x .venv/bin/python ]]; then
  "$repo_root/scripts/python.sh" -m venv .venv
fi

.venv/bin/python -m pip install --disable-pip-version-check -e '.[dev]'
npm ci

printf 'Horizon dependencies are ready. Run ./scripts/check.sh or ./scripts/launch_cpu.sh.\n'
