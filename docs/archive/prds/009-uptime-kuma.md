# PRD 009 — Uptime Kuma

> **Sprint:** [020 — Observability gap-fillers](../sprints/020-observability-gap-fillers.md)
> **Role:** `ansible/roles/uptime_kuma`
> **Status:** Implemented. The original requirements below are historical;
> current configuration is in the role and runtime evidence in [PROJECT_INDEX](../../../PROJECT_INDEX.md).

## Purpose

External / HTTP endpoint uptime monitoring with a public-style status page and push notifications to ntfy. Fills the gap between Beszel (host metrics, sustained-window CPU/load/temp/status) and vmalert (PromQL/LogsQL rules) — neither pings arbitrary URLs or renders a status page.

## Why not just vmalert + a blackbox exporter?

Considered. Rejected for three reasons:

1. **Multi-protocol** — Uptime Kuma natively probes HTTP(S), TCP, ping, DNS, Docker container state, Steam, MQTT, and push-style heartbeats. Replacing all that with vmalert needs a blackbox_exporter + multiple modules.
2. **Status page** — vmalert + Grafana can render an alert list, but UK ships a maintenance-aware public status page in one click.
3. **Operator UX** — a `wget` checked once a minute does not need a PromQL rule; UK is the right level of tool for "is jellyfin up."

vmalert keeps everything PromQL-shaped. UK owns flat "did this URL answer."

## Implementation

| Setting | Value |
|---|---|
| Image | `louislam/uptime-kuma:1` (pinned major) |
| Host port | `3011` |
| Container port | `3001` |
| Compose dir | `/home/{{ docker_user }}/docker/core/uptime-kuma` |
| Data dir | `…/uptime-kuma/data` (SQLite + uploads) |
| Subdomain | `uptime.example` (Traefik) |
| Memory limit | `256M` |
| PID limit | `200` |
| cap_drop | `ALL` (Node binary, no privileged sockets) |
| cap_add | _none_ |
| read_only | `false` (UK writes to data + node_modules cache) |
| Backup | Covered by Sprint 010 catch-all on `/home/{{ docker_user }}/docker` |

### Vault keys (new)

```yaml
# Uptime Kuma — initial admin (only used on first-run UI; UK does not seed via env)
vault_uptime_kuma_username: "admin"
vault_uptime_kuma_password: "CHANGE_ME"
```

UK has no env-driven bootstrap — operator creates the first admin in the UI on first deploy. Vault keys are there for documentation + future automation if/when UK ships a bootstrap API.

### Monitors to seed (operator, post-deploy)

- `http://jellyfin.example` (200, every 60 s) — golden-path Jellyfin reachability
- `http://grafana.homelab/api/health` (200, every 60 s)
- `http://homepage.example` (200, every 60 s)
- `tcp://192.0.2.1:53` (DNS reachability via router)

### Notification

Single ntfy channel via UK's shoutrrr-compatible transport:

- URL: `{{ ntfy_shoutrrr_url }}` (already centralized in `group_vars/all/main.yml`)
- Topic: `homelab-uptime` (separate from existing `homelab` topic so UK noise does not drown the existing alert stream)

## Acceptance

1. `make uptime-kuma` exits 0, no handler failures.
2. `curl -s http://uptime.example` returns 200.
3. Jellyfin monitor configured and reporting green.
4. `docker stop jellyfin` triggers an ntfy push to `homelab-uptime` within 90 s.
5. `docker start jellyfin` triggers a recovery push within 90 s.
6. UK survives Pi reboot (sqlite persisted; monitors restored from disk).

## Pre-implementation TODO

- [ ] Context7-verify current `louislam/uptime-kuma:1` env keys (`UPTIME_KUMA_PORT`, etc.) — the role scaffold uses defaults known stable on 1.23.x.
- [ ] Confirm shoutrrr-compatible ntfy URL syntax against current UK 1.x docs.
