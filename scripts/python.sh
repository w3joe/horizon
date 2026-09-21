#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${HORIZON_PYTHON:-}" ]]; then
  python_bin="$HORIZON_PYTHON"
elif [[ -x /opt/homebrew/bin/python3.12 ]]; then
  python_bin=/opt/homebrew/bin/python3.12
else
  python_bin=python3
fi

"$python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else "Horizon requires Python 3.11+")'
exec "$python_bin" "$@"
