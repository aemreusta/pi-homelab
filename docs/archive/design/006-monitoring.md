# Monitoring & Alerting

> **Historical monitoring baseline.** The maintained topology is
> [architecture 001](../../design/001-architecture.md#workload-and-observability-flow);
> current service ownership is [inventory 037](../../design/037-service-inventory-and-relationships.md).
> Beszel now has independent freshness monitoring; old counts and gaps below
> describe their original observation window.
>
> For the full two-pipeline data-flow (metrics + logs → one alert path), the textfile-emitter
> inventory, and the cross-stack service map, see
> [037-service-inventory-and-relationships.md](../../design/037-service-inventory-and-relationships.md).

## Overview

Three-layer monitoring stack: **VictoriaMetrics** for long-term metrics, **Beszel** for lightweight real-time dashboards, **ntfy** for push notifications. **Grafana** provides the unified query UI for logs and metrics.

```text
node_exporter ─┐                          ntfy ──→ Phone
cadvisor ──────┤                          ▲
vmagent ───────┼──→ VictoriaMetrics       │
               │    (90d metrics)    Beszel alerts
VictoriaLogs ──┘    (30d logs)       Cron scripts
  (Docker logs)          │                │
                         └──→ Grafana ←───┘
                              (dashboards, explore)
```

### Stack History

| Phase | Scope | Status |
|-------|-------|--------|
| Phase 1 | Beszel + ntfy (metrics dashboards + push alerts) | Deployed |
| Phase 2 | VictoriaMetrics, VictoriaLogs, Grafana, vmagent, node_exporter, cAdvisor | Deployed (Sprint 9) |
| Phase 3 | vmalert (PromQL) + vmalert-logs (LogsQL) + Alertmanager + ntfy-bridge | Deployed (Sprint 18); **authoritative alert path since 2026-05-26** (`common_use_vmalert: true`) — legacy cron alert scripts removed, Beszel rules reduced to CPU/LoadAvg5 |

## Services

### Observability Stack (Victoria)

| Service | Port | Purpose |
|---------|------|---------|
| VictoriaMetrics | 8428 | TSDB, 90-day metric retention |
| VictoriaLogs | 9428 | Log database, 30-day retention |
| vmagent | 8429 | Prometheus-compatible scraper |
| Grafana | 3003 | Dashboards + explore UI |
| node_exporter | 9100 | Host hardware metrics (host network) |
| cAdvisor | 8081 | Per-container CPU/RAM/network/IO |

All observability containers share a `observability` Docker bridge network. vmagent and node_exporter/cAdvisor use host network mode (DOCKER-USER iptables chain blocks bridge→host traffic).

### Beszel + ntfy

| Service | Port | URL | Purpose |
|---------|------|-----|---------|
| Beszel Hub | 8090 | `http://192.0.2.100:8090` | Monitoring dashboard |
| Beszel Agent | 45876 | (internal) | Host metrics collector |
| ntfy | 2586 | `http://192.0.2.100:2586` | Push notification server |

## Victoria Stack

### vmagent Scrape Targets

vmagent runs on host network and scrapes every 30s:

| Job | Target | Metrics Path |
|-----|--------|-------------|
| node-exporter | `127.0.0.1:9100` | `/metrics` |
| cadvisor | `127.0.0.1:8081` | `/metrics` |
| victorialogs | `127.0.0.1:9428` | `/metrics` |
| victoriametrics | `127.0.0.1:8428` | `/metrics` |
| vmagent-self | `127.0.0.1:8429` | `/metrics` |
| traefik | `127.0.0.1:8180` | `/metrics` |
| ntfy | `127.0.0.1:2586` | `/metrics` |

### Grafana Dashboards

Eight provisioned dashboards — four grafana.com imports + four local JSONs under
`roles/grafana/files/dashboards/` (synced by the `copy: src=dashboards/` task; add
a new one by dropping a JSON there with an explicit `uid`).

**grafana.com imports** (downloaded at deploy time, listed in `grafana_dashboards`):

| Dashboard | ID | Datasource | Purpose |
|-----------|-----|-----------|---------|
| VictoriaLogs Single Node | 22084 | VictoriaLogs | VL ingestion/storage health |
| VictoriaMetrics Single Node | 10229 | VictoriaMetrics | TSDB health |
| Node Exporter Full | 1860 | VictoriaMetrics | Host hardware |
| Docker cAdvisor | 193 | VictoriaMetrics | Container metrics |

**Local (homelab-specific)**:

| Dashboard (uid) | Datasource | Purpose |
|-----------------|-----------|---------|
| `smart-disk-health` | VictoriaMetrics | SSD SMART health/temp/realloc from `smart_check_*` (replaces retired Scrutiny) |
| `homelab-overview` | VictoriaMetrics + VictoriaLogs | One-pane host/container/disk/log-error health |
| `container-log-errors` | VictoriaLogs | Top error producers / timeline / recent lines via `log_level:error` |

> **`log_level` coverage caveat:** the `homelab-overview` error count and the entire
> `container-log-errors` dashboard rely on the `log_level` field set by
> `fluentbit/enrich.lua`, which only extracts **bracketed** levels (`[Error]`/`[ERR]`).
> Services that don't bracket their logs — VictoriaMetrics, VictoriaLogs, fluentbit,
> cAdvisor — emit no `log_level`, so their errors aren't reflected. Those log to
> journald and the ones that matter are watched by vmalert directly.

### Datasources

- **VictoriaMetrics**: `http://victoriametrics:8428` (Prometheus-compatible)
- **VictoriaLogs**: `http://victorialogs:9428` (LogsQL via Loki handler)

## Beszel

### Architecture

Both hub and agent run with `network_mode: host` so the hub connects to the agent on `127.0.0.1:45876`. This avoids Docker bridge → host firewall issues.

- **Hub**: PocketBase-backed dashboard, stores metrics history
- **Agent**: Collects CPU, memory, disk, temperature, Docker container stats
- **Auth**: `AUTO_LOGIN` enabled (single-user homelab, LAN-only access via UFW)

### Agent Configuration

| Env Var | Value | Purpose |
|---------|-------|---------|
| `LISTEN` | `45876` | SSH server port for hub connection |
| `KEY` | (from vault) | SSH public key for hub authentication |
| `FILESYSTEM` | `/dev/sda` | Primary SSD monitoring |
| `EXTRA_FILESYSTEMS` | `/dev/mmcblk0p2` | SD card monitoring |
| `SMART_DEVICES` | `/dev/sda:sat` | S.M.A.R.T. health via USB-SATA adapter |

### Alert Rules

Configured via PocketBase API in `configure.yml` (fields: `name`, `min` duration, `value` threshold):

| Alert | Threshold | Duration | ntfy Priority |
|-------|-----------|----------|---------------|
| CPU Usage | > 85% | 5 minutes | default |
| Memory | > 80% | immediate | default |
| Disk | > 75% | immediate | default |
| Temperature | > 70C | immediate | default |
| Status | any change | immediate | default |

### Key Fix: Agent Key

The agent requires a valid SSH public key (not a random string). To get the correct key:

```bash
# Extract public key from hub's private key
sudo ssh-keygen -y -f ~/docker/core/beszel/data/id_ed25519
# Output: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5...
```

This key goes in `vault_beszel_agent_key`.

## ntfy

### Setup

Self-hosted ntfy instance for LAN push notifications. No authentication (LAN-only via UFW).

```bash
# Test notification with emoji and action button
curl -H "Title: Test Notification" \
     -H "Tags: white_check_mark" \
     -H "Actions: view, Open Homepage, http://raspberrypi.local, clear=true" \
     -d "Hello from homelab!" \
     http://192.0.2.100:2586/homelab

# Subscribe on phone
# Install ntfy app → Add server: http://192.0.2.100:2586 → Subscribe to: homelab
```

### Notification Sources

All notifications go to the `homelab` topic with emojis, priorities, and action buttons.

#### Control API Actions (triggered from web panel or API)

| Event | Tag Emoji | Title | Priority | Action Buttons |
|-------|-----------|-------|----------|----------------|
| Shutdown | red_circle | Homelab Shutting Down | high | — |
| Restart | arrows_counterclockwise | Homelab Restarting | high | — |
| Update | package | Containers Updated | default | [Open Homepage](http://homepage.example) |
| Clean | broom | Docker Cleaned | default | — |
| Junk Clean | wastebasket | Junk Cleaned | default | [Open Storage](http://control.example) |

#### Cron Job Alerts

| Script | Schedule | Tag Emoji | Title | Action Buttons |
|--------|----------|-----------|-------|----------------|
| disk-alert.sh | Every 6h at :15 | warning + floppy_disk | Disk XX% on /srv | [Open Storage](http://control.example), Clean Junk |
| memory-alert.sh | Hourly at :30 | warning + brain | Swap at XX% | [Open Beszel](http://beszel.example), Restart |
| docker-prune.sh | Sunday 04:00 | whale | Docker Prune Complete | — |
| junk-scan.sh | Sunday 03:00 | mag + wastebasket | Junk Scan: N files found | [Review & Clean](http://control.example) |

#### Beszel System Alerts

Beszel sends alerts via Shoutrrr webhook: `ntfy://192.0.2.100:2586/homelab?scheme=http`

Triggers on CPU > 85%, Memory > 80%, Disk > 75%, Temperature > 70C, Status change.

### Action Button URLs

All action buttons use `*.example` domains (resolved via AdGuard DNS on LAN):

| Button | URL | Type |
|--------|-----|------|
| Open Homepage | `http://homepage.example` | view |
| Open Storage | `http://control.homelab/?token=...#storage-section` | view |
| Open Beszel | `http://beszel.example` | view |
| Review & Clean | `http://control.homelab/?token=...#junk-section` | view |
| Clean Junk | `http://control.homelab/junk` | http POST |
| Restart | `http://control.homelab/restart` | http POST |

- **view** buttons open a URL in the phone browser
- **http** buttons send an HTTP request directly from the phone (requires LAN/Tailscale)
- All URLs require the phone to be on the home LAN or connected via Tailscale

### Priority Levels

| Priority | Phone Behavior | Used For |
|----------|---------------|----------|
| `urgent` | Loud alarm, bypasses DND | Disk > 90%, Swap > 90% |
| `high` | Sound + vibration | Disk/memory threshold, shutdown, restart |
| `default` | Normal notification | Updates, prune, junk scan |
| `low` | Silent | Informational (future) |

## Cron Jobs

### Docker System Prune

Weekly cleanup of dangling images, stopped containers, unused networks.

```text
Schedule: Sunday 04:00
User: root
Script: /usr/local/bin/docker-prune.sh
ntfy: 🐳 Docker Prune Complete (always)
```

### Disk Usage Alert

Checks `/srv` and `/` every 6 hours. Sends ntfy alert if usage exceeds 75%.

```text
Schedule: every 6 hours at :15
User: root
Script: /usr/local/bin/disk-alert.sh
ntfy: 💾 Disk XX% (threshold exceeded only)
```

### Memory Monitoring

Checks swap usage hourly. Sends ntfy alert if swap >= 75%. Logs containers > 1GB.

```text
Schedule: every hour at :30
User: root
Script: /usr/local/bin/memory-alert.sh
ntfy: 🧠 Memory Alert (threshold exceeded only)
```

### Junk File Scan

Weekly scan for macOS junk files (._*, .DS_Store, etc.). Reports findings via ntfy.

```text
Schedule: Sunday 03:00
User: pi
Script: /opt/homelab/control/junk-scan.sh
ntfy: 🗑️ Junk Scan: N files found (files > 0 only)
```

## CPU Stress Test

One-time script to establish thermal baseline:

```bash
# Run from local machine (sends script via SSH)
ssh homelab 'bash -s' < scripts/cpu-stress-test.sh [duration_seconds]

# Default: 120s, 4 CPU workers
# Monitors: temperature, frequency, throttle status
# Outputs: idle baseline → stress test → cooldown → peak summary
```

### Throttle Flag Reference

| Flag | Meaning |
|------|---------|
| `0x0` | No throttling |
| `0x50000` | Throttling occurred (past) |
| `0x50005` | Currently throttled |

## Deployment

```bash
# Observability stack (VictoriaMetrics, VictoriaLogs, Grafana, vmagent, node_exporter, cAdvisor)
make victoriametrics
make victorialogs
make grafana
make vmagent
make node-exporter
make cadvisor

# Legacy monitoring (still active)
make ntfy     # Deploy ntfy push notifications
make beszel   # Deploy Beszel hub + agent + alerts
make base     # Includes monitoring crons (disk alert, memory, docker prune)
```

## Troubleshooting

### Beszel shows "Down"

1. Check agent logs: `docker logs beszel-agent --tail 20`
2. If "Failed to load public keys" → agent key is wrong. Re-extract from hub (see above)
3. If hub can't reach agent → both must use `network_mode: host`

### ntfy not receiving alerts

1. Test directly: `curl -d "test" http://127.0.0.1:2586/homelab`
2. Check Beszel webhook URL in Settings > Notifications
3. Verify UFW allows port 2586 from LAN

### Container memory stats errors

Beszel agent may log `bad memory stats` for some containers. This is a known issue (henrygd/beszel#144) on certain kernel versions. Stats for those containers are skipped; other monitoring continues normally.

### VictoriaMetrics not receiving data

1. Check vmagent logs: `docker logs vmagent --tail 20`
2. Verify vmagent can reach VictoriaMetrics: `curl http://127.0.0.1:8428/api/v1/query?query=up`
3. Check scrape targets in vmagent config at `/home/pi/docker/observability/vmagent/config/`

### Grafana can't query datasources

1. Both Grafana and Victoria services must be on the `observability` network
2. Check datasource URLs use container names: `http://victoriametrics:8428`, `http://victorialogs:9428`
3. Verify containers are running: `docker ps --filter network=observability`
