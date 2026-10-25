# 038 — Internal domain migration: `example` → `example.org` (dual-running)

> **Superseded migration record.** Since 2026-07-15 the sole domain is
> `example.internal`; `.example` and `.example.org` are retired. Use the
> [current architecture](../../design/001-architecture.md#infrastructure-and-network).
> The historical steps below must not be replayed as current setup.
>
> **Status:** Shipped 2026-06-24 (dual-running; example.org primary).
> **Related:** [033 — internal CA + HTTPS](../../design/033-internal-ca-https.md),
> roles `adguard`, `traefik`, `tailscale_dns`, `homepage`.

## Context

The internal TLD was `.example` (a made-up single-label TLD). We are renaming it
to `.example.org` for a real-TLD-looking name. The trigger was a Samsung TV showing
`jellyfin.example` as "not secure" and refusing to open it — but see the **caveat**
below: the rename does **not** by itself fix that.

## Decision

Run **both** `*.example` (legacy) and `*.example.org` (new) simultaneously during a
transition, driven by a **single source of truth**: `homelab_internal_domains` in
`group_vars/all/main.yml` (first entry = primary). Every resolver, router and cert
derives its domains from this list, so the cutover and any future rename are a
one-line reorder/edit.

```yaml
homelab_internal_domains:
  - example.org   # primary (leaf cert CN, first-listed everywhere)
  - homelab      # legacy, still fully functional
```

## Mechanism (what consumes the list)

| Layer | How it uses the list |
|---|---|
| **AdGuard** (LAN DNS) | one `*.<domain>` rewrite per entry → Pi IP (`adguard_dns_rewrites`) |
| **dnsmasq** (Tailscale/VPN DNS) | one `address=/<domain>/<tailscale-ip>` line per entry |
| **Traefik cert SAN** | `*.d`, `d`, and `<svc>.d` + `traefik.d` for every domain × service; leaf CN = `*.<first>` |
| **Traefik routers** | each rule becomes `Host(svc.example.org) \|\| Host(svc.example)` |
| **Homepage** | dashboard hrefs point at the primary (`.example.org`) |

One self-signed leaf cert (internal `homelab CA`) carries both domains' SANs, so
both serve trusted-by-our-CA HTTPS off a single cert.

**App-level Host allowlists also need the domain list.** DNS + cert + Traefik
routing being correct is not enough — some apps validate the `Host`/`Origin`
header themselves and 4xx an unknown name even though the proxy routed it fine.
Found so far (both derive their allowlist from `homelab_internal_domains`):

- **Homepage** — `HOMEPAGE_ALLOWED_HOSTS` (the symptom was `homepage.example.org`
  → "Host validation failed").
- **Label Studio** — Django `CSRF_TRUSTED_ORIGINS` (login/POST rejected otherwise).
- MLflow uses `*` (no change). When adding any new host-validating service, build
  its allowlist from `homelab_internal_domains`, not a hardcoded `.example`.

## Verified (2026-06-24)

- `jellyfin.example` and `jellyfin.example.org` both resolve to the Pi (AdGuard + dnsmasq).
- Both serve HTTPS validated against the homelab CA; leaf CN = `*.example.org`.
- Router rule = `Host(jellyfin.example) || Host(jellyfin.example.org)`.

## Caveats / deliberate non-goals

- **The rename does NOT fix untrusted-CA devices (the Samsung TV).** Certs stay
  **self-signed** (operator choice). Tizen won't trust the homelab CA and can't
  easily install a custom root, so `https://jellyfin.example.org` is *still* "not
  secure" on the TV. Real fix would need a domain we **own** + Let's Encrypt
  (DNS-01) — out of scope for now. **TV workaround:** the native Jellyfin Tizen
  app pointed at `http://192.0.2.100:8096` (plain HTTP, no cert), verified
  reachable (UFW-open, HTTP 200).
- **`example.org` is a real public TLD we do NOT own** — this is split-horizon
  only (LAN AdGuard + Tailscale dnsmasq). We never request public certs for it
  and never expose it; a client off split-DNS would hit the real example.org.
  Acceptable for an internal name with self-signed certs.
- **HSTS:** once a browser loads any `*.example` HTTPS page (HSTS
  `includeSubdomains`), it forces HTTPS for that whole domain — so a plain-HTTP
  fallback on `.example` won't work in that browser. `.example.org` is a fresh
  domain until its first HTTPS visit.

## Remaining / cutover checklist

- [ ] **Notification `*_hostname` vars** (`ntfy_hostname`, `control_hostname`,
      `beszel_hostname`, `homepage_hostname` and others) still resolve to `.example`. They
      work (both domains resolve), so this is cosmetic consistency — flipping them
      is a **wider redeploy** (the `common` alert scripts + `/etc/hosts` safety-net
      pins + ntfy `NTFY_BASE_URL` + homelab_control/backup scripts) and
      ntfy is delivery-delicate, so it is held as a deliberate, separately-verified
      step rather than bundled with the DNS/cert flip.
- [ ] When fully cut over, drop `example` from `homelab_internal_domains` (cert
      shrinks to `.example.org` only, routers stop matching the legacy names).
- [ ] Historical docs (sprints, incidents, runbooks) keep their `.example`
      references **by design** — they record past state; do not mass-rewrite them.
