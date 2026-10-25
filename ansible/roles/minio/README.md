# minio

S3-compatible object storage for the homelab AI/ML stack (PRD-004). Backs the
MLflow artifact store, the DVC remote, and dataset storage.

## What it deploys

A single `minio/minio` container (pinned release), running as `1000:1000`, with
object data on the SSD at `/srv/data/minio`. The bundled `mc` client provisions
the buckets post-boot.

| Item            | Value                                              |
|-----------------|----------------------------------------------------|
| S3 API          | host `:9010` → container `:9000`                    |
| Web console     | host `:9011` → container `:9001`                    |
| Data            | `/srv/data/minio` (host-owned, UID/GID 1000)       |
| Buckets         | `mlflow-artifacts`, `dvc-remote`, `datasets`       |
| Traefik (HTTPS) | `https://minio.example` (UI), `https://minio-api.example` (S3) |

Host ports 9010/9011 are remapped because MinIO's defaults (9000/9001) clash
with Portainer on this Pi.

## Access

- **Console (browser):** `https://minio.example` (trusted internal-CA cert on
  LAN + Tailscale).
- **S3 API, internal clients:** `http://192.0.2.100:9010` (LAN host port) or
  `https://minio-api.example`. Region `us-east-1` (MinIO default).
- **Credentials:** `vault_minio_root_user` / `vault_minio_root_password`
  (Ansible Vault). The deploy fails fast if they're unset or < 8 chars.

## Deploy

```bash
make minio        # role: container + buckets + UFW
make traefik      # publishes minio.example / minio-api.example HTTPS routers
```

UFW allows 9010/9011 from the LAN subnet only; external access is via Traefik.

## Security

- Runs non-root (`user: 1000:1000`); data dir pre-owned, so `cap_drop: [ALL]`
  with no caps added.
- `no-new-privileges`, `mem_limit`, `pids_limit` per repo convention.
- Root credentials live only in the vault and are passed to `mc` via the
  `MC_HOST_local` env var (never in argv) during bucket provisioning.

## Observability

- **Metrics:** scraped by vmagent (`/minio/v2/metrics/cluster`, public auth) →
  VictoriaMetrics. Job `minio`.
- **Alerts (→ ntfy):** `vmalert` rules `MinioDown`, `MinioClusterUnhealthy`,
  `MinioDriveOffline` (`roles/vmalert/templates/rules/minio.yml.j2`). Capacity is
  covered by the host `/srv` disk alerts — not duplicated here.
- **Logs:** fluentbit ships the `minio` container stdout to VictoriaLogs
  automatically (`container_name:minio`).

## Notes

- Image is **pinned** (`RELEASE.<date>`), not `:latest` — bump deliberately.
- Buckets are created idempotently (`mc mb --ignore-existing`); adding a bucket
  = append to `minio_buckets` and re-run `make minio`.
- See `docs/design/034-minio-object-storage.md` for design rationale and the
  MLflow/DVC consumer wiring.
