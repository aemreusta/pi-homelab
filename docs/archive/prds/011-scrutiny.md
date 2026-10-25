# PRD 011 — Scrutiny

> **Sprint:** [020 — Observability gap-fillers](../sprints/020-observability-gap-fillers.md)
> **Role:** `ansible/roles/scrutiny` (DELETED 2026-06-04)
> **Status:** ❌ **RETIRED 2026-06-04.** Deployed but never functional on this
> hardware. The collector runs `smartctl --info --json /dev/sda` **without**
> `-d scsi` at the device-*detection* step (the `metrics_smartctl_bin` wrapper
> only injects `-d scsi` at the metrics step), so the SanDisk USB3→SATA bridge
> failed detection and the device DB stayed permanently empty — while the Docker
> healthcheck (which probes `/api/health`, not collector success) reported
> "healthy". Upstream has no hook to force `-d` at detection for USB-bridge
> disks. **Disk-health is fully covered without Scrutiny** by the `common`
> role's `smart-check.sh` (05:00 cron → `smart_check_*` textfile metrics + the
> `SmartHealthNotOK` / `SmartCheckStale` / `SmartReallocatedSectorsGrowing`
> vmalert rules), now also surfaced in the Grafana **smart-disk-health**
> dashboard. The role, playbook, Makefile target, Traefik route, homepage tile,
> and Uptime-Kuma seed monitor were all removed. This PRD is kept as a tombstone.

## Purpose

SMART-attribute monitoring for `/dev/sda` (the 1.8 TB SSD that holds `/srv` and `/var/lib/docker`). Beszel reports filesystem usage; it does not read SMART. The disk is the single largest single-point-of-failure surface on this Pi — reallocated sectors, wear leveling count, and projected POH-to-failure all warrant tracking.

## Why Scrutiny and not just `smartctl` + cron?

Considered. A weekly `smartctl -a /dev/sda | curl → VictoriaMetrics` was on the table. Rejected because:

1. Scrutiny ships a curated set of failure-prediction heuristics derived from Backblaze drive-stat data — better than threshold-on-raw-attribute.
2. Built-in InfluxDB time series + UI; no Grafana plumbing required for v1.
3. Self-test scheduling baked in.

If Scrutiny proves heavy on Pi resources, we revisit with a smartctl + node_exporter textfile collector.

## Implementation

| Setting | Value |
|---|---|
| Image | `ghcr.io/analogj/scrutiny:master-omnibus` (collector + web + InfluxDB in one container) |
| Host port | `8092` |
| Container port | `8080` |
| Compose dir | `/home/{{ docker_user }}/docker/core/scrutiny` |
| Config dir | `…/scrutiny/config` |
| InfluxDB dir | `…/scrutiny/influxdb` (embedded InfluxDB 2.x) |
| Subdomain | `scrutiny.example` |
| Memory limit | `512M` (InfluxDB is the heaviest piece) |
| PID limit | `200` |
| Devices | `/dev/sda:/dev/sda` |
| cap_drop | `ALL` |
| cap_add | `SYS_RAWIO`, `SYS_ADMIN` *(house-rule deviation, see below)* |
| read_only | `false` (InfluxDB writes; configs persisted) |

### House-rule deviation — cap_add SYS_RAWIO + SYS_ADMIN

The project's container-hardening default is `cap_drop: ALL` with no re-adds. Scrutiny needs:

- `SYS_RAWIO` — raw ATA pass-through to `smartctl`.
- `SYS_ADMIN` — required by `smartctl -d ata` on some SATA bridges; included defensively. Drop if `make scrutiny` works without it on this hardware.

**Mitigation:**

1. Scrutiny runs as a single-purpose container; no other process shares the container.
2. Devices list is explicit (`/dev/sda` only) — no broad `/dev` mount.
3. `read_only` cannot be enabled because of embedded InfluxDB writes, but the bind mount is scoped to `…/scrutiny/`.
4. ADR follow-up if the deviation outlives Sprint 020 (file under `docs/design/`).

### Vault keys (new)

None — Scrutiny has no first-run admin login (web UI is unauthenticated; UFW restricts to LAN, Traefik adds TLS for Tailscale).

If multi-user access is ever needed, front it with Traefik basicauth like the Traefik dashboard.

## Acceptance

1. `make scrutiny` exits 0; container `Up` and healthy.
2. `curl -s http://scrutiny.example` returns 200.
3. `/dev/sda` listed with: model, capacity, current temperature, reallocated-sector count, power-on-hours.
4. Weekly short SMART self-test scheduled (configured via UI; documented in runbook on close).
5. SSH `smartctl -a /dev/sda` on the host still works (capability use does not lock the device).

## Pre-implementation TODO

- [ ] Context7-verify the current `ghcr.io/analogj/scrutiny:master-omnibus` env keys + volume layout.
- [ ] After first deploy, try dropping `SYS_ADMIN` from `cap_add` and confirm Scrutiny still reads `/dev/sda`. If yes, tighten the default.
- [ ] Add a vmalert rule for SMART reallocated_sector_count > 0 (follow-up commit; uses Scrutiny's Influx → Prometheus exporter if available, otherwise a `curl → textfile collector` shim).
