# mlflow

MLflow tracking server + model registry for the homelab ML stack ([PRD-004](../../../docs/archive/prds/004-aiml-infrastructure.md), Faz-2).

## What it does

- Experiment tracking, runs, metrics, params, and the model registry.
- **Backend store:** PostgreSQL `mlflow` DB, reached as `postgresql:5432` over the
  shared `mlstack_net` bridge (created by the `postgresql` role).
- **Artifact store:** MinIO `s3://mlflow-artifacts`, served in **`--serve-artifacts`
  proxy mode** — clients log/fetch artifacts *through* MLflow, so only this server
  holds S3 credentials.

## Image

Built locally from `files/Dockerfile` with **uv** (`homelab/mlflow:latest`).
The upstream slim image omits `psycopg2` + `boto3`, which a Postgres backend and S3
artifacts both require, plus `prometheus-flask-exporter` for `/metrics`. No version
pinning (operator directive) — `build: always` reinstalls latest each deploy.

## Security

- Non-root (`1000:1000`), `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit`.
- **Scoped MinIO credentials.** `configure.yml` mints a MinIO service user bound to
  a policy that allows access to the `mlflow-artifacts` bucket *only* — never root,
  never the sensitive `datasets` bucket. The MinIO root creds are used solely to mint that
  key (admin API via the container's `mc`, root creds passed via `MC_HOST_*` env).
- LAN-only host port `5000` (UFW); `mlflow.example` via Traefik. Note: MLflow has no
  built-in auth and every service is also LAN-open by host port — this is **not** an
  auth boundary (see `docs/conventions/reverse-proxy-not-an-auth-boundary.md`).

## Prerequisites (deploy order)

1. `make postgresql` — creates `mlstack_net`, the `mlflow` DB + user.
2. `make minio` — buckets incl. `mlflow-artifacts`.
3. Vault: `vault_mlflow_db_password`, `vault_minio_mlflow_access_key`,
   `vault_minio_mlflow_secret_key` (see `vault.yml.example`).
4. `make mlflow`.

## Backups

The `mlflow` Postgres DB is captured by the `postgresql-mlflow` `pg_dump` entry in
the `backup` role. Artifacts live on the SSD under `/srv/data/minio`.

## Verify

```bash
curl -s http://<pi>:5000/health           # -> OK
# log a run + artifact (proxied through MLflow, no MinIO creds on the client):
MLFLOW_TRACKING_URI=http://<pi>:5000 python -c "import mlflow; mlflow.set_experiment('smoke'); mlflow.start_run(); mlflow.log_metric('x',1); mlflow.log_text('hi','a.txt'); mlflow.end_run()"
```

## Known follow-ups

- Rotating `vault_minio_mlflow_secret_key` re-syncs on redeploy (`mc admin user add`
  is an upsert); the Postgres user rotates via the role's `ALTER ROLE` task.
