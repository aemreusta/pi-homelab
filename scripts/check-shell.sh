#!/usr/bin/env bash
# The same complete shell gate is used locally and in CI.
set -euo pipefail
cd "$(dirname "$0")/.."
rendered=$(mktemp -d)
trap 'rm -rf "$rendered"' EXIT
python3 tests/render_pipeline_scripts.py "$rendered"
shellcheck -S warning "$rendered"/*.sh
git ls-files -z -- 'scripts/*.sh' 'mac-worker/*.sh' | xargs -0 shellcheck -S warning
