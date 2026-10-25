#!/usr/bin/env bats
# Regression tests for ansible/roles/common/templates/alert-lib.sh.j2.
# Sprint 017 Story 3.
#
# alert-lib has zero Jinja2 expressions, so we source the template
# directly — no rendering step. Tests cover the failure modes that bit
# us in commit 201afcb / f57752b:
#   - curl -f masking HTTP codes (must log the real 4xx/5xx code)
#   - HTTP 000 on connection refused / timeout
#   - logger fallback when ALERT_LOG is unwritable
#
# Run via `bats tests/test_alert_lib.bats` or `make test-bats`. Skips
# cleanly if `bats` is not installed.

ALERT_LIB="$BATS_TEST_DIRNAME/../ansible/roles/common/templates/alert-lib.sh.j2"

setup() {
    TMPDIR=$(mktemp -d)
    export ALERT_LOG="$TMPDIR/audit.log"
    touch "$ALERT_LOG"
    # Mock curl. PATH-prepend a tempdir with a stub that prints the
    # requested HTTP code (encoded via $MOCK_HTTP_CODE).
    mkdir -p "$TMPDIR/bin"
    cat > "$TMPDIR/bin/curl" <<'EOF'
#!/bin/bash
# Mock curl — emits $MOCK_HTTP_CODE on stdout when called with
# `-w "%{http_code}"` (matches alert-lib's invocation). Otherwise no-op.
for arg in "$@"; do
    if [ "$arg" = "%{http_code}" ]; then
        printf "%s" "${MOCK_HTTP_CODE:-000}"
        exit "${MOCK_CURL_RC:-0}"
    fi
done
exit 0
EOF
    chmod +x "$TMPDIR/bin/curl"
    export PATH="$TMPDIR/bin:$PATH"
    # shellcheck disable=SC1090
    source "$ALERT_LIB"
}

teardown() {
    rm -rf "$TMPDIR"
}

@test "ntfy_send logs http=200 as ok on success" {
    MOCK_HTTP_CODE=200 run ntfy_send "test-script" "http://example/"
    [ "$status" -eq 0 ]
    grep -qF "script=test-script step=ntfy_send status=ok http=200" "$ALERT_LOG"
}

@test "ntfy_send logs http=404 as fail (curl -f masking regression guard)" {
    MOCK_HTTP_CODE=404 run ntfy_send "test-script" "http://example/missing"
    [ "$status" -eq 1 ]
    grep -qF "script=test-script step=ntfy_send status=fail http=404" "$ALERT_LOG"
}

@test "ntfy_send logs http=000 on connection refused (curl rc!=0)" {
    MOCK_HTTP_CODE=000 MOCK_CURL_RC=7 run ntfy_send "test-script" "http://nope/"
    [ "$status" -eq 1 ]
    grep -qF "script=test-script step=ntfy_send status=fail http=000" "$ALERT_LOG"
}

@test "ntfy_send logs http=000 on timeout (curl rc=28)" {
    MOCK_HTTP_CODE=000 MOCK_CURL_RC=28 run ntfy_send "test-script" "http://slow/"
    [ "$status" -eq 1 ]
    grep -qF "script=test-script step=ntfy_send status=fail http=000" "$ALERT_LOG"
}

@test "ntfy_send treats http=503 as fail (5xx not 2xx)" {
    MOCK_HTTP_CODE=503 run ntfy_send "test-script" "http://example/"
    [ "$status" -eq 1 ]
    grep -qF "status=fail http=503" "$ALERT_LOG"
}

@test "alert_event writes structured logfmt line with extra kv pairs" {
    run alert_event "myscript" "run" "ok" "foo=1" "bar=baz"
    [ "$status" -eq 0 ]
    grep -qE "^[0-9-]+T[0-9:]+Z script=myscript step=run status=ok foo=1 bar=baz" "$ALERT_LOG"
}

@test "alert_event uses logger fallback when ALERT_LOG is unwritable" {
    export ALERT_LOG="/proc/1/cannot-write-here.log"
    # Mock logger as well so the test doesn't pollute the host journal.
    cat > "$TMPDIR/bin/logger" <<EOF
#!/bin/bash
echo "logger: \$*" >> "$TMPDIR/logger.out"
EOF
    chmod +x "$TMPDIR/bin/logger"
    run alert_event "fb-test" "step" "ok" "key=val"
    [ "$status" -eq 0 ]
    grep -qF "cannot write" "$TMPDIR/logger.out"
    grep -qF "script=fb-test step=step status=ok key=val" "$TMPDIR/logger.out"
}

@test "alert_event defaults to 'unknown' when script name is empty" {
    # Defensive: empty script name must still produce a parseable log line.
    # alert-lib uses ${1:-unknown} so empty/unset → "unknown" (not "").
    run alert_event "" "ntfy_send" "ok" "http=200"
    [ "$status" -eq 0 ]
    grep -qF "script=unknown step=ntfy_send status=ok http=200" "$ALERT_LOG"
}
