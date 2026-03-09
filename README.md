# Raspberry Pi Homelab

Production-grade home infrastructure on Raspberry Pi 4B (1.8TB SSD), managed with Ansible and Docker.

## Infra Topology

```
┌──────────────────────────┐
│  MacBook (control node)  │
│  - Ansible               │
│  - pre-commit            │
└────────────┬─────────────┘
             │ SSH
             ▼
┌──────────────────────────┐
│  Raspberry Pi 4B         │
│  - Debian Trixie/13      │
│  - SSD root (1.8TB)      │
│  - SD card (boot only)   │
│  - Docker + Tailscale    │
│  - 192.168.1.100 (LAN)   │
│  - 100.90.59.36 (Tailnet)│
└──────────────────────────┘
```

## Services

| Service | Port | Domain | Description |
|---------|------|--------|-------------|
| **Traefik** | 80, 443, 8180 | traefik.homelab | Reverse proxy for all services |
| **AdGuard Home** | 53, 8080 | adguard.homelab | DNS + ad blocking + local domains |
| **Tailscale** | 41641/udp | — | VPN mesh + subnet router |
| **Jellyfin** | 8096 | jellyfin.homelab | Media server (direct play only) |
| **Sonarr** | 8989 | sonarr.homelab | TV series automation |
| **Radarr** | 7878 | radarr.homelab | Movie automation |
| **Prowlarr** | 9696 | prowlarr.homelab | Indexer management |
| **qBittorrent** | 8181 | qbittorrent.homelab | Torrent client |
| **Portainer** | 9000, 9443 | portainer.homelab | Container management |
| **Homepage** | 3001 | homepage.homelab | Dashboard |
| **Glances** | 61208 | glances.homelab | System monitoring |
| **FileBrowser** | 8082 | filebrowser.homelab | File manager |
| **Control API** | 9099 | control.homelab | Shutdown/restart/update |
| **Maintainerr** | 6246 | maintainerr.homelab | Auto-delete watched media |
| **Jellyseerr** | 5055 | jellyseerr.homelab | Netflix-style media requests |
| **Watchtower** | — | — | Auto-update containers |

All services are accessible via `*.homelab` domains (port 80) through Traefik, or directly via their ports.

## Network Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Raspberry Pi                      │
│                                                      │
│  Tailscale (host) ◄── WireGuard ── Tailnet devices  │
│       │                                              │
│       ▼                                              │
│  Traefik :80/:443 ◄── *.homelab ── LAN devices      │
│       │                                              │
│       ├── jellyfin.homelab  → 127.0.0.1:8096        │
│       ├── sonarr.homelab    → 127.0.0.1:8989        │
│       ├── radarr.homelab    → 127.0.0.1:7878        │
│       └── ...               → 127.0.0.1:<port>      │
│                                                      │
│  AdGuard Home :53 ◄── DNS ── All LAN devices        │
│       └── *.homelab → 192.168.1.100                  │
└─────────────────────────────────────────────────────┘
```

**LAN access:** AdGuard DNS resolves `*.homelab` → Pi IP → Traefik routes by Host header.
**Remote access:** Tailscale split DNS resolves `*.homelab` → Tailscale IP → same Traefik routing.

## Filesystem Layout

```
/                              ext4, 1.8TB SSD
├── /srv
│   ├── docker/                Docker data root + container configs
│   ├── media/
│   │   ├── movies/            movie library (HEVC direct play)
│   │   └── series/            series library
│   ├── downloads/
│   │   ├── incomplete/        active downloads
│   │   └── complete/          finished, ready for import
│   └── backups/               restic snapshots
├── /etc                       system configs
└── /boot/firmware             SD card (boot only)
```

## Quick Start

```bash
# Clone and setup
git clone https://github.com/emrekasg/pi-homelab && cd pi-homelab
pre-commit install && pre-commit install --hook-type commit-msg
make requirements

# Deploy
make ping          # test connectivity
make check         # dry-run (no changes)
make base          # full provisioning

