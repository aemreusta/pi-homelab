# 046 — 2026-07-08: macOS — reaching the homelab while on a corporate VPN (`*.example` names go dark)

> **Current use:** configure `/etc/resolver/example.internal` with
> `nameserver 192.0.2.100` and verify a current hostname such as
> `jellyfin.example.internal` with the VPN on. The two old resolver files and
> domains in this incident record were retired on 2026-07-15.
> [Current network architecture](../design/001-architecture.md#infrastructure-and-network).

**Status:** resolved 2026-07-08. On the Mac, connecting the **company VPN** made
the homelab unreachable **by name** — `https://jellyfin.example`,
`grafana.example`, every `*.example` / `*.example.org` host stopped resolving.
Root cause was **DNS, not routing**: the corp VPN is *split-tunnel* (LAN route to
the Pi stays intact) but it installs its own resolvers as **primary**, and those
resolvers don't know `.example` and don't forward to AdGuard.

> Mac-local resolver gap, **not** a repo / AdGuard / Traefik bug. AdGuard on the
> Pi answers `*.example` correctly the whole time (`dig @192.0.2.100
> jellyfin.example` → `192.0.2.100`); the Mac just never *asks* it once the VPN
> owns the default resolver. Fix is two macOS scoped-resolver files. See DNS
> architecture in [design 038](../archive/design/038-homelab-io-domain-migration.md).

## Quick navigation

| § | What | One-line |
|---|---|---|
| 1 | Symptom | `*.example` names fail only while the corp VPN is up |
| 2 | Why | split-tunnel VPN makes its DNS primary; it doesn't know `.example` |
| 3 | Diagnose | 4 commands that separate routing from DNS (no sudo) |
| 4 | Fix | two `/etc/resolver/*` files → AdGuard (`192.0.2.100`) |
| 5 | Verify | names resolve + HTTP 200 with the VPN **on** |
| 6 | If the VPN ever goes full-tunnel | fall back to Tailscale |

---

## 1. Symptom

With the company VPN connected, on the Mac:

- `http://jellyfin.example:8096` (or any `*.example` / `*.example.org` host) fails
  to resolve / connect.
- **But** `raspberrypi.local` and the raw IP `192.0.2.100:<port>` still work.

That last line is the whole diagnosis in miniature: the *service* is reachable,
only *name resolution* for `.example` is broken. `raspberrypi.local` survives because
it is **mDNS/Bonjour** (multicast, resolved by the OS directly — it never touches
the configured DNS servers the VPN hijacked).

## 2. Why it happens (split-tunnel VPN + primary DNS)

Verified live 2026-07-08:

- **Routing is fine.** Default route stays `192.0.2.1` via `en0`; `route -n get
  192.0.2.100` stays on `en0`; `ping 192.0.2.100` succeeds. The VPN is
  **split-tunnel** — it does *not* capture `192.168.0.0/16`.
- **DNS is the problem.** The VPN adds its own resolvers (seen:
  `10.102.103.211`, `10.34.80.100`) as **primary**. Those don't host `.example`
  and don't forward the query to AdGuard, so the system resolver returns nothing
  for `jellyfin.example` even though AdGuard would answer it instantly.

So the failure hits **every** `*.example` / `*.example.org` name at once; you just
notice it on whatever you opened.

## 3. Diagnose (read-only, no sudo) — separate routing from DNS

Run these **while the VPN is connected**:

```bash
# (a) Routing test — bypasses DNS entirely. If this works, it is NOT a route problem.
ping -c 2 192.0.2.100
route -n get 192.0.2.100 | grep interface        # → en0 == LAN (good, split-tunnel)

# (b) Who owns DNS now? Corp 10.x resolvers present == VPN made itself primary.
scutil --dns | grep 'nameserver\[' | sort -u

# (c) Does AdGuard still answer the name directly? (It will.)
dig +short @192.0.2.100 jellyfin.example          # → 192.0.2.100

# (d) Does the *system* resolver answer it? (This is what the browser sees.)
dscacheutil -q host -a name jellyfin.example | grep ip_address || echo "EMPTY — DNS gap confirmed"
```

If (a)+(c) succeed but (d) is empty, it is definitively the resolver gap → §4.
Nothing on the Pi needs touching.

## 4. Fix — scoped resolver files (`/etc/resolver`)

macOS reads `/etc/resolver/<domain>` and routes queries for that domain to the
listed nameserver **regardless of what the VPN set as primary**. Point both
internal TLDs at AdGuard. Needs your admin password (one-time):

```bash
sudo mkdir -p /etc/resolver
printf 'nameserver 192.0.2.100\n' | sudo tee /etc/resolver/homelab /etc/resolver/example.org
```

macOS picks these up **instantly** — no restart, no `dscacheutil -flushcache`
needed. The files are permanent (survive reboots); they are only removed by hand.

> Both TLDs matter because the internal domain is mid-migration `.example` →
> `.example.org` (dual-running, [design 038](../archive/design/038-homelab-io-domain-migration.md)).
> Add a third file if a new internal TLD is ever introduced.

## 5. Verify (with the VPN **on**)

```bash
scutil --dns | grep -A1 'domain.*: homelab'          # → domain: homelab / nameserver: 192.0.2.100
dscacheutil -q host -a name jellyfin.example | grep ip_address       # → 192.0.2.100
dscacheutil -q host -a name jellyfin.example.org | grep ip_address    # → 192.0.2.100
curl -s -o /dev/null -w "%{http_code}\n" http://jellyfin.example:8096/health   # → 200
```

Confirmed 2026-07-08 across all three states: VPN off → OK, VPN on → OK, VPN
re-toggled → OK. The scoped resolver is a no-op when the VPN is off (AdGuard is
already primary then), so it never conflicts.

## 6. If the corp VPN ever goes **full-tunnel**

The scoped-resolver fix relies on the VPN leaving the LAN route intact. If IT
ever switches it to **full-tunnel** (captures `192.168.0.0/16`, so even
`ping 192.0.2.100` fails), DNS alone won't save you — the *route* is gone too.
Fall back to **Tailscale**: the Pi's tailnet IP is `100.64.0.10` (Tailscale is
installed on the Mac at `/usr/local/bin/tailscale`, but was found **stopped** —
turn it on and leave it on). Tailscale rides the `100.64.0.0/10` CGNAT range,
which corp full-tunnel policies essentially never claim, so it survives when the
LAN route doesn't. Reach services at `http://100.64.0.10:<port>` (enable
MagicDNS for names). See [tailscale DNS design](../archive/design/038-homelab-io-domain-migration.md)
and the `tailscale_dns` role.

---

## See also

- [design 038 — `.example` → `.example.org` domain migration](../archive/design/038-homelab-io-domain-migration.md)
  (internal DNS architecture: AdGuard rewrites, dual-TLD, self-signed cert SANs)
- [runbook 045 — macOS homelab CA trust](045-2026-06-07-macos-homelab-ca-trust.md)
  (the *other* Mac-local gap: HTTPS trust; this one is name resolution)
- `ansible/roles/adguard/` — the DNS server that answers `*.example`
- `ansible/roles/tailscale_dns/` — off-LAN / full-tunnel fallback path
