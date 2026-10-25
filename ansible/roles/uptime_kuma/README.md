# uptime_kuma

Uptime Kuma 1.x checks homelab endpoints and sends alerts to ntfy. The role
also includes a daily check that the live monitors and notification target still
match the committed config.

## What it deploys

One `louislam/uptime-kuma:1` container (`uptime-kuma`) on its own
`uptime_kuma_net` bridge. The image stays on 1.x until 2.x has a stable
persistence migration path. The root filesystem stays writable because Kuma
writes its data and node_modules cache. The entrypoint chowns `/app/data` as
root, so the role adds CHOWN, DAC_OVERRIDE, FOWNER, SETGID and SETUID back
after `cap_drop: [ALL]`. Without them the container crash-loops.

| Item         | Value                                                     |
|--------------|-----------------------------------------------------------|
| Web UI       | host `:3011` → container `:3001`, LAN-only in UFW (`lan_subnet`) |
| Traefik      | `https://uptime.example.internal`                         |
| Data         | `/home/pi/docker/core/uptime-kuma/data` (`kuma.db`)     |
| Drift script | `/usr/local/bin/uptime-kuma-drift.sh`, cron daily 09:30   |
| Memory limit | `256M` (`uptime_kuma_mem_limit`)                          |

## Deploy

```bash
make uptime-kuma         # container, settings, drift cron, UFW
make uptime-kuma-seed    # on demand: idempotent restore of monitors + notifications
make uptime-kuma-drift   # show live-vs-committed diff
```

Seeding is tagged `never`, so a normal deploy does not reseed. A second run
should report `created=0`.

## Configuration

- `uptime_kuma_ntfy_url` — taken from `ntfy_external_base_url`. It must be the
  plain base URL, not the shoutrrr URL. Never hardcode a hostname.
- `uptime_kuma_ntfy_topic` — `homelab-uptime`.
- `uptime_kuma_retention_days` — `30`, written to `keepDataPeriodDays` in
  `kuma.db`. Reclaiming the already-allocated space needs a manual `VACUUM`.
- Vault: `vault_uptime_kuma_username`, `vault_uptime_kuma_password` (admin),
  `vault_uptime_kuma_metrics_key` (`/metrics` API key, used by vmagent and the
  drift script).

## Operations

- **Source of truth:** `templates/uptime-kuma-backup.json.j2` holds the
  monitors, which are created in the UI and exported here. See
  [files/README.md](files/README.md).
- **Every deploy fixes the live ntfy URL.** `configure.yml` rewrites any ntfy
  notification row whose `ntfyserverurl` has drifted. A stale `ntfy.example`
  row left all monitors unable to notify for three weeks.
- **Alerts:** `UptimeKumaDown` (crit; inactive until the metrics key is set),
  `UptimeKumaConfigDrift` (`uptime_kuma_config_drift == 1`) and
  `UptimeKumaNotifyTargetDown` (`uptime_kuma_notify_target_up == 0`).
- **Healthcheck:** `curl -sf http://127.0.0.1:3001`.
- The drift check only notices monitors that were added, removed or renamed.
  It does not catch changed fields on an existing monitor.
- Setup history: [docs/archive/runbooks/039-2026-05-31-uptime-kuma-setup.md](../../../docs/archive/runbooks/039-2026-05-31-uptime-kuma-setup.md).
