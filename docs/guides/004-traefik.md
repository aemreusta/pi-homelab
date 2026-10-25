# Traefik reverse proxy

Current repository configuration uses Traefik in host-network mode, HTTP on 80,
HTTPS on 443 and its dashboard on 8180. Browser routes use
`<service>.example.internal`. This guide replaces the old HTTP-only and
`.example` setup examples.

## Routing and TLS

```text
LAN client → AdGuard DNS → 192.0.2.100 → Traefik → configured backend
Tailnet client → split DNS → 100.64.0.10 → Traefik → configured backend
```

Internal HTTPS and HTTP→HTTPS redirection are enabled in
[Traefik defaults](../../ansible/roles/traefik/defaults/main.yml).
The internal CA signs service names derived from `traefik_services` and
[`homelab_internal_domains`](../../ansible/inventory/group_vars/all/main.yml).
Clients need to trust the CA; see the [Mac trust runbook](../runbooks/045-2026-06-07-macos-homelab-ca-trust.md).
An optional Tailscale FQDN certificate is a separate path; it does not replace
CA trust for `*.example.internal`.

The route list, backend ports, redirects and middleware belong in role variables
and templates, not a second table in this guide. Portainer's configured backend
uses HTTP so the proxy does not need to bypass its backend certificate validation.

## Authentication boundary

Selected browser routes use Authelia forward-auth. Services with directly
reachable host ports still have a second access path. Inventory and migrate
Homepage, monitoring and machine API consumers before closing those ports;
keep application authentication until access is actually restricted.
See the [auth-boundary convention](../conventions/reverse-proxy-not-an-auth-boundary.md).
The [sync test](../../tests/test_authelia_traefik_sync.py) checks that Traefik's
auth selection agrees with Authelia's service list.

## Add or change a service

1. Add the backend to `traefik_services`, deriving its hostname from the shared domain.
2. Confirm the backend is reachable from the host-network proxy and its intended API consumers.
3. If using forward-auth, update the matching Authelia policy and verify the sync guard.
4. Run `make validate`. Ship through the documented tag-gated process;
   `make traefik` remains available for initial provisioning or deliberate recovery.
5. Verify DNS, TLS hostname/issuer, the backend response and the intended login/API behavior.

The role templates static/dynamic configuration, installs CA/certificates and
firewall rules, and applies Compose. Inspect its check/diff before apply.

## Diagnose

```bash
dig @192.0.2.100 jellyfin.example.internal
curl -I https://jellyfin.example.internal
ssh homelab 'docker logs --tail 50 traefik'
```

A DNS failure, untrusted CA, hostname mismatch, backend error and Authelia denial
are different failure paths. Test the relevant layer; an auth redirect alone
is not application health. For corporate VPN resolver conflicts use
[runbook 046](../runbooks/046-2026-07-08-macos-corp-vpn-homelab-dns.md).
