# Tailscale VPN and split DNS

Tailscale runs on the Pi host, advertising the LAN subnet. The separate
`tailscale_dns` role provides dnsmasq for internal service names. Current addresses
and domains come from [shared variables](../../ansible/inventory/group_vars/all/main.yml):
`192.0.2.100` (LAN), `100.64.0.10` (tailnet), `example.internal` (service domain).

## Provisioning

The [tailscale role](../../ansible/roles/tailscale/) installs its configured apt
repository/package, starts `tailscaled`, authenticates from the encrypted vault,
advertises routes and applies firewall rules. The role currently selects the
Bookworm repository; that is a repository configuration choice, not a claim about
upstream Trixie support today.

For initial setup, provision `make tailscale` before `make tailscale-dns`.
The DNS role needs the Pi's authenticated tailnet address. Keep auth keys in the
vault and inspect [role defaults](../../ansible/roles/tailscale/defaults/main.yml)
for advertised routes, DNS acceptance and exit-node settings.

## Tailnet control-plane setup

Approve the intended subnet route in the Tailscale admin console. Configure a
restricted/split nameserver for **`example.internal` → `100.64.0.10`**. Old
`.example`/`.example.org` routes are retired. MagicDNS hostnames and this internal
service suffix are separate resolution paths.

The repo's split-DNS probe detects drift; it does not imply the controller can
change tailnet-wide DNS settings. A successful LAN probe is also not proof of
remote access. Credential expiry and off-LAN verification remain open work.

## Verify access

On the Pi, confirm the daemon is authenticated and has its expected address:

```bash
tailscale status
tailscale ip -4
```

From a connected remote client, verify the restricted resolver and then the
client's normal resolver/application path:

```bash
dig @100.64.0.10 jellyfin.example.internal
curl -I https://jellyfin.example.internal
```

The HTTPS client still needs the internal CA. See the
[Traefik guide](004-traefik.md) and [Mac CA trust procedure](../runbooks/045-2026-06-07-macos-homelab-ca-trust.md).
Testing the Pi address directly verifies routing, but does not verify split DNS
or hostname-based proxy routing. Test off-LAN when accepting a remote-access change.

## Recovery

If dnsmasq is running but returns `REFUSED`, inspect its Docker network endpoint,
not only the process. A container with only loopback cannot be repaired by a
simple restart. The [endpoint-loss incident](../incidents/2026-07-11-tailscale-dns-lost-network-endpoint-refused-forward.md)
records the diagnostic and recovery distinction.

macOS corporate VPN DNS conflicts use the scoped-resolver procedure in
[runbook 046](../runbooks/046-2026-07-08-macos-corp-vpn-homelab-dns.md).
Mac workers normally use the LAN address and do not require Mac Tailscale to run
while on that LAN. Recheck firewall, route approval and DNS separately when
remote connectivity fails.
