# postgresql

Shared PostgreSQL cluster for the homelab. It started as the ML-stack backend
(PRD-004) and now also hosts the Authelia database. Each consumer
gets its own unprivileged user and database.

## What it deploys

A single `postgres:16-alpine` container named `postgresql`. It runs as UID/GID
`999:999` with `cap_drop: [ALL]` and `no-new-privileges`. It joins the
`mlstack_net` bridge, which this role creates. Consumers declare that network
`external: true` and connect to `postgresql:5432` by service name.

| Item         | Value                                                        |
|--------------|--------------------------------------------------------------|
| Host port    | `127.0.0.1:5432` only (admin `psql` from the Pi, no LAN, no UFW) |
| Network      | `mlstack_net` (bridge)                                       |
| Data         | `/srv/data/postgres` (pre-owned `999:999`, mode `0700`)      |
| Compose dir  | `/home/<docker_user>/docker/data/postgresql`                 |
| Memory       | `postgresql_mem_limit` (`1G`), `postgresql_shm_size` (`256M`) |
| Databases    | `mlflow`, `label_studio`, `authelia`                         |

## Deploy

```bash
make postgresql
```

This is the first step of the ML stack: postgresql, then minio, mlflow,
and label-studio, in that order.
The role fails before it starts anything if `vault_postgres_password` is shorter
than 12 characters or if any per-database password is shorter than 8.

## Configuration

- `postgresql_image`: the major version is pinned. A major bump needs `pg_upgrade`,
  so pulling a new tag is not enough.
- `postgresql_databases`: list of `{name, user, password}` entries. Users and
  databases are created idempotently. One user can own several databases
  (e.g. a *arr app's main + log databases). Passwords come from
  `vault_mlflow_db_password`, `vault_label_studio_db_password`
  and `vault_authelia_db_password`.
- `postgresql_postgres_password`: the superuser password, from `vault_postgres_password`.
  Consumers never use the superuser.
- `postgresql_server_conf` / `postgresql_shared_preload_libraries`: loads
  `pg_stat_statements` with `track_utility=off`, so that `ALTER ROLE ... PASSWORD`
  literals are never stored in the stats view.

## Operations

- **Healthcheck:** `pg_isready -U postgres -d postgres`. The deploy also waits on it.
- **Password rotation:** `ALTER ROLE` runs on every deploy, so you rotate a
  password by changing it in the vault and re-running `make postgresql`.
- **Query stats:** run the `pg_stat_statements` query from the comment in
  `defaults/main.yml`. Do not add a stats reset to the role, because it would
  wipe the cumulative counters on every deploy.
- **Backups:** the `backup` role takes online `pg_dump`s of
  `mlflow` and `label_studio`, plus a `postgresql-globals` dump. See
  [docs/runbooks/028-backup-restore.md](../../../docs/runbooks/028-backup-restore.md).
- **Tenant migrations** (SQLite to Postgres for Label Studio): data moved with
  `manage.py dumpdata -e django_rq | loaddata` (see `defaults/main.yml`).
