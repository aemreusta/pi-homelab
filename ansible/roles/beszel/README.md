# beszel

Lightweight host and container monitoring: the Beszel hub (web UI) plus a
local agent that reports this Pi's CPU, memory, disk, temperature and Docker
stats.

## What it deploys

Two scratch-image containers, both `network_mode: host` and `cap_drop: [ALL]`:

| Container      | Image                         | Port                       | Data                                    | Memory var               |
|----------------|-------------------------------|----------------------------|-----------------------------------------|--------------------------|
| `beszel`       | `henrygd/beszel:latest`       | `8090` (UFW: LAN)          | `/home/pi/docker/core/beszel/data`    | `beszel_mem_limit` (256M) |
| `beszel-agent` | `henrygd/beszel-agent:latest` | `45876` (`beszel_agent_port`) | `/home/pi/docker/core/beszel/agent-data` | `beszel_agent_mem_limit` (128M) |

The agent mounts `/var/run/docker.sock` read-only. Both data directories are
root-owned because the containers run as root. Traefik publishes the UI at
`https://beszel.example.internal`.

## Deploy

```bash
make beszel
```

When `vault_beszel_email` is defined, `configure.yml` uses the PocketBase API to
create the admin, register the `homelab` system (agent at `127.0.0.1:45876`),
add the ntfy webhook (`ntfy_shoutrrr_url`) and reconcile the alert rules. That
phase is skipped under `--check` because `uri` tasks return no `.json` in check
mode.

## Configuration

- `vault_beszel_email` / `vault_beszel_password`: admin login. `vault_beszel_agent_key` sets the agent `KEY` when present.
- `beszel_root_filesystem` (`/dev/sda`): a device name, because the containerised agent cannot see the host `/`. USB disks re-enumerate after power incidents, so check `lsblk` if the root-disk chart goes flat ([pi4-usb-power-budget](../../../docs/conventions/pi4-usb-power-budget.md)).
- `beszel_extra_filesystems`: empty since the 2026-08-02 ssd2 rollback. Restore it in the same change that re-enables `storage_ssd2_enabled`. Otherwise Docker auto-creates the bind source and the root disk is charted twice.
- `beszel_alert_rules`: Temperature and Status only. Memory and disk alerts belong to the `common` role scripts ([alert discipline](../../../docs/design/024-alert-discipline.md)). CPU and LoadAvg5 moved to vmalert `CPULoadHigh`/`CPULoadCritical` on 2026-10-15 because Beszel has no hysteresis and flapped at its threshold. Names must use Beszel's enum; anything else fails with HTTP 400. `beszel_alert_rules_to_remove` deletes superseded rules.

## Operations

- Healthchecks use the binaries: `/beszel health` (hub) and `/agent health` (agent).
- Beszel exposes no Prometheus endpoint. Instead, a root cron
  (`/usr/local/bin/beszel-freshness.py`, every 5 min) reads its SQLite store
  read-only and writes `beszel_freshness.prom` to the node_exporter textfile
  collector. The `BeszelSamplesStale` and `BeszelFreshnessCollectorFailed`
  alerts (`roles/vmalert/templates/rules/beszel.yml.j2`) watch it.
- The compose comment notes that S.M.A.R.T. is unavailable because the USB
  bridge does not expose it.
- Incident: [ten-day container-stats blackout](../../../docs/incidents/2026-09-24-beszel-container-stats-blackout-ten-days-unseen.md).
