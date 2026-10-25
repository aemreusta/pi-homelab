# 033 — Internal CA: trusted HTTPS for `*.example` (LAN + Tailscale)

> **Current hostname:** `*.example.internal`. The CA decision below is retained
> with its original June examples; use the [current proxy guide](../guides/004-traefik.md)
> and [CA trust runbook](../runbooks/045-2026-06-07-macos-homelab-ca-trust.md) for operation.

**Status:** shipped 2026-06-06. Supersedes the HTTP-only-LAN model in
[004-traefik guide](../guides/004-traefik.md) and completes Phase 2 of
[025-ntfy-dns-name](../archive/design/025-ntfy-dns-name.md).

## Why

Browsers and apps increasingly force HTTPS. LAN `*.example` was HTTP-only, so:

- `https://<svc>.example` (browser auto-upgrade / HSTS) hit Traefik with **no HTTPS
  router → 404**. Worse, the `security-headers` middleware emitted
  `Strict-Transport-Security … includeSubDomains` **over plaintext HTTP** (RFC 6797
  §7.2 violation), pinning browsers to HTTPS for a host with no HTTPS.
- The ntfy phone app defaults to `https://` for a scheme-less server, but ntfy served
  plaintext HTTP on `:2586` → connection failed.

Fix: a private CA issues a trusted cert for every `*.example` host; Traefik serves
HTTPS on `:443` for both LAN and Tailscale; ntfy moves behind Traefik. HSTS becomes
correct (HTTPS-only).

## Key enabler — one cert, both networks

AdGuard (LAN) resolves `*.example → 192.0.2.100`; dnsmasq (Tailscale) resolves
`*.example → 100.64.0.10`. **Both terminate at the Pi's Traefik :443**, so a single
internal-CA cert presented by Traefik serves every client on either network. No
per-network cert juggling; `https://<svc>.example` works on LAN and tailnet alike.

## Design decisions

- **`community.crypto` ownca, not step-ca.** A two-tier static CA (root signs a leaf)
  generated at deploy time by `ansible/roles/traefik/tasks/internal-ca.yml`. step-ca
  was rejected: it adds a long-running daemon + ACME plumbing + RAM for automated
  short-lived rotation that a single Pi serving ~26 static hostnames doesn't need.
  ownca is 100% declarative, zero runtime footprint, matches the repo's Ansible-only
  model. Re-evaluate only if cert count/rotation needs ever explode.
- **Explicit per-host SANs, not a bare wildcard.** `*.example` alone is **rejected by
  OpenSSL/curl/browsers** — wildcards whose parent is a single label (`.example`)
  don't match (a guard against `*.com`-style certs). The leaf SAN enumerates
  `DNS:<svc>.example` for every `traefik_services` entry (+ `example`, `traefik.example`,
  and the `*.example` wildcard for lenient clients). Adding a service auto-extends the
  SAN → the cert re-issues on the next deploy.
- **Leaf only to Traefik (no fullchain).** Root signs the leaf directly; devices trust
  the root, so no intermediate/fullchain is needed — avoids cert-ordering pitfalls.
- **HSTS split.** `security-headers` (no HSTS) for any plaintext context;
  `security-headers-tls` (+ HSTS) on websecure routers only. RFC 6797 compliant.
- **HSTS max-age was staged.** `traefik_hsts_seconds` ran at `86400` (1 day) during
  rollout so a cert mistake self-heals; **now `31536000` (1y)** as of rollout close-out
  (commit `934ed74`).
- **HTTP→HTTPS redirect is gated by `traefik_force_https_redirect`.** It ran `false`
  (dual-stack — HTTP and HTTPS both serve, no lockout risk) during rollout and is **now
  `true`** (plaintext HTTP 301s to HTTPS). Set both back to the rollout values to return
  to dual-stack if needed.
- **ntfy decoupled URLs.** `ntfy_public_url=https://ntfy.example` drives only
  `NTFY_BASE_URL` (action-button/web/mobile links) + the phone subscription. The ~30
  internal senders keep using `ntfy_external_*` on the HTTP `:2586` path — so the
  migration touches the ntfy compose + one group_var, not 30 sender trust stores.
  ntfy runs `NTFY_BEHIND_PROXY=true`; the `:2586` host port stays published (senders +
  Traefik backend `http://127.0.0.1:2586`). **Do NOT localhost-bind `:2586`** —
  ntfy-bridge reaches ntfy via the host LAN IP:2586; binding to loopback would kill all
  vmalert→ntfy delivery.

## CA durability (DR)

The CA key signs all certs; losing it = re-trust every device. First run generates it
on the Pi. **After first run, copy the key+cert into vault**
(`vault_traefik_internal_ca_key`/`_cert`) so an SSD rebuild reuses the same CA. The
role materializes the vaulted values (`no_log`) when present, else generates.

## Renewal

Validity is set with **absolute** timestamps (`traefik_internal_{ca,leaf}_not_after`),
not relative `+Nd`, so re-runs are idempotent. To renew: bump
`traefik_internal_leaf_not_after` and redeploy. An `x509_certificate_info` + `assert`
guard **fails the play when the leaf has < 30 days left**. Check remaining validity:
`sudo openssl x509 -noout -enddate -in <certs>/homelab.crt`. Root is ~10 years.

## Device trust (manual — operator)

Install the root `ansible/roles/common/files/homelab-ca.crt` (fetched from the Pi):

- **iOS:** AirDrop/email the `.crt` → install profile → **then** Settings ▸ General ▸
  About ▸ Certificate Trust Settings ▸ **enable full trust** (the easily-missed step).
- **Android:** Settings ▸ Security ▸ Encryption & credentials ▸ Install a certificate ▸
  CA certificate. Verify the ntfy app accepts the user-store CA.
- **macOS:** double-click → Keychain *System* ▸ set "Always Trust". Import ≠ trust
  — the cert can sit in the keychain untrusted and every `*.example` HTTPS host
  still throws `ERR_CERT_AUTHORITY_INVALID`. Full diagnose/fix/verify + the CLI
  one-liner: [runbook 045](../runbooks/045-2026-06-07-macos-homelab-ca-trust.md).
- **Windows:** `certlm.msc` ▸ Trusted Root Certification Authorities ▸ Import.
- **Linux:** `/usr/local/share/ca-certificates/` + `sudo update-ca-certificates`
  (the Pi host does this automatically via the common role). Firefox uses its own store.

## Clearing the OLD bad HSTS (one-time)

Browsers that cached the pre-fix HSTS keep force-upgrading. After installing the CA,
HTTPS works so it's moot; if a browser still misbehaves on HTTP, clear it once:
Chrome `chrome://net-internals/#hsts` → "Delete domain security policies" →
`<svc>.example` (and `example`).

## Rollback

`traefik_internal_ca_enabled: false` + `traefik_force_https_redirect: false` →
`make traefik` reverts to HTTP routers. (Browsers with cached HSTS still upgrade —
clear as above.)
