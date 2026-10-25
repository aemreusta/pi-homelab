# Sprint 020 — Observability gap-fillers

> **Historical sprint record.** Statuses and checklists below describe the original
> work window, not today's backlog or deployment state. See
> [PROJECT_INDEX](../../../PROJECT_INDEX.md) for dated evidence.
>
> **Status:** Planning (scaffolded 2026-05-22, awaiting operator green-light).
> **Predecessor:** Sprint 018 alerting substrate (vmalert + Alertmanager) live and in soak.
> **Depends on:** Traefik + AdGuard wildcard `*.example` (already in place), Homepage role.
> **PRDs:** [009-uptime-kuma](../prds/009-uptime-kuma.md), [010-speedtest-tracker](../prds/010-speedtest-tracker.md), [011-scrutiny](../prds/011-scrutiny.md).

## Charter

Close three observability gaps the current stack (Beszel + Victoria{Metrics,Logs} + vmalert + Alertmanager + ntfy) does not cover:

| Gap | Today | Sprint 020 |
|---|---|---|
| **External / HTTP endpoint uptime** | vmalert needs PromQL targets; Beszel watches host metrics, not arbitrary URLs | Uptime Kuma — multi-protocol probes + status page + push to ntfy |
| **ISP bandwidth tracking** | None | Speedtest Tracker — hourly Ookla run, persisted to SQLite, Grafana-friendly |
| **SSD SMART health** | Beszel reports `df` usage only | Scrutiny — SMART attributes + reallocated sectors + projected wear for `/dev/sda` |

No story duplicates Beszel/vmalert. Each is additive and small (single container, no migration risk).

## Stories

### Story 1 — Uptime Kuma (`uptime_kuma` role)

- Image `louislam/uptime-kuma:1` (pinned major; see PRD for rationale).
- Host port `3011` → container `3001`. Exposed at `uptime.example` via Traefik.
- SQLite at `/srv` bind not required — runs out of `/home/{{ docker_user }}/docker/core/uptime-kuma/data`, included in backup role's catch-all.
- Monitors seeded by operator post-deploy (no API automation in v1 — Uptime Kuma's API is unstable across 1.x → 2.x). Document seed list in PRD.
- Notification: ntfy via UK's built-in shoutrrr-compatible transport (`{{ ntfy_shoutrrr_url }}`).
- **Acceptance:** `make uptime-kuma` deploys; `uptime.example` returns 200; one monitor (Jellyfin `/health`) configured; failure of `docker stop jellyfin` produces an ntfy push within 90s; restoring jellyfin produces a recovery push.

### Story 2 — Speedtest Tracker (`speedtest_tracker` role)

- Image `lscr.io/linuxserver/speedtest-tracker:latest`.
- Host port `8765` → container `80`. Exposed at `speedtest.example` via Traefik.
- Schedule: hourly (`SPEEDTEST_SCHEDULE="0 * * * *"`). Stores results in `data/` SQLite.
- Prometheus endpoint exposed at `/api/v1/metrics/prometheus` — added to vmagent scrape config for Grafana dashboards.
- **Acceptance:** `make speedtest` deploys; first scheduled run completes < 90s; result row visible in UI; `up{job="speedtest"}` returns 1 in VictoriaMetrics within 5 min.

### Story 3 — Scrutiny (`scrutiny` role) — ❌ RETIRED 2026-06-04

> **Retired.** Scrutiny's collector runs `smartctl --info` without `-d scsi` at the
> device-detection step, so it never detected the SanDisk USB3→SATA SSD and its DB
> stayed empty while reporting "healthy". Upstream-unfixable for USB-bridge disks.
> Replaced by the `common` role's `smart-check.sh` (already shipping `smart_check_*`
> metrics + 3 vmalert rules) and the `smart-disk-health` Grafana dashboard. Role,
> playbook, Makefile target, Traefik route, homepage tile, and Uptime-Kuma monitor
> all removed. See PRD 011 tombstone. The original spec below is kept for history.

- Image `ghcr.io/analogj/scrutiny:master-omnibus` (single-container variant — Pi has one disk).
- Host port `8092` → container `8080`. Exposed at `scrutiny.example` via Traefik.
- **House-rule deviation:** needs `--cap-add SYS_RAWIO` + `--cap-add SYS_ADMIN` + `/dev/sda` device pass-through to read SMART. Documented exception in PRD (file an ADR if the deviation outlives the sprint).
- InfluxDB embedded — data path on local volume, no external dep.
- **Acceptance:** `make scrutiny` deploys; `scrutiny.example` shows `/dev/sda` with model + temp + reallocated-sector count; SMART self-test schedule visible.

## Deploy order

1. Land all three role scaffolds (this sprint scaffolds only — no deploy yet).
2. Operator review + Context7-verify image tags/env keys before first `make`.
3. `make uptime-kuma` → seed one monitor → confirm ntfy push.
4. `make speedtest` → wait one hour for first scheduled run → confirm row + Prometheus scrape.
5. `make scrutiny` → confirm disk visible → schedule weekly short SMART self-test in UI.
6. Add three Homepage tiles (already templated in this sprint's commits).
7. Add three Traefik service entries (already templated).
8. Sprint 020 acceptance: all three reachable on `*.example`, one alert path proven end-to-end (Uptime Kuma → ntfy).

## Out of scope

- Migrating Beszel alert rules into Uptime Kuma. Beszel keeps CPU/load/temp/status (sustained-window) per [docs/design/024-alert-discipline.md](../../design/024-alert-discipline.md). Uptime Kuma owns *endpoint* uptime, not *host* metrics.
- Replacing vmalert. PromQL/LogsQL rules stay in vmalert.
- Multi-disk Scrutiny (collector + influxdb split). Single-disk Pi doesn't justify the topology.
- API-driven monitor seeding for Uptime Kuma — operator UI for v1.

## Risks

- **Scrutiny cap exception:** breaks the cap_drop ALL house rule. Mitigation: PRD documents and `docker-compose.yml.j2` carries a comment explaining why. If Scrutiny ever ships a SMART proxy that doesn't need raw IO, revisit.
- **Speedtest WAN saturation:** hourly Ookla runs are ~30 s of full-bandwidth traffic. Mitigation: schedule `0 * * * *` (top of hour, lighter LAN load) and document opt-down to 4-hourly if jellyfin streams collide.
- **Uptime Kuma 2.0 churn:** image pinned to `:1` until 2.x has a stable API + persistence migration path.
