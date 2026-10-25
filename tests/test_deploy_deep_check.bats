#!/usr/bin/env bats
# Regression tests for scripts/deploy-deep-check.sh.
#
# The gate this script implements runs `ansible-playbook --check --diff` against
# the live Pi before every apply. CLAUDE.md documents it as default-ON since
# 2026-06-25 and it is the only thing that catches undefined vars, missing roles
# and wrong vault keys — `--syntax-check` exits 0 on a fully undefined var.
#
# On 2026-09-07 it turned out the gate had been skipping on every deploy run
# from a Mac since the day it was made default-ON. The reachability probe was
# `timeout 10 ansible pi -m ping`; macOS ships no coreutils `timeout`, so the
# probe exited non-zero with "command not found", which the script read as
# "Pi unreachable" and used to exit 0. The Pi answered `pong` in well under a
# second the whole time. The CD was unaffected because it runs on the Pi, where
# `timeout` exists — so the gate worked in the one place a second check already
# existed, and no-opped in the only place it was load-bearing.
#
# That is the same class as the two import-pipeline defects fixed in
# deploy-v2026.09.05.1: a check that cannot distinguish "the thing failed" from
# "I was unable to run the check" is not a check, and reporting the former when
# the latter happened sends the operator to the wrong place entirely.
#
# These tests pin the two properties that keep it honest:
#   1. no dependency on a binary macOS does not ship
#   2. a broken local toolchain is reported as such, never as an unreachable Pi
#
# Run via `bats tests/test_deploy_deep_check.bats` or `make test-bats`.

SCRIPT="$BATS_TEST_DIRNAME/../scripts/deploy-deep-check.sh"

setup() {
    TMPDIR=$(mktemp -d)
    mkdir -p "$TMPDIR/bin"
}

teardown() {
    rm -rf "$TMPDIR"
}

@test "script does not invoke coreutils timeout (absent on macOS)" {
    # The specific regression. `timeout` is not on a stock macOS, so any use of
    # it in the probe path degrades to a silent skip on every developer machine.
    # Comments are stripped first: the fix documents the old broken invocation
    # verbatim, and a guard that trips over its own postmortem is useless.
    run bash -c "sed 's/#.*//' '$SCRIPT' | grep -nE '(^|[^_[:alnum:]])timeout[[:space:]]+[0-9]'"
    [ "$status" -ne 0 ]
}

@test "probe budget is expressed via ANSIBLE_TIMEOUT" {
    # Ansible's own connect timeout needs no external binary, so the budget
    # survives on any platform.
    run grep -q 'ANSIBLE_TIMEOUT' "$SCRIPT"
    [ "$status" -eq 0 ]
}

@test "missing ansible is reported as a toolchain fault, not an unreachable Pi" {
    # PATH with no ansible at all. The script must fail loudly rather than
    # exiting 0 with a message blaming the network — an exit 0 here is what
    # turns a broken environment into a skipped safety gate.
    # Keep a real PATH so the shell and coreutils resolve — only ansible is
    # missing. Emptying PATH entirely would test "no shell", not "no ansible".
    PATH="$TMPDIR/bin:/usr/bin:/bin" run bash "$SCRIPT" playbooks/media.yml
    [ "$status" -ne 0 ]
    [[ "$output" == *"not found on PATH"* ]]
    [[ "$output" != *"Pi unreachable"* ]]
}

@test "a genuinely unreachable Pi still soft-fails so off-LAN deploys work" {
    # This soft-fail is deliberate: deploying over Tailscale from off-LAN must
    # not be blocked by the gate. Stub ansible to fail (as an unreachable host
    # would) while keeping it present on PATH.
    cat > "$TMPDIR/bin/ansible" <<'EOF'
#!/bin/bash
exit 4
EOF
    cat > "$TMPDIR/bin/ansible-playbook" <<'EOF'
#!/bin/bash
exit 0
EOF
    chmod +x "$TMPDIR/bin/ansible" "$TMPDIR/bin/ansible-playbook"
    PATH="$TMPDIR/bin:$PATH" run bash "$SCRIPT" playbooks/media.yml
    [ "$status" -eq 0 ]
    [[ "$output" == *"Pi unreachable"* ]]
}

@test "a reachable Pi runs the check and a failing check blocks the apply" {
    # The gate's whole purpose: --check --diff failing must be fatal, so the
    # apply never runs.
    cat > "$TMPDIR/bin/ansible" <<'EOF'
#!/bin/bash
exit 0
EOF
    cat > "$TMPDIR/bin/ansible-playbook" <<'EOF'
#!/bin/bash
exit 2
EOF
    chmod +x "$TMPDIR/bin/ansible" "$TMPDIR/bin/ansible-playbook"
    PATH="$TMPDIR/bin:$PATH" run bash "$SCRIPT" playbooks/media.yml
    [ "$status" -ne 0 ]
    [[ "$output" == *"deep check failed"* ]]
}

@test "a reachable Pi with a passing check reports OK" {
    cat > "$TMPDIR/bin/ansible" <<'EOF'
#!/bin/bash
exit 0
EOF
    cat > "$TMPDIR/bin/ansible-playbook" <<'EOF'
#!/bin/bash
exit 0
EOF
    chmod +x "$TMPDIR/bin/ansible" "$TMPDIR/bin/ansible-playbook"
    PATH="$TMPDIR/bin:$PATH" run bash "$SCRIPT" playbooks/media.yml
    [ "$status" -eq 0 ]
    [[ "$output" == *"deep check OK"* ]]
}

@test "DEPLOY_DEEP_CHECK=0 opts out explicitly" {
    DEPLOY_DEEP_CHECK=0 run bash "$SCRIPT" playbooks/media.yml
    [ "$status" -eq 0 ]
    [[ "$output" == *"opted out"* ]]
}
