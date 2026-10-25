# victorialogs

Single-node log database for the observability stack, queried with LogsQL.
Fluent Bit ships container logs here; Grafana and vmalert's logs instance
query it.

## What it deploys

One `victoriametrics/victoria-logs:latest` container (`victorialogs`) on the
shared `observability` bridge network (created if missing). It runs hardened:
read-only root filesystem, `/tmp` on tmpfs, `cap_drop: [ALL]` and
`no-new-privileges`. The data directory is root-owned to match the container UID.

| Item          | Value                                                     |
|---------------|-----------------------------------------------------------|
| HTTP API/vmui | host `:9428` (`victorialogs_port`), LAN-only in UFW       |
| Traefik       | `https://logs.example.internal`                           |
| In-network    | `http://victorialogs:9428` (Grafana, vmalert)             |
| Data          | `/home/pi/docker/observability/victorialogs/data`       |
| Retention     | `30d` (`victorialogs_retention_period`)                   |
| Memory limit  | `768M` (`victorialogs_mem_limit`)                         |

## Deploy

```bash
make victorialogs
make observability   # the whole stack
```

Order: victoriametrics → victorialogs → grafana → vmagent → node-exporter →
cadvisor → fluentbit.

## Configuration

- `victorialogs_retention_period` — how long logs are kept (`30d`).
- `victorialogs_mem_limit`, `victorialogs_pids_limit`,
  `victorialogs_tmpfs_size` — resource caps.
- `victorialogs_network` — shared bridge network (`observability`).

## Operations

- **No Docker healthcheck, on purpose.** The upstream image became distroless
  in v1.52.0 (2026-07-16): it has no shell or `wget`, so an exec healthcheck
  can never pass. The container therefore shows no health status. See the
  [incident](../../../docs/incidents/2026-07-16-victorialogs-distroless-image-broke-exec-healthcheck.md).
- **Liveness:** vmagent scrapes `:9428/metrics` as job `victorialogs`.
  `VictoriaLogsDown` (crit, `up{job="victorialogs"} == 0`) is the liveness
  signal. While it fires, every LogsQL-based alert is blind and Fluent Bit
  buffers or drops logs.
- Known-benign error lines are dropped by Fluent Bit before they reach this
  store. See
  [docs/runbooks/020-known-log-noise.md](../../../docs/runbooks/020-known-log-noise.md).
