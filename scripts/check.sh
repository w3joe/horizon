#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

if [[ ! -x .venv/bin/python ]]; then
  printf 'Missing .venv. Run ./scripts/bootstrap.sh first.\n' >&2
  exit 2
fi

export PYTHONPATH="$repo_root:$repo_root/packages/contracts/python:$repo_root/services/simulator:$repo_root/services/collector:$repo_root/services/fusion:$repo_root/services/perception:$repo_root/services/neural-health:$repo_root/adapters/maritime${PYTHONPATH:+:$PYTHONPATH}"
.venv/bin/python scripts/generate_contract_types.py --check
.venv/bin/python scripts/validate_contracts.py

test_paths=(packages/contracts/python/tests)
for candidate in experiment/tests services scripts/tests tests; do
  if [[ -d "$candidate" ]]; then
    test_paths+=("$candidate")
  fi
done
.venv/bin/python -m pytest -q "${test_paths[@]}"
npm run check
