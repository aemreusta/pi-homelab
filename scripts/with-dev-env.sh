#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
if [ ! -x "$repo/.venv/bin/python3" ]; then
    echo "Development environment missing. Run make dev first." >&2
    exit 1
fi
export PATH="$repo/.venv/bin:$PATH"
exec "$@"
