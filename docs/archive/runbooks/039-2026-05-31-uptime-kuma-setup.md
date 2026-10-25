# 039 — Uptime Kuma: setup, monitors, backup/restore (reproducible)

**Status:** DONE 2026-05-31 — 25 monitors + ntfy + maintenance window + grouped
status page, all seeded over the **socket.io API** (UK 1.x has no monitor-CRUD
REST API; the authenticated frontend socket drives `add`/`addNotification`/
`addMaintenance`/`saveStatusPage`). The reproducibility artifacts are **this
runbook and the committed backup JSON**
(`ansible/roles/uptime_kuma/files/uptime-kuma-backup.json`). Re-export and
re-commit the JSON whenever monitors change — that diff is the review trail
(a drift-check is a tracked follow-up).

## Alert-ownership matrix

Avoids a 3-system storm on one crash:

- **Uptime Kuma** = endpoint/URL reachability (HTTP/TCP/ping/DNS). ntfy topic **`homelab-uptime`**.
- **vmalert** = host + container metrics + speedtest/SMART rules → Alertmanager → ntfy-bridge, ntfy topic **`homelab`** (severity→priority).
- **Beszel** = host CPU/load/temp trends.

So a Jellyfin crash: UK pages "jellyfin endpoint down" on `homelab-uptime`;
vmalert may page container/PID metrics on `homelab`. Different topics let you
tell "edge/endpoint" from "resource" at a glance.

## 1. First-run admin

Done — admin **`pi`** already existed (set during an earlier session).
`vault_uptime_kuma_password` is reconciled to the live value. **Recommended (not
yet done):** rotate it to a strong password in-UI (Settings → Security) and update
the vault key — a weak password on a LAN+Tailscale-reachable admin panel is real
exposure. A change must be made
in the UI first (UK sets the admin at first-run only), then mirrored to vault.

## 2. ntfy notification

Settings → Notifications → **Add** → type **ntfy**:

- Server URL: `http://ntfy.example:2586` · Topic: **`homelab-uptime`** · Priority: default
- Set as **default** so new monitors use it. Test → expect a phone push.

## 3. Monitors

Interval **60 s**, retries **2**, attach the ntfy notification. **Order the status
page with linchpins first** so a DNS/edge failure is visibly distinct from a
service failure.

Linchpins — raw IP:port (bypass DNS + Traefik, so "edge down" ≠ "service down"):

| Monitor | Type | Target |
|---|---|---|
| Traefik (edge) | HTTP | `http://192.0.2.100:80` (expect 404 — proxy alive) |
| AdGuard (DNS UI) | HTTP | `http://192.0.2.100:8080` |
| Router DNS | DNS | `192.0.2.1` resolve `google.com` |
| Pi (LAN) | Ping | `192.0.2.100` |
| Internet | Ping | `1.1.1.1` |
| Tailscale | Ping | `100.64.0.10` |

Services — via `*.example` (exercises the real DNS+proxy path users hit), including:

- Jellyfin `https://jellyfin.homelab/health`
- Jellyseerr `https://jellyseerr.example`
- qBittorrent `https://qbittorrent.example`
- Sonarr `https://sonarr.homelab/ping`, Radarr `https://radarr.homelab/ping`, Prowlarr `https://prowlarr.homelab/ping`
- byparr `http://192.0.2.100:8191/docs` (raw IP:port — not behind Traefik; /docs is its healthcheck; leak/restart history makes it the most worth watching). **False-green caveat:** `/docs` returns 200 even when the Camoufox browser is wedged (the documented F9 blind spot) — a green dot here proves the API is up, NOT that a bypass works. The real leak signal stays the `byparr_prerestart_*` metrics + the weekly restart cron, not this monitor.
- Homepage `https://homepage.example`, Grafana `https://grafana.homelab/api/health`, Portainer `https://portainer.example`
- ntfy `http://ntfy.example:2586`, Speedtest `https://speedtest.example` (Scrutiny retired 2026-06-04 — monitor removed)
- Maintainerr `https://maintainerr.example`, Filebrowser `https://filebrowser.example`, Beszel `https://beszel.example`

## 4. Maintenance window (avoid nightly false pages)

Settings → Maintenance → add a **recurring daily window 03:55–05:20** covering the
restart band (byparr Sun 04:00, media-app restarts 04:00–04:15, smart-check
05:00, weekly backup). Apply to the *arr/byparr monitors. Without this, the 60 s /
2-retry checks flag every nightly restart as an outage and train you to ignore
the board.

## 5. Status page

Done — status page slug **`homelab`** (`/status/homelab`), published, with groups
**Linchpins** (6) / **Media** (10, incl. byparr) / **Infra & Observability** (9),
linchpins first. Seeded via `saveStatusPage` (the `config` object must include
`domainNameList: []` or the call rejects with "Invalid array").

**Exposure (confirmed 2026-05-31):** `published: true` is reachable on **LAN +
Tailscale only** — no public tunnel container (cloudflared/ngrok/frp), home NAT
with no port-forward, UFW + DOCKER-USER scope `:3011`/`:80` to the LAN
(`192.0.2.0/16`) and Tailscale (`100.64.0.0/10`) ranges only. The page lists
the service inventory + LAN IPs, so do **not** add a public Traefik/tunnel route.

## 6. Meta-monitor (vmalert watches UK)

Settings → **API Keys** → create a read-only key. Then:

```bash
ansible-vault edit ansible/inventory/group_vars/all/vault.yml   # set vault_uptime_kuma_metrics_key: "<key>"
make vmagent     # enables the gated uptime-kuma scrape job
```

The `UptimeKumaDown` vmalert rule (already shipped, gated) goes live. **Verify**
`up{job="uptime-kuma"}=1` in VictoriaMetrics before treating it as active.

## 7. Backup, seed & restore (the reproducibility step)

`ansible/roles/uptime_kuma/files/uptime-kuma-backup.json` is the committed
source of truth (diffable). It's deployed to the Pi and a weekly **drift check**
(`uptime-kuma-drift.sh` → `uptime_kuma_config_drift` metric → `UptimeKumaConfigDrift`
alert; `make uptime-kuma-drift` for the diff) catches UI edits that diverge from
it. If you edit monitors in the UI, re-export/update the JSON and commit.

**Seed / restore (runnable, idempotent):**

```bash
make uptime-kuma-seed   # ansible --tags seed: runs seed-from-backup.js INSIDE
                        # the container (bundles socket.io-client) to recreate
                        # notifications + monitors. Idempotent — existing items
                        # (by name) are skipped.
```

**Restore after a data-volume wipe / rebuild:** `make uptime-kuma` → complete
first-run admin (vault creds) → `make uptime-kuma-seed` (recreates the 25
monitors + 2 notifications) → re-apply the maintenance window (§4) + status-page
grouping (§5) in the UI (those aren't seeded — they rarely change). A native
Settings → Backup → **Import** also works if you keep a UI export.

**Restore drill (done 2026-05-31):** ran `make uptime-kuma-seed` against the live
instance → `created=0 skipped=25`, proving the JSON→UK rebuild path connects,
authenticates, and is idempotent without touching existing monitors.
