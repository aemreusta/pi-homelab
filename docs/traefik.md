# Traefik Reverse Proxy

Traefik v3 reverse proxy for `*.homelab` domains, running as a Docker container
with host network mode.

## Overview

Traefik routes HTTP requests based on `Host` header to backend services running
on localhost ports. This replaces the need to remember port numbers — instead of
`192.168.1.100:8096`, use `jellyfin.homelab`.

**Existing port-based access is preserved.** Traefik is additive — all services
still respond on their original ports.

## Architecture

### LAN Access (HTTP)

```
Browser → jellyfin.homelab
  → AdGuard DNS → 192.168.1.100
  → Traefik :80 → Host: jellyfin.homelab → 127.0.0.1:8096
```

### Tailscale Access (HTTP via Split DNS)

```
Browser (tailnet) → jellyfin.homelab
  → Tailscale Split DNS → 100.90.59.36
  → Traefik :80 → Host: jellyfin.homelab → 127.0.0.1:8096
```

### Tailscale HTTPS (When Enabled)

```
Browser (tailnet) → https://jellyfin.homelab
  → Tailscale Split DNS → 100.90.59.36
  → Traefik :443 + TLS cert → 127.0.0.1:8096
```

Requires `vault_tailscale_fqdn` to be set and HTTPS enabled in Tailscale
admin console. See [tailscale.md](tailscale.md) for setup.

## Service Routing

All services are configured in `ansible/roles/traefik/defaults/main.yml`:

| Domain | Backend | Port |
|--------|---------|------|
| jellyfin.homelab | http://127.0.0.1:8096 | 8096 |
| sonarr.homelab | http://127.0.0.1:8989 | 8989 |
| radarr.homelab | http://127.0.0.1:7878 | 7878 |
| prowlarr.homelab | http://127.0.0.1:9696 | 9696 |
| qbittorrent.homelab | http://127.0.0.1:8181 | 8181 |
| homepage.homelab | http://127.0.0.1:3001 | 3001 |
| glances.homelab | http://127.0.0.1:61208 | 61208 |
| filebrowser.homelab | http://127.0.0.1:8082 | 8082 |
| adguard.homelab | http://127.0.0.1:8080 | 8080 |
| portainer.homelab | http://127.0.0.1:9000 | 9000 |
| control.homelab | http://127.0.0.1:9099 | 9099 |
| traefik.homelab:8180 | api@internal | 8180 |

> **Portainer** is proxied via HTTP on port 9000. Both ports 9000 and 9443 are
> exposed by the container, but Traefik routes to the HTTP port to avoid TLS
> complexity (no `serversTransport` or `insecureSkipVerify` needed).

## Installation

```bash
make traefik
```

This runs `ansible/playbooks/traefik.yml` which:
1. Creates config directories under `~/docker/network/traefik/`
2. Templates static config (`traefik.yml`), dynamic config (`dynamic.yml`), and compose file
3. Starts the Traefik container with `network_mode: host`
4. Optionally generates TLS certs via `tailscale cert` (if FQDN is set)
5. Opens UFW ports (80, 443, 8180)

## Design Decisions

### Host Network Mode

Traefik uses `network_mode: host` instead of bridged networking. This means:
- Traefik binds directly to host ports 80, 443, 8180
- Backend services are reached via `127.0.0.1:<port>`
- No Docker network configuration needed
- **No changes to existing service compose files** — this was the key reason

### File Provider (Not Docker Labels)

Traefik uses a **file provider** (`dynamic.yml`) instead of Docker labels because:
- All routing rules are centralized in one template
- Existing roles don't need modification
- No Docker socket mount required (more secure)
- Adding a new service = editing one defaults file + redeploying

### Pinned Version

`traefik:v3.3` — pinned to avoid major version breaking changes. Update
deliberately by changing `traefik_image` in defaults.

## Configuration Files

On the Pi after deployment:

```
~/docker/network/traefik/
├── docker-compose.yml       # container definition
└── config/
    ├── traefik.yml          # static config (entrypoints, providers, API)
    └── dynamic.yml          # routing rules (routers, services, middlewares)
```

### Static Config (traefik.yml)

Defines entrypoints and enables the file provider:
- `web` — `:80` (HTTP)
- `websecure` — `:443` (HTTPS, used with Tailscale certs)
- `traefik` — `:8180` (Dashboard, `api.insecure: true`)

### Dynamic Config (dynamic.yml)

Generated from the `traefik_services` list in defaults. Each service gets:
- An HTTP router with `Host()` rule on the `web` entrypoint
- A load balancer service pointing to `127.0.0.1:<port>`
- (If FQDN set) An HTTPS router on the `websecure` entrypoint

> **Traefik v3.3 note:** The `tls` block in `dynamic.yml` must be at the
> top level, not nested under `http`. This changed in Traefik v3.3.

## Adding a New Service

1. Add to `traefik_services` in `ansible/roles/traefik/defaults/main.yml`:
   ```yaml
   - name: newservice
     port: 1234
   ```
2. Deploy: `make traefik`

> DNS is handled automatically. AdGuard uses a single `*.homelab` wildcard
> rewrite pointing to the Pi IP, so no per-service DNS entries are needed.

## Dashboard

Access at `http://traefik.homelab:8180` (or `http://192.168.1.100:8180`).

The dashboard shows:
- All routers and their rules
- Backend service health
- Active middlewares
- Provider status

API endpoint: `http://traefik.homelab:8180/api/overview`

## Port Allocation

| Port | Service | Protocol |
|------|---------|----------|
| 80 | Traefik HTTP | TCP |
| 443 | Traefik HTTPS | TCP |
| 8180 | Traefik Dashboard | TCP |

> Port 8080 is used by AdGuard. Traefik's default dashboard port (8080)
> was changed to 8180 to avoid conflict.

## Verification

```bash
# Container status
docker ps --filter name=traefik

# Dashboard API
curl -s http://traefik.homelab:8180/api/overview | python3 -m json.tool

# Test routing (from Pi)
curl -s -H "Host: jellyfin.homelab" http://127.0.0.1 -o /dev/null -w "%{http_code}"

# Test all services
for svc in jellyfin sonarr radarr prowlarr qbittorrent homepage glances \
           filebrowser adguard portainer control; do
  code=$(curl -s -H "Host: ${svc}.homelab" http://127.0.0.1 -o /dev/null \
    -w "%{http_code}" --max-time 3)
  echo "${svc}: ${code}"
done
```

## Troubleshooting

```bash
# Container logs
docker logs traefik --tail 50

# Check config for errors
docker logs traefik 2>&1 | grep -i error

# Restart
cd ~/docker/network/traefik && docker compose restart

# Common issues:
# - "bind: address already in use" → port conflict (check 80, 443, 8180)
# - "500 Internal Server Error: TLS certs" → enable HTTPS in Tailscale admin
# - "404 Not Found" → check Host header matches a router rule
# - "502 Bad Gateway" → backend service is down
```
