# 034 — MinIO: S3-compatible object storage for the ML stack

**Status:** shipped 2026-06-06. Implements the MinIO half of Faz 1 in
[PRD-004 (AI/ML Infrastructure)](../archive/prds/004-aiml-infrastructure.md) /
[Sprint 3](../archive/sprints/003-aiml-foundation.md). PostgreSQL (the other
Faz-1 foundation) shipped 2026-06-07 (role `postgresql`).

## Why

The AI/ML tools planned for this Pi (MLflow, DVC, dataset storage) all need an
S3 endpoint for artifacts and versioned data. MinIO is a single Go binary that
speaks the S3 API, runs in ~200–300 MB idle, and keeps everything on the
existing 1.8 TB SSD — no external cloud, no second machine.

This role stands MinIO up "properly" — i.e. to the repo's *current* conventions,
which are well past the 2-year-old sprint sketch: pinned image, vault-only
credentials with a fail-fast assert, non-root container, `cap_drop: [ALL]`,
`mem_limit`/`pids_limit`, a real healthcheck, and trusted HTTPS via the internal
CA (doc 033) — none of which the original sketch had.

## Decisions

- **Ports remapped to 9010 (API) / 9011 (console).** MinIO's defaults
  (9000/9001) both collide with Portainer on this Pi. The container still
  listens on 9000/9001 internally; only the host side is remapped.
- **Data on `/srv/data/minio`, not the compose dir.** Object data belongs on the
  SSD in the ML-stack namespace (`/srv/data/`), decoupled from the compose dir so
  a compose-dir wipe never risks buckets. Dir is pre-created owned by UID/GID
  1000.
- **Runs as `1000:1000` (non-root).** Because the data dir is host-owned, MinIO
  needs no in-container chown, so `cap_drop: [ALL]` works with **no** caps added
  (it's a Go binary binding only high ports). Contrast the byparr/uptime-kuma
  roles, which must re-add CHOWN/SETUID because their entrypoints chown as root.
- **Image pinned to a `RELEASE.<date>` tag, not `:latest`.** Reproducible
  deploys; bump deliberately. (The sprint sketch used `:latest` — rejected.)
- **Credentials are vault-only with a deploy-time assert.** `deploy.yml` fails
  fast if `vault_minio_root_*` are unset or the password is < 8 chars, rather
  than booting MinIO on its insecure `minioadmin/minioadmin` default.
- **Buckets via the bundled `mc`, no sidecar.** The `minio/minio` image ships
  `/usr/bin/mc`, so `configure.yml` provisions `mlflow-artifacts`, `dvc-remote`,
  `datasets` with `docker exec … mc mb --ignore-existing` (idempotent). The
  alias is passed inline via the `MC_HOST_local` env var — mc needs no writable
  config dir in the container, and the secret never appears in argv/logs (`no_log`).
- **HTTPS via the internal CA (doc 033), HTTP host ports for internal clients.**
  Adding `minio` (UI, 9011) and `minio-api` (S3, 9010) to `traefik_services`
  auto-generates `https://minio.example` / `https://minio-api.example` with the
  wildcard cert. `MINIO_BROWSER_REDIRECT_URL=https://minio.example` fixes the
  console login redirect behind the proxy. The host ports stay published (UFW =
  LAN only) so in-cluster ML clients can use `http://192.0.2.100:9010` directly
  without TLS/CA trust — same decoupling pattern as ntfy (doc 025/033).

## Consumer wiring (Faz 2, not yet deployed)

MLflow / DVC point at the S3 endpoint with the root credentials (a dedicated
service account is a future hardening step):

```text
MLFLOW_S3_ENDPOINT_URL = http://192.0.2.100:9010   (or https://minio-api.example)
AWS_ACCESS_KEY_ID       = {{ vault_minio_root_user }}
AWS_SECRET_ACCESS_KEY   = {{ vault_minio_root_password }}
MLFLOW_DEFAULT_ARTIFACT_ROOT = s3://mlflow-artifacts
```

DVC: `dvc remote add minio s3://dvc-remote` + `endpointurl` = the same.

## Observability (metrics, alerts, logs)

Wired into the existing stack — no MinIO-specific sidecars:

- **Metrics.** MinIO runs with `MINIO_PROMETHEUS_AUTH_TYPE=public`, so its
  `/minio/v2/metrics/cluster` endpoint serves unauthenticated (LAN-only via UFW,
  read-only counters). vmagent scrapes it as the `minio` job (host port 9010) →
  VictoriaMetrics. Verified live: `up{job="minio"}=1`,
  `minio_cluster_health_status=1`.
- **Alerts → ntfy.** `vmalert/templates/rules/minio.yml.j2` defines three rules,
  delivered through the existing Alertmanager → ntfy-bridge path:
  - `MinioDown` — `up{job="minio"} == 0` (container/endpoint down).
  - `MinioClusterUnhealthy` — `minio_cluster_health_status == 0` (up but lost
    read/write quorum).
  - `MinioDriveOffline` — `minio_cluster_drive_offline_total > 0`.
  Capacity is **intentionally not alerted here** — MinIO shares `/srv`, so the
  host `SrvDiskHigh`/`Critical` rules already cover it; a MinIO free-bytes rule
  would double-page the same event (alert-discipline).
- **Logs.** MinIO logs to stdout; fluentbit already ships every container's logs
  to VictoriaLogs, so `container_name:minio` is queryable with no extra config.
  (MinIO's own logger/audit webhooks exist but are overkill for a single-node
  homelab store and would duplicate the VictoriaLogs path.)

## Operations

- Deploy: `make minio && make traefik` (then `make vmagent && make vmalert` if
  the scrape job / alert rules changed).
- Console: `https://minio.example`. S3 API: `https://minio-api.example` or LAN
  `:9010`. Region `us-east-1`.
- Add a bucket: append to `minio_buckets`, re-run `make minio`.
- Bump version: edit `minio_image`, re-run `make minio`.

## Future hardening (not done)

- Per-service MinIO access keys (scoped policies) instead of root creds in every
  consumer.
- A Grafana dashboard for the MinIO metrics (official dashboard ID 13502) —
  metrics are scraped and alerting is live; only the visualization panel is
  outstanding.
- Bucket-event notifications (`MINIO_NOTIFY_WEBHOOK_*`) if a consumer ever needs
  to react to object writes — not required by MLflow/DVC today.
