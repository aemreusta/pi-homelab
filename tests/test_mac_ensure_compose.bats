#!/usr/bin/env bats
# Regression tests for mac-worker/ensure-compose.sh — the one recovery path
# every Mac compose LaunchAgent (worker + GitHub runner) shares.
#
# Incident class: a Mac sleep/OrbStack restart left a compose project down
# with nothing to bring it back (a worker 26 days in 2026-08; GitHub runner
# 19h on 2026-09-28 because its NFS volume timed out once at login and the
# runner LaunchAgent was a one-shot). The script must (1) re-assert the engine
# before the containers, (2) give up a cycle rather than hang (launchd will not
# start a second instance of a running job), and (3) report failure through its
# exit code instead of the old unconditional `exit 0`.

SCRIPT="$BATS_TEST_DIRNAME/../mac-worker/ensure-compose.sh"

setup() {
    TMP=$(mktemp -d)
    mkdir -p "$TMP/bin" "$TMP/project"
    export CALLS="$TMP/calls"
    : > "$CALLS"
    cat > "$TMP/bin/orb" <<'EOF'
#!/bin/bash
echo "orb $*" >> "$CALLS"
EOF
    cat > "$TMP/bin/docker" <<'EOF'
#!/bin/bash
echo "docker $* (cwd=$PWD)" >> "$CALLS"
if [ "$1" = "info" ]; then
    exit "${MOCK_DOCKER_INFO_RC:-0}"
fi
if [ "$1" = "compose" ]; then
    echo "${MOCK_COMPOSE_ERR:-}" >&2
    exit "${MOCK_COMPOSE_RC:-0}"
fi
EOF
    chmod +x "$TMP/bin/orb" "$TMP/bin/docker"
    export PATH="$TMP/bin:/usr/bin:/bin"
    export ENSURE_DOCKER_WAIT_ATTEMPTS=3
    export ENSURE_DOCKER_WAIT_SECONDS=0
}

teardown() {
    rm -rf "$TMP"
}

@test "starts the engine before asserting the compose project" {
    run bash "$SCRIPT" "$TMP/project"
    [ "$status" -eq 0 ]
    [ "$(head -1 "$CALLS")" = "orb start" ]
    grep -q "docker compose up -d (cwd=$TMP/project)" "$CALLS"
}

@test "gives up the cycle with a non-zero exit when docker never comes up" {
    MOCK_DOCKER_INFO_RC=1 run bash "$SCRIPT" "$TMP/project"
    [ "$status" -ne 0 ]
    [ "$(grep -c '^docker info' "$CALLS")" -eq 3 ]
    ! grep -q "docker compose" "$CALLS"
    [[ "$output" == *"docker not up"* ]]
}

@test "compose failure (e.g. NFS volume mount timeout) surfaces in the exit code" {
    MOCK_COMPOSE_RC=1 MOCK_COMPOSE_ERR="failed to mount local volume: connection timed out" \
        run bash "$SCRIPT" "$TMP/project"
    [ "$status" -ne 0 ]
    [[ "$output" == *"compose up failed"* ]]
    [[ "$output" == *"connection timed out"* ]]
}

@test "a missing orb binary never blocks the compose re-assert" {
    rm "$TMP/bin/orb"
    run bash "$SCRIPT" "$TMP/project"
    [ "$status" -eq 0 ]
    grep -q "docker compose up -d" "$CALLS"
}

@test "rejects a missing or non-existent project directory" {
    run bash "$SCRIPT"
    [ "$status" -eq 2 ]
    run bash "$SCRIPT" "$TMP/nope"
    [ "$status" -eq 2 ]
    ! grep -q "docker compose" "$CALLS"
}
