# Boot & Recovery Flow

How the homelab recovers from reboots, shutdowns, and power outages.

## TL;DR

All scenarios (reboot, shutdown+power-on, power outage) result in full automatic
recovery. No manual intervention needed.

## How It Works

### Container Restart Policy

Every Docker container uses `restart: unless-stopped`. This means:

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

```
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
   → Traefik starts → reverse proxy for *.homelab
8. Homelab Control API starts (systemd: homelab-control.service)
```

### DNS Chicken-and-Egg Problem

The Pi runs AdGuard as the LAN DNS server, but AdGuard itself runs in Docker.
During boot, Docker may need DNS before AdGuard is ready.

**Solution:** NetworkManager is configured with a DNS fallback chain:

```
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

## Systemd Service Dependencies

```
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
| DNS Server | `192.168.1.100` | Pi / AdGuard (primary, blocks ads) |
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

**Profile:** `814aa4` ("My First Experience")
**DNS Servers:** `45.90.28.75`, `45.90.30.75`
**Dashboard:** https://my.nextdns.io/814aa4/setup

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
