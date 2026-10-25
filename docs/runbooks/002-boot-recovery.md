# Boot & Recovery Flow

How the homelab recovers from reboots, shutdowns, and power outages.

## TL;DR

All scenarios (reboot, shutdown+power-on, power outage) result in full automatic
recovery. No manual intervention needed.

## How It Works

### Container Restart Policy

Every Docker container uses `restart: unless-stopped`. Containers that spawn child processes use `init: true` so Docker injects tini as PID 1 to properly reap zombie children. This means:

| Event | Containers auto-start? | Why |
|-------|----------------------|-----|
| `reboot` | Yes | systemd SIGTERM → Docker → containers "crashed" |
| `shutdown -h now` + power on | Yes | same as reboot |
| Power outage + power returns | Yes | containers were killed, not "stopped" |
| Manual `docker stop <name>` | **No** | intentionally stopped by user |

The key insight: `docker stop` marks a container as "explicitly stopped" and
`unless-stopped` respects that. Our shutdown/restart scripts intentionally avoid
`docker stop` so containers always auto-restart on next boot.

### Boot Sequence

```text
1. Kernel boots, systemd starts
2. Network comes online (NetworkManager, ethernet)
3. DNS resolves immediately:
   → 127.0.0.1 (AdGuard, not ready yet → fails)
   → 1.1.1.1  (Cloudflare, works immediately)
   → 9.9.9.9  (Quad9, backup)
4. Tailscale daemon starts (systemd: tailscaled.service)
   → Reconnects to tailnet, subnet routes re-advertised
5. Docker daemon starts (systemd: docker.service)
6. DOCKER-USER iptables rules applied (systemd: docker-user-iptables.service)
7. All containers auto-start (restart: unless-stopped)
   → AdGuard starts → DNS now served locally
   → Traefik starts → reverse proxy for *.example
8. Homelab Control API starts (systemd: homelab-control.service)
```

### DNS Chicken-and-Egg Problem

The Pi runs AdGuard as the LAN DNS server, but AdGuard itself runs in Docker.
During boot, Docker may need DNS before AdGuard is ready.

**Solution:** NetworkManager is configured with a DNS fallback chain:

```text
Primary:  127.0.0.1  (AdGuard — used once container is up)
Fallback: 1.1.1.1    (Cloudflare — used during boot)
Fallback: 9.9.9.9    (Quad9 — backup)
```

This is set via `ansible/roles/common/tasks/dns.yml` with `ipv4.ignore-auto-dns yes`
so DHCP-provided DNS is ignored.

## Failure Scenarios

### AdGuard container fails to start

- **Pi itself:** Still has internet via 1.1.1.1/9.9.9.9 fallback DNS
- **Other LAN devices:** Lose DNS if router only points to the Pi.
  Fix: set secondary DNS in router DHCP to NextDNS linked IP (see below)
- **Diagnosis:** `docker logs adguard`, `docker compose -f ~/docker/network/adguard/docker-compose.yml up -d`

### Docker daemon fails to start

- **All containers down.** Check: `sudo systemctl status docker`
- **Homelab Control API also down** (depends on docker.service)
- **DNS fallback still works** (NetworkManager, not Docker)
- **Fix:** `sudo systemctl restart docker` via SSH

### Control API fails to start

- **No web panel access.** Containers still running fine.
- **Check:** `sudo systemctl status homelab-control`
- **Fix:** `sudo systemctl restart homelab-control`

### Network/Ethernet fails

- **Everything Docker-related works locally** but no remote access
- **SSH unavailable** — need physical access (keyboard + monitor)

### Tailscale daemon fails to start

- **VPN access lost.** LAN access unaffected.
- **Subnet routing down** — tailnet devices can't reach LAN services
- **Check:** `sudo systemctl status tailscaled`
- **Fix:** `sudo systemctl restart tailscaled`

### Traefik container fails to start

- **Port 80/443 reverse proxy down.** Services still accessible via direct ports.
- **Check:** `docker logs traefik`, common cause is port conflict (8080 vs AdGuard)
- **Fix:** `cd ~/docker/network/traefik && docker compose up -d`

### SSD unmounted, corrupted, or replaced

The SSD holds `/srv` (Docker data root, all container bind mounts, and the media
library). If it dies, **every container loses its state**. Restart policies will
keep re-creating containers that fail to start because their volumes are gone.

**Symptoms**

- `ls /srv` is empty or returns I/O errors
- `docker ps` shows containers restarting in a loop
- `dmesg | tail` shows `blk_update_request: I/O error, dev sda` or similar
- `systemctl status srv.mount` (if mounted via fstab) is failed

**Recovery — soft (SSD unmounted but physically OK)**

```sh
# 1. Stop docker so it stops thrashing
sudo systemctl stop docker homelab-control

# 2. Re-mount /srv (check /etc/fstab entry)
sudo mount /srv
ls /srv   # should show docker/, media/, downloads/, backups/

# 3. Storage role will verify the hardlink invariant next run
cd ~/raspi-homelab && make base   # runs the /srv single-fs assertion

# 4. Start docker back up — restart: unless-stopped brings everything back
sudo systemctl start docker homelab-control
```

**Recovery — hard (SSD replaced / data lost)**

1. Plug in new SSD, partition + mkfs.ext4, update `/etc/fstab` UUID.
2. `sudo mount /srv`
3. `make base` — creates `/srv/{docker,media,downloads,backups}` skeleton.
4. Restore `/srv/backups/*` from off-site backup (if any).
5. `make adguard portainer media homepage watchtower homelab-control …` —
   compose files redeploy; containers pull fresh images.
