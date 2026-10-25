# ntfy Phone Setup Runbook

Configure push notifications from your homelab to your phone.

## Overview

ntfy is a self-hosted push notification service. As of 2026-06-06 it runs **behind
Traefik with trusted HTTPS** ([design 033](../design/033-internal-ca-https.md),
[design 025](../archive/design/025-ntfy-dns-name.md) Phase 2):

- **Server (canonical):** `https://ntfy.example` — one URL that works on **both LAN and
  Tailscale** (AdGuard resolves it to the Pi on LAN; dnsmasq resolves it to the Pi's
  Tailscale IP on the tailnet; the same internal-CA cert is served on both).
- **Topic:** `homelab`
- **Auth:** none — access is restricted by UFW (LAN + Tailscale) and the topic name.
- **HTTP fallback:** `http://ntfy.example:2586` still serves (used by internal senders);
  prefer HTTPS on the phone.

## Prerequisites

1. ntfy + Traefik deployed: `make ntfy && make traefik`.
2. **Install the homelab internal CA root on the phone** — without it the app rejects
   the `https://ntfy.example` cert. The root is `ansible/roles/common/files/homelab-ca.crt`.
   See the per-platform steps in [design 033 → Device trust](../design/033-internal-ca-https.md#device-trust-manual--operator).
   - **iOS:** install the profile, **then** Settings ▸ General ▸ About ▸ Certificate
     Trust Settings ▸ enable full trust (the easily-missed second step).
   - **Android:** Settings ▸ Security ▸ Encryption & credentials ▸ Install a certificate ▸
     CA certificate.
3. Phone on the home WiFi, or Tailscale connected for remote.
4. ntfy app installed (iOS App Store / Android Play Store / F-Droid), by Philipp C. Heckel.

---

## Subscribe (iOS and Android — same steps)

1. Open the ntfy app and tap **+** to add a subscription.
2. Configure:
   - **Topic:** `homelab`
   - **Server:** `https://ntfy.example` (tap "Use another server" / custom server)
   - **Name:** `Homelab Notifications`
3. Tap **Subscribe**.

The single `https://ntfy.example` works on LAN and over Tailscale — no separate
Tailscale-IP subscription is needed anymore (the old `http://<tailscale-ip>:2586` setup
is retired).

### Background delivery

- **iOS:** Settings ▸ ntfy → enable Notifications + Background App Refresh; allow Sound,
  Badge, Alert.
- **Android:** Settings ▸ Apps ▸ ntfy → Notifications: allow all; Battery: Unrestricted
  (disable optimization); allow background data.

---

## Web app (any device)

1. Browse to `https://ntfy.example` (the browser must trust the internal CA — install
   the root on that device first).
2. Enter topic `homelab` and Subscribe; allow browser notifications.

Notifications only arrive while the browser tab is open — use for testing, not as the
primary channel.

---

## Testing

```bash
# From a machine that trusts the internal CA:
curl -X POST https://ntfy.homelab/homelab \
  -H "Title: Test Notification" -d "Hello from the homelab!"

# From the Pi (internal HTTP path — always works, no CA needed):
ssh raspberrypi.local 'curl -X POST http://127.0.0.1:2586/homelab \
  -H "Title: SSH Test" -d "Sent from the Pi itself"'
```

Your phone should receive it within seconds.

---

## Troubleshooting

### App can't connect / certificate error

The internal CA root is not installed/trusted on the phone. Install it (Prerequisite 2).
On iOS, confirm the **full-trust** toggle is on (Certificate Trust Settings), not just
the profile install.

### "Still not accessible" after a hostname/HTTPS change

If a browser was previously pinned by the old (buggy) HSTS, clear it once:
Chrome `chrome://net-internals/#hsts` → delete `ntfy.example` and `example`. The app
(OkHttp) is unaffected by browser HSTS — for the app, the fix is the CA install above.

### Notifications not arriving

```bash
ssh raspberrypi.local 'docker ps | grep ntfy'           # container up + healthy?
ssh raspberrypi.local 'docker logs ntfy --tail 50'
ssh raspberrypi.local 'curl -s http://127.0.0.1:2586/v1/health'   # {"healthy":true}
```

### Remote (Tailscale)

Confirm the Tailscale app shows Connected with the Pi online, then subscribe to the same
`https://ntfy.example` (no IP needed — split-DNS resolves it on the tailnet).

---

## Security notes

1. **No built-in auth** — UFW restricts access to LAN (`192.168.0.0/16`) and Tailscale
   (`100.64.0.0/10`). HTTPS now also encrypts traffic in transit on the LAN.
2. **Topic names are not secret** — anyone who can reach the server can subscribe. Don't
   put sensitive content in notifications without adding ntfy auth.
3. **Remote access via Tailscale**, never by exposing ntfy to the internet.

---

## Quick reference

| Action | Command / URL |
|--------|---------------|
| Subscribe (phone/web) | Server `https://ntfy.example`, topic `homelab` |
| Test notification | `curl -d "test" https://ntfy.homelab/homelab` |
| Internal test (Pi) | `ssh raspberrypi.local 'curl -d test http://127.0.0.1:2586/homelab'` |
| Health | `ssh raspberrypi.local 'curl -s http://127.0.0.1:2586/v1/health'` |
| Service status | `ssh raspberrypi.local 'docker ps \| grep ntfy'` |
| Restart | `make ntfy` |
| CA root for devices | `ansible/roles/common/files/homelab-ca.crt` |
