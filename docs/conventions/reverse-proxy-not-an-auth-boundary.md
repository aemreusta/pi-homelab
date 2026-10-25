# Convention — Traefik is a convenience router, **not** an auth boundary

**TL;DR:** Every service in this stack publishes its own LAN-open host port **and** is routed by
Traefik. Traefik runs `network_mode: host` and reaches each backend at `http://127.0.0.1:<port>` —
it does **not** sit in the data path as a gate. So **any auth applied at the Traefik layer
(forward-auth, basic-auth middleware, IP allow-lists on a router) protects only the `*.example`
hostname path — a client that hits `192.0.2.100:<port>` directly walks straight past it.** Do not
plan SSO, forward-auth, or "lock service X down via Traefik" without first closing that service's
host port and UFW rule. Proven, not theoretical.

## Why this doc exists

An SSO research report (see [research/036](../research/036-sso-identity-provider-options.md))
recommended Authelia forward-auth across ~20 services on the premise that "one Traefik middleware
protects all." In this topology that premise is **false**, and shipping it would have produced
security theater — every gated service still answers, unauthenticated, on its own port. The same
reflex recurs any time someone reasons "it's behind the reverse proxy, so it's protected." It
isn't. This records the constraint so the next auth/exposure sprint starts from the truth.

## The proof (live, 2026-06-07)

```text
curl http://192.0.2.100:8989/      → HTTP 302   # Sonarr answers directly: no Traefik, no auth
curl -H Host:sonarr.example :80       → HTTP 301   # the Traefik path is just *one* of two doors
```

And UFW opens the direct door for most services:

```text
8989/tcp ALLOW IN Anywhere  # Sonarr      7878/tcp ALLOW IN Anywhere  # Radarr
8181/tcp ALLOW IN Anywhere  # qBittorrent 8096/tcp ALLOW IN Anywhere  # Jellyfin
9000+9443 ALLOW IN Anywhere # Portainer   8082/tcp ALLOW IN Anywhere  # FileBrowser
6246/tcp ALLOW IN Anywhere  # Maintainerr  ... (most of the 29 routed services)
```

Routing model: `ansible/roles/traefik/templates/dynamic.yml.j2` builds a static **file provider**
from the `traefik_services` list, each as `loadBalancer → http://127.0.0.1:<svc.port>`. Compose
templates bind `"<port>:<port>"` on the host. Two independent paths to the same service; auth on the
proxy path covers one of them.

## The rule

1. **Reverse-proxy auth ⇒ close the direct port.** Before (or with) any Traefik-layer auth on a
   service, you must also (a) remove its `ports:` host binding so it's only reachable on a Docker
   network, (b) drop its UFW `ALLOW IN` rule, and (c) ensure Traefik reaches it over that Docker
   network instead of `127.0.0.1:<port>`. All three, or the auth is decorative.
2. **For machine-to-machine surfaces, don't use interactive auth at all.** The *arr / qBit
   / ntfy-publish / control-API paths are consumed by API key/token, not browser sessions. The way
   to take them off the open LAN is **port closure + UFW scoping to `192.0.2.0/24` + Tailscale**,
   not forward-auth.
3. **The cheap hardening that needs none of the above:** most service ports are `ALLOW IN Anywhere`
   today. Scoping them to LAN + Tailscale is a big security win with **no topology change** and is
   per-rule reversible — do this before reaching for an IdP.
4. **Don't trust "it's behind Traefik" in a threat model.** Traefik gives you nice hostnames, TLS
   under the internal CA, and security headers. It does **not** give you access control while the
   host ports stay open.

## Related

- [research/036-sso-identity-provider-options.md](../research/036-sso-identity-provider-options.md) — full SSO evaluation + the safe phased plan
- [guides/004-traefik.md](../guides/004-traefik.md) — "additive / ports preserved" is the same fact, stated as a feature
- [design/001-architecture.md](../design/001-architecture.md#infrastructure-and-network) — exposure model + firewall chain
- [ops-discipline.md §4](../operations/ops-discipline.md) — verify premises (here: "behind the proxy = protected") against live state before building
