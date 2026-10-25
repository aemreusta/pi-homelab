#!/usr/bin/env bash
# Pre-commit hook to check for placeholder values in vault.yml
set -euo pipefail

# Only check if vault.yml is being committed
if ! git diff --cached --name-only | grep -q "vault\.yml$"; then
    exit 0
fi

# Check for CHANGE_ME placeholders in the staged vault file(s) ONLY.
# Note the `$` anchor: this matches `…/vault.yml` but NOT `vault.yml.example`,
# which is *meant* to ship CHANGE_ME placeholders. Earlier this grepped every
# staged file, so committing the example alongside a real vault.yml change
# false-tripped the hook (the example's placeholders are by design).
vault_files=$(git diff --cached --name-only | grep "vault\.yml$" || true)

if [[ -n "$vault_files" ]]; then
    if echo "$vault_files" | xargs grep -l "CHANGE_ME" 2>/dev/null; then
        echo "ERROR: vault.yml contains placeholder values (CHANGE_ME)." >&2
        echo "Please replace all CHANGE_ME strings with real values before committing." >&2
        exit 1
    fi
fi

exit 0
