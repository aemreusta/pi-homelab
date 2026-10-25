#!/bin/bash
# scripts/deploy-deep-check.sh <playbook>
# Semantic deploy gate. Born as Sprint 017 Story 9 (opt-in); flipped to
# DEFAULT-ON 2026-06-25 (H1).
#
# Runs `ansible-playbook --check --diff` against the live Pi BEFORE the real
# apply. This catches semantic errors that --syntax-check CANNOT see:
# undefined vars, missing roles, wrong vault keys, host_vars referencing a
# deleted file, etc. Empirically `ansible-playbook --syntax-check` exits 0 on
# a totally undefined variable — it only parses YAML, never resolving
# vars/when:/roles/vault/Jinja — so syntax-check alone is a weak gate. The
# 2026-05-29 Tdarr-dedup YAML-escape incident slipped through it.
#
# Soft-fails (exits 0) if the Pi is unreachable so off-LAN deploys via
# Tailscale work without extra ENV juggling — the operator still gets a
# warning printed. Hard-fails if the Pi IS reachable AND the check turns
# up an error.
#
# Default-ON rationale: the check costs one extra dry-run round-trip but is
# the only deploy-time gate that resolves vars. It self-disables when the Pi
# is unreachable, so it never blocks an off-LAN deploy — making it safe to
# run unconditionally.
#
# ── KNOWN BLIND SPOT: live-API config phases are check-mode-EXEMPT ──────────
# Roles whose config phase drives a live HTTP API (e.g. beszel, jellyseerr,
# media) gate that phase on `when: ... and not ansible_check_mode`.
# Reason: `ansible.builtin.uri` is SKIPPED under `--check`, registering
# `{skipped: true}` with no `.json`, so a downstream `X.json` consumer crashes
# at arg-finalization — the 2026-07-02 autodeploy brick loop
# (docs/operations/ops-discipline.md §6).
# Consequence for THIS gate: `--check --diff` no longer executes those exempt
# phases, so an undefined var / bad template inside a `configure.yml` will NOT
# be caught here — it surfaces at APPLY time instead. That coverage is replaced
# structurally, not at deploy time, by `tests/test_uri_check_mode_safety.py`
# (fails CI on any check-mode-reachable `uri`→`.json` crash) — the gate for the
# exempt phases lives in the pytest suite, not in this dry run.
#
# Usage:
#   make adguard                       # runs deep check, then apply (DEFAULT)
#
# Opt out (skip ONLY the deep check, keep syntax-check):
#   DEPLOY_DEEP_CHECK=0 make adguard   # e.g. known intentional state divergence
#
# Full bypass (skip BOTH checks):
#   DEPLOY_SKIP_CHECK=1 make adguard   # existing flag, Makefile-level

set -uo pipefail

PLAYBOOK="${1:?usage: $0 <playbook-path>}"

# Default ON (DEPLOY_DEEP_CHECK unset → "1"). Opt out with DEPLOY_DEEP_CHECK=0.
# Accept any falsey-ish opt-out spelling so muscle memory doesn't surprise.
case "${DEPLOY_DEEP_CHECK:-1}" in
    0 | no | false | off | "")
        echo "ℹ DEPLOY_DEEP_CHECK opted out — syntax-check only, skipping --check --diff"
        exit 0
        ;;
esac

cd "$(dirname "$0")/../ansible" || { echo "✗ cannot cd into ansible dir"; exit 1; }

# Quick reachability probe — `ansible pi -m ping` honours inventory + any
# `-e ansible_host=...` override.
#
# The budget is enforced with ANSIBLE_TIMEOUT (ansible's own SSH connect
# timeout), NOT by wrapping the call in coreutils `timeout`. macOS ships no
# `timeout`, so `timeout 10 ansible ...` failed with "command not found" — a
# non-zero exit indistinguishable from an unreachable Pi. This gate therefore
# reported "Pi unreachable" and skipped on EVERY deploy run from a Mac, while
# the Pi answered `pong` immediately. Found 2026-09-07; it had been silently
# skipping since the gate was made default-ON on 2026-06-25. The CD was
# unaffected (it runs on the Pi, where `timeout` exists), so the gate worked
# exactly where a second check already existed and no-opped where it was the
# only one.
#
# Same class as the unmonitor and manual-import refusals fixed in
# deploy-v2026.09.05.1: a check that cannot tell "it failed" from "I could not
# run the check" is not a check. Guarded by tests/test_deploy_deep_check.bats.
if ! ANSIBLE_TIMEOUT="${DEPLOY_PING_TIMEOUT:-10}" ansible pi -m ping >/dev/null 2>&1; then
    # Separate the two causes before blaming the network. A missing/broken
    # ansible is an environment fault the operator must fix; skipping on it
    # would hide a broken toolchain behind a message about the Pi.
    if ! command -v ansible >/dev/null 2>&1; then
        echo "✗ deep check cannot run — 'ansible' not found on PATH"
        echo "  This is a local toolchain fault, not an unreachable Pi."
        echo "  (DEPLOY_DEEP_CHECK=0 to skip the deep check deliberately)"
        exit 1
    fi
    echo "⚠ deep check enabled but Pi unreachable — skipping --check --diff"
    echo "  (run with DEPLOY_SKIP_CHECK=1 to also skip syntax-check, or"
    echo "   pass -e ansible_host=100.64.0.10 to use Tailscale IP)"
    exit 0
fi

echo "→ deep check — running --check --diff against live Pi (DEPLOY_DEEP_CHECK=0 to skip)"
if ! ansible-playbook --check --diff "$PLAYBOOK"; then
    echo "✗ deep check failed — refusing to apply $PLAYBOOK"
    echo "  (use DEPLOY_SKIP_CHECK=1 to bypass if this is an intentional state divergence)"
    exit 1
fi
echo "✓ deep check OK"
