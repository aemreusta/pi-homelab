# victoriametrics

Single-node, Prometheus-compatible time-series database for the observability
stack. vmagent and vmalert write to it; Grafana and vmalert query it.

## What it deploys

One `victoriametrics/victoria-metrics:latest` container (`victoriametrics`) on
the shared `observability` bridge network (the role creates the network if it is
missing). It runs hardened: read-only root filesystem, `/tmp` on tmpfs,
`cap_drop: [ALL]` and `no-new-privileges`. The data directory is owned by root
because the container runs as UID 0 without `CAP_DAC_OVERRIDE`.

| Item         | Value                                                              |
|--------------|--------------------------------------------------------------------|
| HTTP API/vmui | host `:8428` (`victoriametrics_port`), LAN-only in UFW            |
| Traefik      | `https://metrics.example.internal`                                 |
| In-network   | `http://victoriametrics:8428` (Grafana, vmalert)                   |
| Data         | `/home/pi/docker/observability/victoriametrics/data`             |
| Retention    | `90d` (`victoriametrics_retention_period`)                         |
| Memory limit | `384M` (`victoriametrics_mem_limit`)                               |

## Deploy

```bash
make victoriametrics
make observability   # the whole stack
```

Deploy it first in the observability stack: victoriametrics → victorialogs →
grafana → vmagent → node-exporter → cadvisor → fluentbit.

## Configuration

- `victoriametrics_retention_period` — how long metrics are kept (`90d`).
- `victoriametrics_cache_timestamp_offset` — `10m`. vmalert writes can land
  5-6 minutes behind wall time, so that recent range is not cached.
- `victoriametrics_mem_limit`, `victoriametrics_pids_limit`,
  `victoriametrics_tmpfs_size` — resource caps.
- `victoriametrics_network` — shared bridge network (`observability`).

## Operations

- **Healthcheck:** `wget --spider http://127.0.0.1:8428/health`. It uses
  `127.0.0.1`, not `localhost`, because `localhost` can resolve to `::1` first
  while VM listens on IPv4 only.
- **Scraped by vmagent** as job `victoriametrics`. `VMAgentRemoteWriteFailing`
  fires when vmagent cannot write to `:8428`.
- **Off-box liveness:** the dead-man heartbeat beats only while VM answers a
  local query. See
  [runbook 040](../../../docs/runbooks/040-2026-05-31-deadman-switch-activation.md).
- Architecture context:
  [docs/design/001-architecture.md](../../../docs/design/001-architecture.md#workload-and-observability-flow).
