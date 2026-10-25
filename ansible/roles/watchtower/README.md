# watchtower

Automatic container image updater. Once a day it checks running containers for
newer images, recreates the ones it updates, and posts a single summary to ntfy.

## What it deploys

One `containrrr/watchtower:latest` container (`watchtower`) with
`/var/run/docker.sock` mounted. It opens no ports, so the role has no UFW tasks.
It runs hardened: read-only root filesystem, `/tmp` on tmpfs,
`cap_drop: [ALL]` and `no-new-privileges`.

| Item          | Value                                                  |
|---------------|--------------------------------------------------------|
| Poll interval | `86400`s, daily (`watchtower_interval`)                |
| Cleanup       | `WATCHTOWER_CLEANUP=true` (old images removed)         |
| Notifications | shoutrrr → ntfy (`ntfy_shoutrrr_url`), one report per scan |
| Compose dir   | `/home/pi/docker/core/watchtower`                    |
| Memory limit  | `128M` (`watchtower_mem_limit`)                        |

## Deploy

```bash
make watchtower
```

## Configuration

- `watchtower_interval` — seconds between update checks.
- `watchtower_excluded_containers` — `mlflow`.
  Ansible builds this image locally, so no registry can update it.
- `watchtower_ntfy_url` — taken from `ntfy_shoutrrr_url` in
  `inventory/group_vars/all/main.yml`. Never hardcode the host.
- `watchtower_notifications_hostname` — host name shown in notifications.

## Operations

- **Healthcheck:** `/watchtower --health-check`.
- Watchtower does not update every container. See
  [docs/design/037-service-inventory-and-relationships.md](../../../docs/design/037-service-inventory-and-relationships.md)
  for which images it owns.
- `extra_hosts` adds `ntfy_hostname` → `lan_ip` to the container's own
  `/etc/hosts`. Docker's embedded DNS does not read the host's `/etc/hosts`, so
  this lets notifications resolve without AdGuard.
- Watchtower applies upstream `:latest` changes on its own schedule. If a
  container turns `(unhealthy)` right after a Watchtower run while its endpoints
  still answer, check `.State.Health.Log` before restarting anything. See the
  [VictoriaLogs distroless incident](../../../docs/incidents/2026-07-16-victorialogs-distroless-image-broke-exec-healthcheck.md).
