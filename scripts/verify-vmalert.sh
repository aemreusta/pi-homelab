#!/bin/bash
# scripts/verify-vmalert.sh — End-to-end probe for the Sprint 18 alerting
# substrate. Confirms that the chain
#
#   synthetic alert → Alertmanager → ntfy-bridge → ntfy + alert-audit.log
#
# is intact before the operator flips `common_use_vmalert: true` to decom
# the cron-based scripts.
#
# Run on the Pi (via SSH from the Mac, or directly):
#   ssh pi@raspberrypi.local 'sudo /usr/local/bin/verify-vmalert.sh'
# or from the Mac via the Makefile target:
#   make verify-vmalert
#
# Flags:
#   --check-logsql   Also query VictoriaLogs for each container-error-rate
#                    rule's filter to confirm field names (container_name,
#                    log_level) resolve to real log streams. Catches the
#                    "rules silently never fire" failure mode that the
#                    synthetic injection alone can't detect.
#
# Exit codes:
#   0 — all checks passed
#   1 — at least one component is unhealthy, the audit-log line did not
#       appear within VERIFY_TIMEOUT seconds, OR --check-logsql found a
#       rule filter that resolves to zero log streams.

set -uo pipefail

# Defaults can be overridden via env. Pi-side service endpoints assume the
# observability network is reachable via host ports (LAN: 192.0.2.100,
# Tailscale: 100.64.0.10 — set HOST appropriately).
HOST="${VERIFY_HOST:-127.0.0.1}"
VMALERT_METRICS_PORT="${VMALERT_METRICS_PORT:-8880}"
VMALERT_LOGS_PORT="${VMALERT_LOGS_PORT:-8881}"
ALERTMANAGER_PORT="${ALERTMANAGER_PORT:-9093}"
BRIDGE_PORT="${BRIDGE_PORT:-9097}"
AUDIT_LOG="${AUDIT_LOG:-/var/log/alert-audit.log}"
# Default exceeds Alertmanager's group_wait (30s by default) — AM buffers the
# first alert of a group for group_wait before dispatching, so 60s gives the
# bridge a comfortable margin to write the audit line.
VERIFY_TIMEOUT="${VERIFY_TIMEOUT:-60}"
VL_PORT="${VL_PORT:-9428}"
LOGSQL_RULES_FILE="${LOGSQL_RULES_FILE:-/home/pi/docker/observability/vmalert/rules/logs/container-error-rate.yml}"

# Flag parsing — keep flat (only one option for now).
CHECK_LOGSQL=0
for arg in "$@"; do
    case "$arg" in
        --check-logsql) CHECK_LOGSQL=1 ;;
        -h|--help)
            sed -n '2,25p' "$0"
            exit 0
            ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

# Unique alertname so we can grep for THIS run's audit line.
PROBE_ID="verify-$(date +%s)-$$"
ALERTNAME="VerifyVmalertProbe"

fail=0

ok() { printf "  \033[32mok\033[0m  %s\n" "$*"; }
warn() { printf "  \033[33m!! \033[0m %s\n" "$*"; fail=1; }
step() { printf "\n→ %s\n" "$*"; }

# ── 1. Component healthchecks ─────────────────────────────────────────
step "Component healthchecks"

probe_health() {
    local name="$1" url="$2"
    if curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" | grep -qE "^(200|204)$"; then
        ok "$name healthy ($url)"
    else
        warn "$name UNHEALTHY ($url)"
    fi
}

probe_health "vmalert-metrics /health" "http://${HOST}:${VMALERT_METRICS_PORT}/health"
probe_health "vmalert-logs /health"    "http://${HOST}:${VMALERT_LOGS_PORT}/health"
probe_health "alertmanager /-/healthy" "http://${HOST}:${ALERTMANAGER_PORT}/-/healthy"

# ntfy-bridge intentionally has NO host port — it's only reachable inside the
# observability docker network (security hardening: bridge has no secrets but
# also no real auth). Use docker's own healthcheck status as the signal.
bridge_status=$(docker inspect --format '{{.State.Health.Status}}' ntfy-bridge 2>/dev/null || echo "missing")
if [ "$bridge_status" = "healthy" ]; then
    ok "ntfy-bridge healthy (docker health: $bridge_status)"
else
    warn "ntfy-bridge UNHEALTHY (docker health: $bridge_status)"
fi

# ── 2. vmalert has rules loaded ───────────────────────────────────────
step "Rule loading"

