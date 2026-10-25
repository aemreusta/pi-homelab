# jellyseerr

Jellyseerr is the Netflix-style media request UI, signed in through the
homelab's Jellyfin server.

## What it deploys

A single `fallenbagel/jellyseerr:latest` container (`jellyseerr`) on its own
`jellyseerr_net` bridge, with `init: true`, `cap_drop: [ALL]`, a read-only root
filesystem and a tmpfs `/tmp`.

| Item            | Value                                            |
|-----------------|--------------------------------------------------|
| Web UI          | host `:5055` → container `:5055` (UFW: `lan_subnet`) |
| Traefik (HTTPS) | `https://jellyseerr.example.internal`            |
| Config          | `/home/pi/docker/media/jellyseerr/config` → `/app/config` |
| Memory limit    | `jellyseerr_mem_limit` (256M)                    |
| PID limit       | `jellyseerr_pids_limit` (300)                    |

## Deploy

```bash
make jellyseerr
```

Jellyfin (the `media` role) must be up. On first run, `configure.yml` signs in
with `POST /api/v1/auth/jellyfin` against the Pi's LAN IP on port `8096`,
using `vault_jellyfin_username` / `vault_jellyfin_password`, and then calls
`/api/v1/settings/initialize`. The LAN IP is used because Jellyseerr and
Jellyfin are on separate Docker networks. Later runs skip this step once
`/api/v1/settings/public` reports `initialized`.

## Configuration

- `jellyseerr_port` (`5055`): host port, UFW-scoped to `lan_subnet`. The role also deletes the pre-2026-07-14 Anywhere rule.
- `jellyseerr_read_only` / `jellyseerr_tmpfs_size`: read-only root plus a 64m `/tmp`.
- The configure phase only runs when `vault_jellyseerr_email` is defined, even though it reads the Jellyfin credentials. It is skipped under `--check`.

## Operations

- Healthcheck: `wget --spider http://localhost:5055/api/v1/status`.
- `init: true` (tini) reaps the zombie `wget` children that `next-server`
  leaves from TMDB/IMDB image prefetches.
- The `JellyseerrErrorRateHigh` log-error alert lives in
  `roles/vmalert/templates/rules/container-error-rate.yml.j2`.
- Media stack context: [media stack design](../../../docs/design/003-media-stack.md).
