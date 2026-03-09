# Project Index: pi-homelab

Generated: 2026-03-08

## Project Structure

```
pi-homelab/
├── Makefile                          # All commands (make base, make media, etc.)
├── .ansible-lint                     # Linter config (offline mode)
├── ansible/
│   ├── ansible.cfg                   # Ansible configuration
│   ├── requirements.yml              # Galaxy collections (community.general, community.docker, ansible.posix)
│   ├── inventory/
│   │   ├── hosts.ini                 # Target: homelab.local, user: emre
│   │   └── group_vars/all/
│   │       ├── main.yml              # Global vars (timezone, packages, sysctl)
│   │       └── vault.yml             # Encrypted secrets
│   ├── playbooks/                    # One playbook per service + base.yml
│   └── roles/                        # 14 roles (see below)
├── docker/                           # Static compose files (legacy/reference)
├── docs/                             # Architecture, media-stack, boot-recovery docs
└── tests/
    └── test_control_api.py           # Pytest tests for homelab control API
```

## Roles Overview

### Base Roles (run in order via `make base`)

| Role | Purpose | Key Config |
|------|---------|------------|
| **common** | Packages, timezone (Europe/Istanbul), sysctl, WiFi/ethernet | No defaults |
| **security** | SSH hardening, UFW firewall, fail2ban (1h ban, 5 retries) | Port 22 |
| **storage** | Creates /srv directory tree for SSD | Owner: emre:emre |
| **docker** | Docker CE install, daemon config, DOCKER-USER iptables | Data root: /srv/docker |

### Service Roles (deployed independently)

| Role | Service(s) | Port(s) | Compose Dir |
|------|-----------|---------|-------------|
| **adguard** | AdGuard Home (DNS) | 53, 80, 443, 3000 | ~/docker/network/adguard |
| **portainer** | Portainer CE | 9000, 9443 | ~/docker/core/portainer |
| **media** | Jellyfin, Sonarr, Radarr, Prowlarr, qBittorrent | 8096, 8989, 7878, 9696, 8080 | ~/docker/media |
| **watchtower** | Watchtower (auto-update) | none | ~/docker/core/watchtower |
| **homepage** | Homepage dashboard | 3001 | ~/docker/core/homepage |
| **glances** | Glances (system monitor) | 61208 | ~/docker/core/glances |
| **filebrowser** | FileBrowser | 8085 | ~/docker/core/filebrowser |
| **homelab_control** | Control API (systemd, not Docker) | 9099 | /opt/homelab/control |
| **maintainerr** | Maintainerr | TBD | ~/docker/media/maintainerr |
| **tailscale** | Tailscale VPN | N/A | N/A |

## Storage Layout (SSD at /srv)

```
/srv/
├── docker/              # Docker data root
├── media/movies/        # Radarr hardlinks here
├── media/series/        # Sonarr hardlinks here
├── downloads/           # qBittorrent (incomplete + complete)
└── backups/
```

All containers mount `/srv:/data` for hardlink support (single filesystem).

## Role File Pattern

Each role follows: `defaults/ → tasks/main.yml → tasks/deploy.yml → tasks/ufw.yml → templates/ → handlers/`

## Templates (27 total)

Key templates:
- `*/templates/docker-compose.yml.j2` — Docker Compose for each service
- `docker/templates/daemon.json.j2` — Docker daemon config
- `docker/templates/docker-user-iptables.j2` — Firewall rules for Docker
- `security/templates/jail.local.j2` — Fail2ban config
- `adguard/templates/AdGuardHome.yaml.j2` — AdGuard config with DNS rewrites
- `homepage/templates/{services,settings,widgets,bookmarks,docker}.yaml.j2` — Dashboard config
- `homelab_control/templates/control-api.py.j2` — Python HTTP API for shutdown/restart

## DNS Rewrites (AdGuard)

All services accessible via `*.homelab` local domain (e.g., `jellyfin.homelab`, `sonarr.homelab`).
AdGuard uses a single `*.homelab` wildcard rewrite pointing to the Pi IP (`192.168.1.100`),
replacing the previous per-service individual rewrites. The `configure.yml` task also cleans up
any stale rewrites with incorrect IPs before adding new ones.

## Commands

```bash
make ping              # Test connectivity
make base              # Provision base (common → security → storage → docker)
make check             # Dry-run base
make lint              # ansible-lint
make requirements      # Install Galaxy collections
make <service>         # Deploy individual service
python3 -m pytest tests/test_control_api.py -v  # Test control API
```

## Key Constraints

- PUID/PGID 1000 for all containers (user: emre)
- No transcoding (Jellyfin direct play only)
- restart: unless-stopped (never use `docker stop`)
- Trusted networks: 192.168.0.0/16 + 100.64.0.0/10 (Tailscale)
- Conventional commits enforced via pre-commit hook