# Deploy services
make tailscale     # VPN (host-level, do this first)
make adguard       # DNS
make traefik       # reverse proxy
make media         # Jellyfin, Sonarr, Radarr, qBittorrent
make portainer     # container management
make homepage      # dashboard
make glances       # monitoring
```

## Make Targets

| Command | Description |
|---------|-------------|
| `make help` | Show all targets |
| `make ping` | Test Pi connectivity |
| `make base` | Full base provisioning |
| `make check` | Dry-run with diff |
| `make tailscale` | Deploy Tailscale VPN |
| `make traefik` | Deploy Traefik reverse proxy |
| `make adguard` | Deploy AdGuard Home DNS |
| `make portainer` | Deploy Portainer |
| `make media` | Deploy media stack |
| `make homepage` | Deploy Homepage dashboard |
| `make glances` | Deploy Glances monitoring |
| `make filebrowser` | Deploy FileBrowser |
| `make homelab-control` | Deploy Control API |
| `make maintainerr` | Deploy Maintainerr |
| `make jellyseerr` | Deploy Jellyseerr |
| `make watchtower` | Deploy Watchtower |
| `make lint` | Run ansible-lint |
| `make requirements` | Install Ansible collections |

## Ansible Roles

### Base Roles (run via `make base`)

| Role | Tasks |
|------|-------|
| `common` | apt packages, timezone, sysctl, DNS fallback, WiFi disable |
| `security` | SSH hardening, UFW, fail2ban |
| `storage` | /srv directory layout |
| `docker` | Docker CE, compose, data root, DOCKER-USER iptables |

### Service Roles (run independently)

| Role | Tasks |
|------|-------|
| `tailscale` | Host-level VPN, subnet router (192.168.1.0/24) |
| `traefik` | Reverse proxy, *.homelab routing, TLS via Tailscale |
| `adguard` | DNS server, ad blocking, `*.homelab` wildcard rewrite |
| `portainer` | Container management UI |
| `media` | Jellyfin, Sonarr, Radarr, Prowlarr, qBittorrent |
| `homepage` | Dashboard with service widgets |
| `glances` | System + container monitoring |
| `filebrowser` | Web file manager |
| `homelab_control` | Shutdown/restart/update API |
| `maintainerr` | Auto-delete watched media via Jellyfin |
| `jellyseerr` | Netflix-style media request system |
| `watchtower` | Auto-update containers |

## Inter-Service Connections

Services run in **isolated Docker networks** — they cannot reach each other by container name across different compose stacks. Use the Pi's IP (`192.168.1.100`) or `127.0.0.1` (for services on host network) when configuring service-to-service connections.

| From | To | Host | Port |
|------|----|------|------|
| Sonarr/Radarr | qBittorrent | 192.168.1.100 | 8181 |
| Sonarr/Radarr | Prowlarr | 192.168.1.100 | 9696 |
| Maintainerr | Jellyfin | 192.168.1.100 | 8096 |
| Maintainerr | Sonarr | 192.168.1.100 | 8989 |
| Maintainerr | Radarr | 192.168.1.100 | 7878 |
| Jellyseerr | Jellyfin | 192.168.1.100 | 8096 |
| Jellyseerr | Sonarr | 192.168.1.100 | 8989 |
| Jellyseerr | Radarr | 192.168.1.100 | 7878 |

> **Note:** Services within the same compose stack (e.g. Sonarr and Radarr in `media`) can use container names directly (e.g. `sonarr`, `radarr`). Cross-stack communication requires the host IP.

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | Mermaid diagrams: infra, services, network, storage, firewall |
| [Traefik](docs/traefik.md) | Reverse proxy setup, routing rules, adding services |
| [Tailscale](docs/tailscale.md) | VPN setup, subnet routing, split DNS, admin console |
| [Media Stack](docs/media-stack.md) | Hardlink model, volume mapping, playback strategy |
| [Boot & Recovery](docs/boot-recovery.md) | Boot sequence, failure scenarios, DNS fallback |

## Commit Convention

[Conventional Commits](https://www.conventionalcommits.org/) enforced via pre-commit hook.

Allowed prefixes: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`.
