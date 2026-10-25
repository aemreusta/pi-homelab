#!/usr/bin/env bash
# Homepage performance baseline measurement
# Run from Mac: bash scripts/perf-baseline.sh
# Requires: curl, ssh access to Pi

set -euo pipefail

PI_HOST="${PI_HOST:-raspberrypi.local}"
PI_USER="${PI_USER:-pi}"
HOMEPAGE_DIRECT_PORT=3001

RED='\033[0;31m'
YLW='\033[1;33m'
GRN='\033[0;32m'
BLU='\033[0;34m'
RST='\033[0m'

sep() { echo -e "${BLU}────────────────────────────────────────${RST}"; }
hdr() { echo -e "\n${BLU}▶ $1${RST}"; }

echo -e "${BLU}Homepage Performance Baseline${RST}"
echo "Pi: ${PI_USER}@${PI_HOST}  |  $(date '+%Y-%m-%d %H:%M')"
sep

# ── 1. DNS resolution ────────────────────────────────────────────────────────
hdr "1. DNS resolution (nslookup homepage.example)"
dns_ms=$(ssh -q "${PI_USER}@${PI_HOST}" \
  "start=\$(date +%s%N); nslookup homepage.example 127.0.0.1 >/dev/null 2>&1; end=\$(date +%s%N); echo \$(( (end - start) / 1000000 ))")
echo "  DNS: ${dns_ms}ms"
[ "${dns_ms}" -gt 100 ] && echo -e "  ${YLW}⚠  >100ms — AdGuard latency issue${RST}"

sep

# ── 2. Traefik path (via hostname) ───────────────────────────────────────────
hdr "2. Via Traefik: curl http://homepage.example"
read -r ttfb_t total_t http_code < <(ssh -q "${PI_USER}@${PI_HOST}" \
  "curl -o /dev/null -s -w '%{time_starttransfer} %{time_total} %{http_code}' \
  --max-time 15 http://homepage.example" || echo "- - 000")
echo "  HTTP: ${http_code}  TTFB: ${ttfb_t}s  Total: ${total_t}s"
ttfb_ms=$(echo "${ttfb_t} * 1000" | bc | cut -d. -f1)
[ "${ttfb_ms}" -gt 2000 ] && echo -e "  ${RED}✗  TTFB >2s — likely widget timeout${RST}"
[ "${ttfb_ms}" -gt 500 ] && [ "${ttfb_ms}" -le 2000 ] && echo -e "  ${YLW}⚠  TTFB 500ms–2s — investigate${RST}"
[ "${ttfb_ms}" -le 500 ] && echo -e "  ${GRN}✓  TTFB ok${RST}"

sep

# ── 3. Direct container (bypass Traefik) ─────────────────────────────────────
hdr "3. Direct container: curl http://${PI_HOST}:${HOMEPAGE_DIRECT_PORT}"
read -r ttfb_d total_d http_d < <(curl -o /dev/null -s -w '%{time_starttransfer} %{time_total} %{http_code}' \
  --max-time 15 "http://${PI_HOST}:${HOMEPAGE_DIRECT_PORT}" || echo "- - 000")
echo "  HTTP: ${http_d}  TTFB: ${ttfb_d}s  Total: ${total_d}s"

sep

# ── 4. Decision gate ─────────────────────────────────────────────────────────
hdr "4. Analysis"
ttfb_t_ms=$(echo "${ttfb_t} * 1000" | bc | cut -d. -f1 2>/dev/null || echo "0")
ttfb_d_ms=$(echo "${ttfb_d} * 1000" | bc | cut -d. -f1 2>/dev/null || echo "0")
diff=$(( ttfb_t_ms - ttfb_d_ms ))
if [ "${diff}" -gt 500 ]; then
  echo -e "  ${YLW}→ Traefik adds ${diff}ms over direct — check middleware/TLS chain${RST}"
elif [ "${ttfb_d_ms}" -gt 2000 ]; then
  echo -e "  ${RED}→ Homepage process itself is slow (widget API timeouts) — check docker logs homepage${RST}"
else
  echo -e "  ${GRN}→ Baseline good. Both paths under threshold.${RST}"
fi

sep

# ── 5. Docker stats snapshot ─────────────────────────────────────────────────
hdr "5. Container resource snapshot"
ssh -q "${PI_USER}@${PI_HOST}" \
  "docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' \
  homepage jellyfin sonarr radarr prowlarr qbittorrent jellyseerr beszel 2>/dev/null || \
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'"

sep

# ── 6. Homepage widget errors ─────────────────────────────────────────────────
hdr "6. Homepage widget errors (last 30 lines)"
ssh -q "${PI_USER}@${PI_HOST}" \
  "docker logs homepage --tail 30 2>&1 | grep -iE 'error|timeout|warn|fail' || echo '  (no errors found)'"

sep
echo -e "${GRN}Baseline complete. Record results before making changes.${RST}"
echo "  Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
