# tailscale_dns

This role runs a dnsmasq resolver on the Pi's Tailscale address. For remote
tailnet clients it answers every `homelab_internal_domains` name with the Pi's
Tailscale IP instead of the LAN IP that AdGuard returns. It also installs an
hourly check for drift in the tailnet's split-DNS routes.

## What it deploys

- A `4km3/dnsmasq:latest` container named `tailscale-dns`, with `init: true`. It
  has no `ufw.yml`, because the `tailscale` role's `tailscale0` interface rule
  already lets tailnet traffic in.
- `tailscale-dns-resume.service`: a systemd oneshot that waits up to 120 s for
  `tailscale0` to get an IPv4 address and then runs `docker start tailscale-dns`.
  Without it the container loses the boot race with `tailscale0`, its port bind
  fails, and it exits.
- `/usr/local/bin/tailscale-split-dns-check.sh` plus the tested decider
  `tailscale_split_dns.py`, run hourly from root's cron.

| Item        | Value                                                       |
|-------------|-------------------------------------------------------------|
| DNS         | `<tailscale ip -4>:53` tcp+udp, bound to the Tailscale IP only |
| Config      | `/home/<docker_user>/docker/network/tailscale-dns/config/dnsmasq.conf` |
| Upstreams   | `tailscale_dns_upstream` (`1.1.1.1`, `9.9.9.9`)             |
| Memory      | `tailscale_dns_mem_limit` (`128M`)                          |
| Drift log   | `/var/log/tailscale-split-dns-check.log` (rotated weekly)   |

## Deploy

```bash
make tailscale       # prerequisite: the Pi must hold an authenticated 100.x address
make tailscale-dns
```

The deploy reads `tailscale ip -4` and fails unless the address starts with `100.`.

## Configuration

- `homelab_internal_domains` (group_vars): each entry becomes an
  `address=/<domain>/<tailscale ip>` line. `tailscale_dns_local_domain` is only a
  fallback for when that var is undefined.
- `tailscale_dns_cap_add`: `KILL` is required. Without it tini cannot forward
  SIGTERM to dnsmasq after dnsmasq drops privileges. Restarts then leave the
  container `exited(1)`, and `unless-stopped` does not recover it. See
  [docs/incidents/2026-09-09-tailscale-dns-capkill-unrecoverable-restart.md](../../../docs/incidents/2026-09-09-tailscale-dns-capkill-unrecoverable-restart.md).
- `tailscale_dns_split_check_enabled` / `_cron_minute`: turn the drift check on
  or off and set its schedule (minute 17, every hour).

## Operations

- **Healthcheck:** `nslookup -timeout=2 google.com 127.0.0.1`.
- **Split-DNS drift:** split-DNS routes live in the Tailscale control plane, and
  Ansible cannot converge them. The check only detects drift. It writes
  `tailscale_split_dns.prom`, and when a route has drifted it logs a ready-made
  `PATCH /api/v2/tailnet/-/dns/split-dns` body for a human to apply. Run the
  check by hand with `sudo tailscale-split-dns-check.sh -v`.
- **Alerts** (in `roles/vmalert/templates/rules/tailscale-split-dns.yml.j2`):
  `TailscaleSplitDnsDrift`, `TailscaleSplitDnsCheckStale`, `TailscaleSplitDnsScrapeFailed`.
- **Check mode:** the `tailscale ip -4` probe and the metric seed task use
  `check_mode: false`. Without that, the CD's `--check --diff` phase fails. See
  [docs/operations/ops-discipline.md](../../../docs/operations/ops-discipline.md) §6.
- Guide: [docs/guides/005-tailscale.md](../../../docs/guides/005-tailscale.md).
