# speedtest_tracker

Speedtest Tracker runs a scheduled Ookla speedtest, keeps the results in SQLite
and exposes them as Prometheus metrics. Background is in
[docs/archive/prds/010-speedtest-tracker.md](../../../docs/archive/prds/010-speedtest-tracker.md).

## What it deploys

A `lscr.io/linuxserver/speedtest-tracker:latest` container named
`speedtest-tracker` (Laravel, with nginx and PHP-FPM). It joins the external
`observability` network so that vmagent can scrape it. Two host cron scripts
are also installed.

| Item        | Value                                                          |
|-------------|----------------------------------------------------------------|
| Web UI      | host `:8765` → container `:80` (UFW: `lan_subnet` only)        |
| URL         | `https://speedtest.<homelab_internal_domains[0]>` (`APP_URL`, Traefik) |
| Data        | `/home/<docker_user>/docker/core/speedtest-tracker/config` (`database.sqlite`) |
| Memory      | `speedtest_tracker_mem_limit` (`384M`)                         |
| Host scripts | `/usr/local/bin/speedtest-result-liveness.sh`, `/usr/local/bin/speedtest-weekly-report.sh` |

## Deploy

```bash
make speedtest
```

The deploy fails immediately if `vault_speedtest_app_key` is empty. The task
error message includes the `php artisan key:generate --show` command that
creates one. The `observability` network must already exist.

## Configuration

- `speedtest_tracker_schedule`: `30 * * * *`. It runs at :30 rather than :00 to
  stay out of the pile-up of jobs that start on the hour.
- `speedtest_tracker_servers`: pinned to Ookla servers `11250,51661`. Pinning
  skips the server-discovery fetch, which caused most `ConfigurationError` failures.
- `speedtest_tracker_prometheus_enabled`: the image keeps this in its settings
  DB, not in an env var. `configure.yml` sets it idempotently.
- Vault: `vault_speedtest_app_key`, `vault_speedtest_admin_email`,
  `vault_speedtest_admin_password`.
- `speedtest_tracker_report_cron` / `speedtest_tracker_report_window`: when the
  weekly ntfy digest runs (Sun 10:00) and the period it covers (`7d`).

## Operations

- **Healthcheck:** `curl -sf http://127.0.0.1/api/healthcheck`. This image has
  no Laravel `/up` route.
- **No `init: true`:** this is an s6-overlay image, so s6 must run as PID 1.
- **SQLite WAL:** `configure.yml` switches the DB to `journal_mode=wal` once.
  This stops the `database is locked` errors.
- **Metrics:** vmagent scrapes job `speedtest`
  (`vmagent_speedtest_metrics_enabled`). Every 5 minutes the liveness cron reads
  SQLite and writes `speedtest_result_liveness.prom` for the textfile collector.
  The weekly report writes `speedtest_weekly_report.prom`.
- **Alerts** (in `roles/vmalert/templates/rules/observability-services.yml.j2`):
  `SpeedtestScrapeDown`, `SpeedtestStaleV2`, `SpeedtestLastResultFailed`,
  `SpeedtestResultLivenessStale`, and the `Speedtest*Degraded` / `*BelowFloor` /
  `SpeedtestPingElevated` rules. If the scrape goes down, re-run
  `make speedtest`: `configure.yml` turns the Prometheus integration back on.
