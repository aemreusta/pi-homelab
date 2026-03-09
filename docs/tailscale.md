# Tailscale VPN

Host-level Tailscale installation acting as a subnet router for the entire LAN.

## Overview

Tailscale is installed directly on the host (not in Docker) because subnet routing
requires access to the host network stack. It advertises `192.168.1.0/24` so all
LAN services are accessible from any device on the tailnet.

## Architecture

```
Tailnet Device (laptop, phone, etc.)
        │
        │  WireGuard tunnel (UDP 41641)
        ▼
┌─────────────────────────┐
│  Raspberry Pi           │
│  Tailscale (host-level) │
│  IP: 100.90.59.36       │
│                         │
│  Subnet Router:         │
│  192.168.1.0/24         │
│                         │
│  FQDN:                  │
│  homelab.tail117bf.ts.net│
└─────────────────────────┘
```

From any tailnet device, you can access:
- `192.168.1.100:8096` — Jellyfin directly
- `jellyfin.homelab` — via Traefik (requires split DNS)

## Installation

Tailscale is installed via apt from the `bookworm` repository (Trixie is not
yet supported — same workaround used for Docker). The binary is statically
linked and works across Debian versions.

```bash
make tailscale
```

This runs `ansible/playbooks/tailscale.yml` which:
1. Adds Tailscale apt repository + signing key
2. Installs the `tailscale` package
3. Enables and starts `tailscaled` systemd service
4. Authenticates with `tailscale up` using an auth key from vault
5. Configures subnet routing (192.168.1.0/24) and IPv6 forwarding
6. Opens UFW ports (41641/udp + tailscale0 interface)

## Configuration

### Ansible Variables

Defined in `ansible/roles/tailscale/defaults/main.yml`:

| Variable | Default | Description |
|----------|---------|-------------|
| `tailscale_auth_key` | from vault | Reusable auth key |
| `tailscale_advertise_routes` | `192.168.1.0/24` | Subnets to advertise |
| `tailscale_exit_node` | `false` | Act as exit node |
| `tailscale_accept_dns` | `true` | Accept MagicDNS |
| `tailscale_hostname` | `homelab` | Machine name on tailnet |

### Vault Secrets

In `ansible/inventory/group_vars/all/vault.yml`:

```yaml
vault_tailscale_auth_key: "tskey-auth-..."  # from admin console
vault_tailscale_fqdn: "homelab.tail117bf.ts.net"  # or empty to skip TLS
```

### Auth Key

Create a **reusable** auth key at https://login.tailscale.com/admin/settings/keys.
The key is used by Ansible for non-interactive authentication. Single-use keys
would require a new key for every `make tailscale` run.

## Admin Console Setup

After `make tailscale`, three manual steps are needed in the Tailscale admin
console (https://login.tailscale.com/admin):

### 1. Approve Subnet Routes

**Machines → homelab → Route settings** → Enable `192.168.1.0/24`.

Without this, the Pi advertises the route but tailnet devices can't use it.

### 2. Enable HTTPS (Optional)

**DNS → HTTPS Certificates** → Enable.

This allows `tailscale cert` to generate TLS certificates signed by
Let's Encrypt via Tailscale's ACME integration. Required for HTTPS access
to services via Traefik.

### 3. Split DNS

**DNS → Nameservers → Add Split DNS**:
- Domain: `homelab`
- Nameserver: `100.90.59.36` (Pi's Tailscale IP)

This routes `*.homelab` DNS queries from tailnet devices to AdGuard on the Pi,
so `jellyfin.homelab` resolves correctly from anywhere on the tailnet.

## Firewall Rules

| Port/Interface | Protocol | Rule | Comment |
|----------------|----------|------|---------|
| 41641 | UDP | allow | WireGuard tunnel |
| tailscale0 | any | allow in | Traffic from tailnet |

The DOCKER-USER iptables chain also allows `100.64.0.0/10` (Tailscale CGNAT
range) so tailnet devices can reach Docker containers.

## Verification

```bash
# Check Tailscale status
ssh homelab.local 'tailscale status'

# Check subnet routes
ssh homelab.local 'tailscale status --json | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(\"State:\", d[\"BackendState\"])
print(\"IPs:\", d[\"Self\"][\"TailscaleIPs\"])
print(\"Routes:\", d[\"Self\"].get(\"PrimaryRoutes\"))
"'

# Test from a tailnet device
ping 192.168.1.100          # via subnet route
curl http://jellyfin.homelab:8096  # via split DNS
```

## Troubleshooting

```bash
# Service status
sudo systemctl status tailscaled

# Restart
sudo systemctl restart tailscaled

# Re-authenticate (if auth key expired)
sudo tailscale up --authkey=NEW_KEY --hostname=homelab \
  --advertise-routes=192.168.1.0/24 --accept-routes --accept-dns

# Check if subnet routes are approved
tailscale status --json | python3 -c "
import sys, json; d=json.load(sys.stdin)
print(d['Self'].get('PrimaryRoutes', 'NOT APPROVED'))
"

# Logs
sudo journalctl -u tailscaled -f
```

## Idempotency

The Ansible role checks `tailscale status --json` before running `tailscale up`.
If BackendState is already "Running", the authentication step is skipped. This
makes `make tailscale` safe to run repeatedly.