6. Media library is gone: Sonarr/Radarr will re-request everything via
   Prowlarr + qBittorrent. Expect days of re-downloads.

**Prevention**

- Keep a recent `rsync` of `/srv/backups/` off the Pi (another machine, or S3).
- Don't store anything unique under `/srv/docker/` — container state can be
  re-created from the Ansible roles, but lost user data cannot.

### Tailscale network failure (vs daemon failure)

Daemon failure is covered above. This section covers **network-level** outages
where `tailscaled` is healthy but the tailnet is unreachable — e.g. ISP outage,
control-plane reachability issues, subnet route conflict.

**Symptoms**

- `tailscale status` shows peers but `tailscale ping <peer>` fails
- `curl https://login.tailscale.com/` from the Pi returns timeout
- LAN access works; remote access does not

**Recovery order of operations**

1. **Confirm the Pi has internet at all:** `curl -I https://1.1.1.1` — if this
   fails, the problem is upstream (router/ISP), not Tailscale.
2. **Bounce the interface:** `sudo tailscale down && sudo tailscale up` —
   re-authenticates against the control plane without a full daemon restart.
3. **Check subnet routing:** `sudo tailscale status --json | jq '.Self.AllowedIPs'`
   should include the LAN subnet advertisement.
4. **Fallback to LAN SSH:** if you're on-LAN, use `ssh pi@raspberrypi.local`
   (mDNS) or `ssh pi@192.0.2.100`.
5. **Accept peer key change:** on first reconnect after a long outage, Tailscale
   may rotate the node key. Re-approve on the admin console.

**If you're locked out remotely:** the Pi has no console-level lockout; an on-LAN
device can always SSH via the static LAN IP. Only full network outage blocks
both paths.

### Partial-start recovery

When only *some* containers come up after a boot or deploy — typical causes are
Docker daemon crashing mid-pull, DNS failing for `ghcr.io` during the image
fetch, or a volume permission mismatch after a role change.

**Diagnose**

```sh
# Show containers that should be running but aren't
docker ps -a --filter "status=exited" --filter "status=restarting"

# Group by compose project to see which role is partial
docker ps -a --format '{{.Label "com.docker.compose.project"}} {{.Status}}' | sort | uniq -c
```

**Recover**

```sh
# Re-run the affected role — compose is idempotent
make media          # or homepage / adguard / whichever is partial
```

If `make <role>` hangs on `Start X container`, the pull is likely stalled on
DNS. Check the DNS fallback chain is working:

```sh
resolvectl query ghcr.io      # should resolve via 127.0.0.1 or 1.1.1.1
sudo systemctl restart NetworkManager
```

If a single container keeps restarting, inspect its logs and volume
ownership — after the homepage UID change, for example, `chown -R 1000:1000`
on its config dir was required.

```sh
docker logs <container> --tail 100
stat /srv/docker/volumes/<name>  # uid/gid should match the container user
```

**Last resort — clean reset of a single stack:**

```sh
cd ~/docker/<stack> && docker compose down && docker compose up -d
```

Never `docker compose down -v` unless you intend to wipe the volume — it
deletes named volumes which may contain config you cannot regenerate.

## Systemd Service Dependencies

```text
multi-user.target
├── tailscaled.service (enabled)
├── docker.service (enabled)
│   ├── docker-user-iptables.service (After=docker, Requires=docker)
│   ├── homelab-control.service (After=docker, Requires=docker)
│   └── containers: adguard, traefik, jellyfin, sonarr, radarr, ...
└── NetworkManager (DNS fallback configured, ethernet)
```

## Router DNS Configuration

The router (TP-LINK EX20v) DHCP settings should be:

| Setting | Value | Why |
|---------|-------|-----|
| DNS Server | `192.0.2.100` | Pi / AdGuard (primary, blocks ads) |
| Secondary DNS | `45.90.28.75` | NextDNS linked IP (fallback, also blocks ads) |

**Why NextDNS instead of 1.1.1.1?**
Using a plain DNS like 1.1.1.1 as secondary means devices bypass ad blocking
whenever AdGuard is slow or down. NextDNS also filters ads/trackers, so ad
blocking continues even during Pi reboots.

**Setup:** Link your home IP in NextDNS dashboard (my.nextdns.io → Linked IP →
Link IP). Home IPs rarely change but if yours does, re-link it.

### NextDNS Linked IP

NextDNS uses your public IP to identify your network when using plain DNS
(instead of DNS-over-HTTPS/TLS). The router can only do plain DNS, so linked IP
is the only option.

**Profile:** `abc123` ("My First Experience")
**DNS Servers:** `45.90.28.75`, `45.90.30.75`
**Dashboard:** <https://my.nextdns.io/abc123/setup>

**Maintenance:**

- If your ISP changes your public IP, NextDNS stops recognizing your traffic
  and falls back to unfiltered DNS. Re-link at my.nextdns.io → Linked IP.
- Turkish ISPs (CGNAT) may share IPs across customers. If ad blocking seems
  inconsistent, this could be the cause — NextDNS linked IP works best with
  a static or semi-static public IP.
- NextDNS free tier allows 300,000 queries/month. After that, queries resolve
  but without filtering. Monitor usage at my.nextdns.io → Analytics.

## Testing Recovery

To verify everything comes back after power loss:

1. Open the control panel in a browser
2. Physically unplug the Pi's power
3. Wait 10 seconds, plug it back in
4. Pi should boot in ~60-90 seconds
5. All services should be accessible
6. Control panel should show "Online" status

Alternatively, use the control panel's Restart button and watch the
auto-reconnect polling — it will show when the system is back.
