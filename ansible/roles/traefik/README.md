# traefik

Traefik v3 reverse proxy for `https://<svc>.<homelab_internal_domains[0]>`. It
serves a leaf certificate signed by the homelab's own internal CA on LAN and
Tailscale alike. It is a convenience router, **not an auth boundary**: every
backend is still reachable on its own host port. See
[docs/conventions/reverse-proxy-not-an-auth-boundary.md](../../../docs/conventions/reverse-proxy-not-an-auth-boundary.md).

## What it deploys

A `traefik:v3.3` container named `traefik`, with `network_mode: host`. It runs
with a read-only root filesystem, a `/tmp` tmpfs and `cap_drop: ALL` plus
`NET_BIND_SERVICE` only. Routing comes from the file provider (`dynamic.yml`).
Each `traefik_services` entry becomes a `<name>.<domain>` router that forwards
to `127.0.0.1:<port>`.

| Item        | Value                                                          |
|-------------|----------------------------------------------------------------|
| Entrypoints | `:80` (301 to HTTPS), `:443`; UFW allows both from anywhere    |
| Dashboard / metrics | `:8180` (`traefik_dashboard_port`, UFW: `lan_subnet` + `docker_bridge_subnet`) |
| Config      | `/home/<docker_user>/docker/network/traefik/config/{traefik,dynamic}.yml` |
| Certs       | `/home/<docker_user>/docker/network/traefik/certs` (`homelab.crt`, `homelab-ca.crt`) |
| Memory      | `traefik_mem_limit` (`256M`)                                   |

## Deploy

```bash
make traefik
```

Base deploy order is tailscale, then adguard, then traefik
(see the top-level [README](../../../README.md)). The role is re-run when a service is added,
because the cert SAN list is derived from `traefik_services`.

## Configuration

- `traefik_services`: list of `{name, port, auth}` entries. `auth: true` attaches
  the `authelia` forward-auth middleware (`traefik_authelia_address`). Never set
  it on the `auth` (Authelia) entry itself, because that causes a redirect loop.
- `traefik_internal_ca_enabled` / `traefik_internal_leaf_not_after`: the role
  creates a private CA plus a leaf whose explicit SANs cover every service ×
  every internal domain. The play fails when the leaf has fewer than 30 days
  left. To renew, bump the date and redeploy. The CA root is fetched to
  `roles/common/files/homelab-ca.crt`.
- `vault_traefik_internal_ca_key` / `vault_traefik_internal_ca_cert`: when set,
  an SSD rebuild reuses the same CA, so devices do not need to re-trust it.
- `vault_traefik_dashboard_htpasswd`: optional basic auth for the dashboard.
- `vault_tailscale_fqdn`: if set, `tailscale cert` issues a cert, and a monthly
  cron (1st of the month, 03:00) renews it. The cert is still loaded, but the
  Tailscale path-prefix routers were removed on 2026-08-04 because they bypassed
  SSO. Remote clients now use the normal `<svc>.<domain>` names through
  `tailscale_dns`.

## Operations

- **Healthcheck:** `traefik healthcheck --ping`.
- **Metrics:** vmagent job `traefik` scrapes `127.0.0.1:8180/metrics`.
- **Logs and alerts:** JSON access logs go to stdout, with request headers
  dropped, and fluentbit ships them to VictoriaLogs. `TraefikErrorRateHigh` is
  in `roles/vmalert/templates/rules/container-error-rate.yml.j2`.
- **Dashboard UFW quirk:** because Traefik runs on the host network, `:8180` is a
  host port that UFW INPUT filters. Containers such as homepage therefore need the
  `docker_bridge_subnet` rule.
- Docs: [docs/design/033-internal-ca-https.md](../../../docs/design/033-internal-ca-https.md),
  [docs/guides/004-traefik.md](../../../docs/guides/004-traefik.md),
  [docs/runbooks/045-2026-06-07-macos-homelab-ca-trust.md](../../../docs/runbooks/045-2026-06-07-macos-homelab-ca-trust.md).