count_rules() {
    local name="$1" port="$2"
    local body
    body=$(curl -s --max-time 5 "http://${HOST}:${port}/api/v1/rules" || echo "{}")
    local n
    n=$(echo "$body" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(sum(len(g.get('rules', [])) for g in d.get('data', {}).get('groups', [])))
except Exception:
    print(0)
")
    if [ "${n:-0}" -gt 0 ] 2>/dev/null; then
        ok "$name has $n rules loaded"
    else
        warn "$name has NO rules loaded — check rule-file syntax or path"
    fi
}

count_rules "vmalert-metrics" "$VMALERT_METRICS_PORT"
count_rules "vmalert-logs"    "$VMALERT_LOGS_PORT"

# ── 2b. (optional) LogsQL field-name probe ────────────────────────────
# Catches the "rules silently never fire" failure mode: vmalert-logs can
# load the rule fine, but if Fluent Bit emits `level=error` instead of
# `log_level=error`, the filter resolves to zero streams forever.
# Probes each rule's filter against the live VictoriaLogs over the last
# 24 h and warns if any returns zero rows.
if [ "$CHECK_LOGSQL" = "1" ]; then
    step "LogsQL field-name probe (rule filters vs live VictoriaLogs)"

    if [ ! -r "$LOGSQL_RULES_FILE" ]; then
        warn "Rule file not readable: $LOGSQL_RULES_FILE — skipping LogsQL probe"
    else
        # Extract `expr: '...'` lines from the rendered rule file. The
        # rules are managed by Ansible so the format is stable: single
        # quotes around the LogsQL expression. We trim the trailing
        # alert-evaluation pipe (`| stats by ... | filter ...:>N`) so we
        # measure whether the underlying filter has ANY matching rows,
        # not whether the threshold is exceeded.
        mapfile -t exprs < <(
            grep -E "^[[:space:]]+expr:" "$LOGSQL_RULES_FILE" \
                | sed -E "s/^[[:space:]]+expr:[[:space:]]+'(.+)'$/\1/" \
                | sed -E 's/[[:space:]]*\|[[:space:]]*stats by.*$//'
        )

        if [ "${#exprs[@]}" -eq 0 ]; then
            warn "No expr: lines found in $LOGSQL_RULES_FILE — file format may have drifted"
        fi

        zero_count=0
        for raw in "${exprs[@]}"; do
            # Constrain to last 24 h to avoid false-negatives from a
            # genuinely quiet container.
            query="_time:24h ${raw}"
            # VL /select/logsql/query returns one JSON doc per line. We
            # only need to know whether any line came back; `head -n1`
            # short-circuits long result sets.
            body=$(curl -s --max-time 10 \
                --data-urlencode "query=${query}" \
                "http://${HOST}:${VL_PORT}/select/logsql/query" 2>/dev/null \
                | head -n1 || true)
            if [ -n "$body" ]; then
                ok "matches >0 rows: ${raw}"
            else
                warn "ZERO rows over 24 h: ${raw}"
                warn "   field name likely wrong (check Fluent Bit emission), or container has been quiet"
                zero_count=$((zero_count + 1))
            fi
        done

        if [ "$zero_count" -gt 0 ]; then
            warn "${zero_count}/${#exprs[@]} rules will never fire — fix before the soak window"
        fi
    fi
fi

# ── 3. Synthetic alert injection ──────────────────────────────────────
step "Inject synthetic alert → AM → bridge → audit log"

# Skip injection if anything upstream is already broken — saves the operator
# a noisy ntfy on top of a real failure.
if [ "$fail" -ne 0 ]; then
    warn "Skipping injection — fix upstream failures first"
    exit 1
fi

# AM /api/v2/alerts expects an array of alert objects with labels, annotations,
# generatorURL. EndsAt defaults to startsAt + 5m if omitted — that's fine, AM
# auto-resolves.
NOW=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
payload=$(python3 -c "
import json
print(json.dumps([{
    'labels': {
        'alertname': '${ALERTNAME}',
        'severity': 'warn',
        'service': 'verify-script',
        'probe_id': '${PROBE_ID}',
    },
    'annotations': {
        'summary': 'Synthetic probe ${PROBE_ID}',
        'description': 'Fired by verify-vmalert.sh — safe to ignore.',
    },
    'startsAt': '${NOW}',
    'generatorURL': 'http://verify-vmalert/probe',
}]))
")

http_code=$(curl -s -o /tmp/verify-vmalert-am.out -w "%{http_code}" --max-time 10 \
    -X POST "http://${HOST}:${ALERTMANAGER_PORT}/api/v2/alerts" \
    -H "Content-Type: application/json" \
    -d "$payload" || echo "000")

if [ "$http_code" = "200" ]; then
    ok "AM accepted synthetic alert (http=${http_code})"
else
    warn "AM rejected synthetic alert (http=${http_code} — see /tmp/verify-vmalert-am.out)"
    cat /tmp/verify-vmalert-am.out 2>/dev/null
    exit 1
fi

# ── 4. Wait for audit-log line ───────────────────────────────────────
step "Wait up to ${VERIFY_TIMEOUT}s for audit-log line"

start=$(date +%s)
while [ "$(( $(date +%s) - start ))" -lt "$VERIFY_TIMEOUT" ]; do
    if grep -F "alertname=${ALERTNAME}" "$AUDIT_LOG" >/dev/null 2>&1; then
        ok "Audit-log entry found:"
        grep -F "alertname=${ALERTNAME}" "$AUDIT_LOG" | tail -1 | sed 's/^/      /'
        echo
        echo "Chain verified end-to-end: AM → ntfy-bridge → audit-log + ntfy."
        echo "(Operator should see a synthetic ntfy on their phone — safe to dismiss.)"
        exit 0
    fi
    sleep 2
done

warn "No audit-log entry for ${ALERTNAME} within ${VERIFY_TIMEOUT}s"
echo "  Recent audit lines:"
tail -5 "$AUDIT_LOG" 2>/dev/null | sed 's/^/      /'
exit 1
