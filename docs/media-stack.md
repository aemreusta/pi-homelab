# Media Stack

Phase 7 — qBittorrent, Sonarr, Radarr, Jellyfin on Raspberry Pi 4B.

## Volume Mapping (Hardlink Model)

All download/import services share a **single mount point** so hardlinks
work between `/downloads` and `/media` (same filesystem, same mount).

```
Host                    Container
─────────────────────   ──────────────
/srv                →   /data            (qBittorrent, Sonarr, Radarr)
/srv/media          →   /data/media:ro   (Jellyfin — read-only)
```

### Host directory layout

```
/srv/
├── backups/
├── docker/              # Docker data-root
├── downloads/
│   ├── complete/        # qBittorrent moves finished files here
│   └── incomplete/      # qBittorrent active downloads
└── media/
    ├── movies/          # Radarr hardlinks here
    └── series/          # Sonarr hardlinks here
```

### Why single mount matters

Separate mounts (`/downloads:/downloads` + `/media:/media`) would cause
`EXDEV: cross-device link` errors because hardlinks cannot span mount
boundaries — even if the underlying host filesystem is the same.

With `/srv:/data`, the container sees both paths under one mount:
- `/data/downloads/complete/Movie.mkv` (source)
- `/data/media/movies/Movie (2024)/Movie.mkv` (hardlink)

Same inode, zero disk copy.

## Workflow

```
Manual torrent add (browser/magnet)
        │
        ▼
┌──────────────┐
│  qBittorrent │  Downloads to /data/downloads/incomplete
│  :8181       │  Moves completed to /data/downloads/complete
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Sonarr/Radarr│  Monitors /data/downloads/complete
│ :8989 / :7878│  Renames + hardlinks to /data/media/{movies,series}
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   Jellyfin   │  Scans /data/media (read-only mount)
│   :8096      │  Serves to LAN clients via direct play
└──────────────┘
```

No Jackett/Prowlarr — torrents are added manually from torrent sites.

## Container Ports

| Service      | Host Port | Container Port | Protocol | Domain                 |
|-------------|-----------|----------------|----------|------------------------|
| qBittorrent | 8181      | 8181           | TCP      | qbittorrent.homelab    |
| qBittorrent | 6881      | 6881           | TCP+UDP  | —                      |
| Sonarr      | 8989      | 8989           | TCP      | sonarr.homelab         |
| Radarr      | 7878      | 7878           | TCP      | radarr.homelab         |
| Jellyfin    | 8096      | 8096           | TCP      | jellyfin.homelab       |

> Port 8080 is reserved by AdGuard Home dashboard.
> qBittorrent WebUI moved to 8181 to avoid conflict.

## Docker Network

All media services run on `media_media_net` (bridge mode).

```
media_media_net (172.20.0.0/16)
├── qbittorrent  172.20.0.2
├── sonarr        172.20.0.3
├── radarr        172.20.0.4
└── jellyfin      172.20.0.5
```

Services reference each other by container name within the network
(e.g., Sonarr connects to qBittorrent at `qbittorrent:8181`).

## Playback Strategy

**Direct play only** — no hardware transcoding on Pi 4B.

| Client           | Codec Support         | Notes                    |
|------------------|-----------------------|--------------------------|
| Samsung TV       | HEVC/H.265, H.264    | Native direct play       |
| Windows PC       | Everything            | MPC-HC / mpv             |
| MacBook          | HEVC/H.264            | Safari or Jellyfin app   |
| Android phone    | HEVC/H.264            | Jellyfin app             |

If a file needs transcoding, Jellyfin will attempt software decode which
will be too slow on the Pi's Cortex-A72. Solution: re-encode the source
file to HEVC/H.265 before importing, or use clients that support the
original codec.

## PUID/PGID

All containers run as `PUID=1000 PGID=1000` matching host user `emre`.
This ensures correct file ownership for hardlinks and media scanning.

```bash
# Verify on host
id emre
# uid=1000(emre) gid=1000(emre)
```

## Sonarr/Radarr Configuration

After deployment, configure via WebUI:

### Download Client (both Sonarr and Radarr)
- **Settings → Download Clients → Add → qBittorrent**
- Host: `qbittorrent`
- Port: `8181`
- Category: `sonarr` or `radarr`

### Root Folders
- Sonarr: `/data/media/series`
- Radarr: `/data/media/movies`

### Import Settings
- **Use hardlinks instead of copy**: Yes (default when same mount)
- Remove completed downloads: personal preference

## Jellyfin Library Setup

During the setup wizard:
- Server name: `jellyfin`
- Add library → Movies → `/data/media/movies`
- Add library → Shows → `/data/media/series`
- Enable: Allow remote connections to this server
- Disable: Allow automatic port mapping (not needed behind firewall)

## Troubleshooting

```bash
# Check all media containers
docker ps --filter "network=media_media_net"

# View logs
docker logs qbittorrent --tail 50
docker logs sonarr --tail 50
docker logs radarr --tail 50
docker logs jellyfin --tail 50

# Verify hardlink works
touch /srv/downloads/complete/test.txt
ln /srv/downloads/complete/test.txt /srv/media/movies/test.txt
stat /srv/downloads/complete/test.txt /srv/media/movies/test.txt
# Both should show same inode number
rm /srv/downloads/complete/test.txt /srv/media/movies/test.txt

# Check filesystem (must be same device for hardlinks)
stat -c "Device: %d" /srv/downloads /srv/media
```

## Ansible

```bash
# Deploy/update media stack
make media

# Or directly
cd ansible && ansible-playbook playbooks/media.yml
```

Role: `ansible/roles/media/`
Compose: `ansible/roles/media/templates/docker-compose.yml.j2`
