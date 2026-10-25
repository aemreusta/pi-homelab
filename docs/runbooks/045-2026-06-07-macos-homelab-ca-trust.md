# 045 — 2026-06-07: macOS — trusting the homelab CA (`ERR_CERT_AUTHORITY_INVALID`)

> **Current use:** substitute `example.internal` for the old `.example`
> examples below. The trust procedure is retained; validate the certificate
> against a current hostname. [Current proxy guide](../guides/004-traefik.md).

**Status:** resolved 2026-06-07. Chrome on the Mac threw
`net::ERR_CERT_AUTHORITY_INVALID` ("Your connection is not private") on
`https://label-studio.example` — and, in fact, on *every* `*.example` HTTPS
host. Root cause was **not** a bad cert: the `homelab CA` root was sitting in
the Mac's System keychain but had **no SSL trust setting**. Importing a CA into
the keychain does *not* trust it — that's a separate, easily-missed step.

> Mac-local trust gap, **not** a repo/Traefik bug. The Pi auto-trusts the CA via
> the `common` role; the Mac is a manual import (design
> [033 §Device trust](../design/033-internal-ca-https.md)) that stopped one step
> short. This runbook is the missing step + how to diagnose/verify it.

## Quick navigation

| § | What | One-line |
|---|---|---|
| 1 | Symptom | `ERR_CERT_AUTHORITY_INVALID` on any `*.example` over HTTPS |
| 2 | Why | CA is *in* the keychain but not *trusted* (import ≠ trust) |
| 3 | Diagnose | three commands that pinpoint it (no sudo) |
| 4 | Fix | one command (or the GUI equivalent) + relaunch Chrome |
| 5 | Verify | `verify-cert` must say "successful" |
| 6 | Fresh Mac | full bootstrap from a Pi with the CA already on disk |

---

## 1. Symptom

Chrome (or Safari/curl) rejects `https://<svc>.example`:

```text
Your connection is not private … net::ERR_CERT_AUTHORITY_INVALID
This server could not prove that it is label-studio.example; its security
certificate is not trusted by your computer's operating system.
```

`ERR_CERT_AUTHORITY_INVALID` means **the issuer isn't trusted** — distinct from
`ERR_CERT_COMMON_NAME_INVALID` (name/SAN mismatch). If you see the *name* error
instead, the service is missing from the leaf SAN → add it to `traefik_services`
and `make traefik` (the SAN auto-extends; see `traefik/tasks/internal-ca.yml`).
This runbook is for the *authority* error.

## 2. Why it happens (import ≠ trust)

A cert dropped into `/Library/Keychains/System.keychain` is **not** automatically
a trusted anchor. macOS keeps trust as a *separate* setting; without an explicit
"Always Trust" (SSL) trust setting the OS — and Chrome, which uses the macOS
trust store — treats the issuer as unknown. So the symptom hits **every**
`*.example` HTTPS host at once; you just notice it on whatever you opened
(here, label-studio). The system roots Apple preinstalls live in a *different*,
read-only keychain (`SystemRootCertificates.keychain`) — being in `System.keychain`
is not the same thing.

## 3. Diagnose (read-only, no sudo)

```bash
# (a) Is the leaf chain/SAN actually fine? Pull the live cert and OS-evaluate it.
echo | openssl s_client -connect raspberrypi.local:443 -servername label-studio.example 2>/dev/null \
  | openssl x509 > /tmp/ls-leaf.pem
security verify-cert -c /tmp/ls-leaf.pem -p ssl -s label-studio.example
#   → CSSMERR_TP_NOT_TRUSTED  == issuer not trusted (this runbook)
#   → "...successful."        == already trusted (look elsewhere)

# (b) Is the CA even present, and where?
security find-certificate -c "homelab CA" /Library/Keychains/System.keychain >/dev/null \
  && echo "present in System keychain" || echo "ABSENT — import it (see §6)"

# (c) Prove the chain is good when the CA is anchored explicitly.
security find-certificate -c "homelab CA" -p /Library/Keychains/System.keychain > ~/.homelab-ca.pem
security verify-cert -c /tmp/ls-leaf.pem -r ~/.homelab-ca.pem -p ssl -s label-studio.example
#   → "...successful."  == cert is fine; ONLY the trust setting is missing → §4
```

If (a) fails but (c) succeeds, it is definitively the trust setting — nothing on
the Pi needs touching.

## 4. Fix

Export the CA to a file (if not already done in §3c), then mark it a trusted
SSL root. Needs your admin password.

```bash
security find-certificate -c "homelab CA" -p /Library/Keychains/System.keychain > ~/.homelab-ca.pem
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/.homelab-ca.pem
```

**GUI equivalent** (the CA is already in the keychain, just flip its trust):
Keychain Access ▸ **System** ▸ double-click **homelab CA** ▸ expand **Trust** ▸
*When using this certificate* → **Always Trust** ▸ close (enter password to save).

Then **fully quit and reopen Chrome** (Cmd+Q — Chrome snapshots the trust store
at launch; a reopened window isn't enough).

> The leaf's ~729-day validity is **fine**: Apple's 398-day TLS limit applies
> only to certs chaining to *Apple-preinstalled* roots. User/admin-added private
> roots like `homelab CA` are exempt (Apple HT211025). Don't "fix" the leaf
> lifetime — it's not the problem.

## 5. Verify

```bash
security verify-cert -c /tmp/ls-leaf.pem -p ssl -s label-studio.example
#   → "...certificate verification successful."
```

Then load `https://label-studio.example` in Chrome — clean padlock, and so is
every other `*.example` host.

## 6. Fresh Mac / SSD rebuild — full bootstrap

If the CA isn't on the Mac at all (new machine, or the CA was regenerated on the
Pi — which forces re-trust on **every** device):

```bash
# Grab the published root from the repo (the common role fetches it off the Pi)
# … or scp it straight from the Pi:
scp pi@raspberrypi.local:/srv/docker/traefik/certs/homelab-ca.crt ~/.homelab-ca.pem
#   (repo copy: ansible/roles/common/files/homelab-ca.crt — same bytes)

# Import AND trust in one shot (add-trusted-cert imports if absent, then trusts):
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/.homelab-ca.pem
```

Quit/reopen Chrome, then verify per §5. The CA root is ~10-year-lived
(`traefik_internal_ca_not_after`), so this is a once-per-machine step until the
root is rotated.

---

## See also

- [design 033 — Internal CA: trusted HTTPS for `*.example`](../design/033-internal-ca-https.md)
  (§Device trust covers iOS/Android/Windows/Linux; this runbook is the macOS deep-dive)
- `ansible/roles/traefik/tasks/internal-ca.yml` — CA + wildcard leaf generation,
  SAN auto-extension from `traefik_services`, 30-day expiry guard
