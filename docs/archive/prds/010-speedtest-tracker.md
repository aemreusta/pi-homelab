# PRD 010 — Speedtest Tracker

> **Sprint:** [020 — Observability gap-fillers](../sprints/020-observability-gap-fillers.md)
> **Role:** `ansible/roles/speedtest_tracker`
> **Status:** Implemented. The original requirements below are historical;
> current configuration is in the role and runtime evidence in [PROJECT_INDEX](../../../PROJECT_INDEX.md).

## Purpose

Long-running ISP bandwidth and latency log. No current equivalent in the stack — Beszel covers LAN-side network bytes, vmagent does not run Ookla, and ad-hoc `speedtest-cli` runs aren't persisted.

## Why a tool, not a cron + curl?

A cron'd `speedtest --json | jq | curl → VictoriaMetrics` was considered. Rejected because:

1. Result schema drift (Ookla output evolves; tracker handles it).
2. License compliance — speedtest-tracker bundles Ookla's CLI with its own license envelope.
3. Built-in scheduling, retries, and per-host server selection logic.
4. Built-in Prometheus endpoint — drop-in scrape target for the existing vmagent.

## Implementation

| Setting | Value |
|---|---|
| Image | `lscr.io/linuxserver/speedtest-tracker:latest` |
| Host port | `8765` |
| Container port | `80` (nginx + PHP) |
| Compose dir | `/home/{{ docker_user }}/docker/core/speedtest-tracker` |
| Config dir | `…/speedtest-tracker/config` (SQLite + Laravel storage) |
| Subdomain | `speedtest.example` |
| Memory limit | `384M` (PHP-FPM + nginx + Laravel) |
| PID limit | `200` |
| cap_drop | `ALL` |
| cap_add | `NET_BIND_SERVICE` (binds 80 inside container) |
| read_only | `false` (Laravel writes to storage + cache) |
| Schedule | `SPEEDTEST_SCHEDULE="0 * * * *"` (hourly) |
| Servers | Auto-select (operator can pin via env later if results jitter) |

### Vault keys (new)

```yaml
# Speedtest Tracker — Laravel APP_KEY (32-char base64); generate with:
#   docker run --rm lscr.io/linuxserver/speedtest-tracker:latest \
#     php artisan key:generate --show
vault_speedtest_app_key: "CHANGE_ME"
# Initial admin login
vault_speedtest_admin_email: "admin@raspberrypi.local"
vault_speedtest_admin_password: "CHANGE_ME"
```

### vmagent scrape

Add a scrape job to `ansible/roles/vmagent` config (out of this sprint's scaffold — flagged as a follow-up):

```yaml
- job_name: speedtest
  metrics_path: /api/v1/metrics/prometheus
  static_configs:
    - targets: ['speedtest-tracker:80']
```

Container joins `core_net` so vmagent can resolve `speedtest-tracker`.

### Grafana dashboard

A starter dashboard (download `https://grafana.com/grafana/dashboards/18138`) lands as JSON under `ansible/roles/grafana/files/dashboards/speedtest.json` on a follow-up commit.

## Acceptance

1. `make speedtest` exits 0.
2. `curl -s http://speedtest.example` returns 200.
3. First scheduled run completes ≤ 90 s after container start (manual trigger via UI).
4. Result row visible: download Mbps, upload Mbps, ping ms.
5. `vmagent` job `speedtest` reports `up=1` within 5 min of scrape config deploy.

## Risks

- **WAN saturation:** ~30 s of full bandwidth per run. Mitigation: schedule top-of-hour; documented opt-down to `0 */4 * * *` if Jellyfin streams collide. Beszel will alert on sustained load — not a silent failure mode.
- **Ookla TOS:** lscr.io image carries the relevant license envelope; do not roll our own.

## Pre-implementation TODO

- [ ] Context7-verify current linuxserver/speedtest-tracker env keys + Prometheus endpoint path.
- [ ] Generate `vault_speedtest_app_key` and store under vault.
- [ ] Add `speedtest` job to vmagent scrape config (follow-up commit after first deploy proves the endpoint).

## Operational notes (post-deploy)

### 2026-06-23 — `ConfigurationError` cluster fixed (scheduling, not network)

Symptom: a steady ~2.7% of hourly runs failed with the Ookla CLI's
`Configuration - Could not retrieve or read configuration (ConfigurationError)`
— the CLI failing to fetch its config/server-list *before* the test (the app
does **no retry**, so each blip = one red row). The link itself was fine
(537 ok / 15 fail over 30d).

Root cause was a **scheduling collision, not the WAN**: 8 of 14 failures landed
at **16:00**, the tail of a redundant **15:00 full media-library scan** whose
heavy disk/CPU I/O starved the top-of-hour speedtest's config fetch. Fixes:

- **Retired the 15:00 library scan** — new imports are already scanned
  automatically and a full scan already runs at 03:00, so it added no coverage
  (cron set to `state: absent`).
- **Moved the speedtest off `:00` → `:30`** (`speedtest_tracker_schedule`) to
  avoid the top-of-hour job pile-up.
- **Pinned servers** `11250,51661` (Turknet + Kapteyan, the two most-successful
  Istanbul servers) — skips the server-discovery round-trip that contributed to
  the scattered (non-16:00) transients.

Note the original "schedule top-of-hour" risk-mitigation above was itself the
problem — top-of-hour is the *busiest* minute on this host. Residual transient
base-config failures are inherent to the Ookla CLI; opt the schedule down to
`30 */4 * * *` if they still annoy.
