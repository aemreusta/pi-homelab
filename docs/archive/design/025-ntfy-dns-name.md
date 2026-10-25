# 025 — ntfy.example: single hostname over split-DNS

Status: shipped 2026-05-18.

## Why this exists

Every host-side alert script (`memory-alert.sh`, `disk-alert.sh`,
`docker-prune.sh`, `daily-report.sh`, `junk-scan.sh`) and the two shoutrrr
URLs (`watchtower`, `beszel`) used to construct the ntfy URL from
`ansible_default_ipv4.address`. That produced eight independent URLs all
pointing at the LAN IP `192.0.2.100:2586`.

Two operational pains followed:

1. **The mobile ntfy app had to be reconfigured** between
   `192.0.2.100:2586` (LAN) and `100.64.0.10:2586` (Tailscale) every
   time the user switched networks.
2. **Drift risk:** the IP appears in eight files. A router-side static
   reservation change or a Pi NIC swap would break alerts in eight places.

The fix is to point everything at the hostname `ntfy.example` and let
the existing split-DNS infrastructure pick the right IP per network:

| Origin                              | Resolver                              | Result            |
|-------------------------------------|---------------------------------------|-------------------|
| Devices on the LAN                  | AdGuard wildcard `*.example`          | `192.0.2.100`   |
| Devices on Tailscale                | dnsmasq `address=/homelab/…`          | `100.64.0.10`    |
| The Pi itself                       | `/etc/hosts` pin (this change)        | `192.0.2.100`   |

The Pi-side `/etc/hosts` pin is the load-bearing piece — see CR1 below.

## CR1 — why `/etc/hosts` is non-optional

The Pi's `/etc/resolv.conf` (from
`ansible/roles/common/tasks/dns.yml`) is:

```text
nameserver 127.0.0.1   # AdGuard (in Docker)
nameserver 1.1.1.1     # public fallback
nameserver 9.9.9.9     # public fallback
```

If AdGuard is down, lookups for `ntfy.example` fall through to
`1.1.1.1`, which returns `NXDOMAIN` (it's a local-only name). That
breaks the alerts at exactly the moment they're most needed —
AdGuard's container is one of the things most likely to be OOM-killed
under memory pressure, so the memory-alert that should fire never
reaches ntfy.

Pinning `ntfy.example → 192.0.2.100` in `/etc/hosts` removes the
AdGuard dependency for loopback delivery. `nsswitch.conf`'s default
order is `files dns`, so `/etc/hosts` wins before any resolver is
consulted.

Implementation: `ansible/roles/common/tasks/hosts.yml` (idempotent
`lineinfile`), wired into `common/tasks/main.yml` with tag `[common, hosts]`.

### Important nuance — Docker bridge containers don't inherit the host pin

Docker's embedded DNS resolver (`127.0.0.11`, served to every bridge-
network container) does **not** read the host's `/etc/hosts`. It only
forwards to the daemon's upstream resolvers, which on the Pi means
`127.0.0.1` (AdGuard) → public fallbacks. So the host-side pin
protects host scripts but **not** containers that use bridge networking
and call ntfy by hostname (watchtower).

beszel sidesteps the issue: it runs `network_mode: host`, so it
shares the Pi's `/etc/hosts` directly.

watchtower runs on bridge networking, so its compose template adds:

```yaml
extra_hosts:
  - "ntfy.example:{{ lan_ip }}"
```

`extra_hosts` is written into the container's own `/etc/hosts` at
start time, consulted by glibc/musl before the resolver chain — same
mechanism as the host-side pin, scoped per-container.

> If you ever move beszel off `network_mode: host`, add the same
> `extra_hosts` entry there. The verify playbook hard-asserts the
> network mode so the regression won't go unnoticed.

## URL pattern map

The codebase has three distinct patterns; only Pattern 1 was changed.

| Pattern | Used by                                                   | Form                                  | Touched? |
|---------|-----------------------------------------------------------|---------------------------------------|----------|
| 1       | Host scripts + shoutrrr (8 files)                         | `http://ntfy.example:2586/<topic>`    | **Yes**  |
| 2       | Containers with `network_mode: host` or running on the Pi | `http://localhost:2586/<topic>`       | No       |
| 3       | Containers on `media_net` (qbit and other media apps)     | `http://ntfy:80/<topic>`              | No       |

Pattern 2 must stay on `localhost` (host-mode containers see the Pi's
own loopback). Pattern 3 must stay on `ntfy:80` (Docker DNS via
service name — no host network involved, no port mapping).

## Centralized variables

Defined once in `ansible/inventory/group_vars/all/main.yml`:

```yaml
lan_ip: "192.0.2.100"
ntfy_port: 2586
ntfy_topic: homelab
ntfy_hostname: ntfy.example
ntfy_external_base_url: "http://{{ ntfy_hostname }}:{{ ntfy_port }}"
ntfy_external_topic_url: "{{ ntfy_external_base_url }}/{{ ntfy_topic }}"
ntfy_shoutrrr_url: "ntfy://{{ ntfy_hostname }}:{{ ntfy_port }}/{{ ntfy_topic }}?scheme=http"
```

Every Pattern 1 file references one of these — no IP construction
anywhere in the role tree.

## Verification

`make verify-ntfy` runs `playbooks/verify-ntfy.yml`, which asserts:

1. `getent hosts ntfy.example` on the Pi returns `192.0.2.100`
   (proves `/etc/hosts` pin wins over the resolver chain).
2. `GET {{ ntfy_external_base_url }}/v1/health` returns
   `{"healthy": true}`.
3. `docker inspect watchtower` reports
   `ntfy.example:192.0.2.100` in `HostConfig.ExtraHosts` —
   guarantees the container's `/etc/hosts` survives an AdGuard outage.
4. `docker inspect beszel` reports `NetworkMode = host` — confirms it
   inherits the Pi's `/etc/hosts` directly (the same way host scripts do).

Scratch images (watchtower, beszel) have no `getent`, no shell — we
can't exec into them — so we read state via `docker inspect` instead.

`make validate` runs `--syntax-check` on the playbook so it can't
silently rot.

### Manual end-to-end check (on LAN and Tailscale)

```bash
# Resolution
dig +short ntfy.example @192.0.2.100   # → 192.0.2.100 (LAN)
dig +short ntfy.example @100.64.0.10    # → 100.64.0.10 (Tailscale)

# Health
curl -s http://ntfy.example:2586/v1/health
# → {"healthy":true}

# Smoke test
curl -sf -d "verification from $(hostname)" \
  http://ntfy.example:2586/homelab
```

## Mobile app

Each subscribed device must be re-pointed once:

1. Open the ntfy app.
2. Delete the old subscription (`192.0.2.100:2586/homelab` or
   `100.64.0.10:2586/homelab`).
3. Add a new subscription: server `http://ntfy.example:2586`, topic
   `homelab`.

During the transition the old IP-based subscription keeps receiving
notifications (ntfy doesn't validate Host header), but action buttons
inside delivered notifications already use the new hostname — so taps
work only on devices that can resolve `ntfy.example`.

iOS push uses APNs as the wake-up signal and then fetches the message
body from the configured server. Split-DNS handles that fetch the same
way the foreground app does, so no extra config is needed beyond
re-subscribing.

## Phase 2 — Traefik HTTPS (SHIPPED 2026-06-06)

> **Done** via an internal CA, not Let's Encrypt (`.example` can't get public
> certs). See [design 033](../../design/033-internal-ca-https.md). Implemented slightly
> differently from the sketch below: the `:2586` host port was **kept** (internal
> senders + Traefik backend still use it) rather than dropped, and a new
> `ntfy_public_url` (`https://ntfy.example`) drives `NTFY_BASE_URL` while the
> `ntfy_external_*` vars stay on HTTP `:2586` for the ~30 internal senders. ntfy
> runs `NTFY_BEHIND_PROXY=true` behind a Traefik websecure router.

Original sketch (for history):

The variable `ntfy_external_base_url` lives in `group_vars/all` so a
future migration to `https://ntfy.example` (Traefik + Let's Encrypt)
is one line. Action buttons in already-delivered notifications would
still point at the old URL until those notifications expire, but new
notifications would use the new value immediately.

When picking this up:

1. Add a Traefik HTTP→HTTPS redirect router for `ntfy.example`.
2. Drop the `2586:80` host port on ntfy (proxy through Traefik
   instead).
3. Change `ntfy_external_base_url` to `https://ntfy.example` and
   drop `ntfy_shoutrrr_url`'s `?scheme=http` query.
4. `make ntfy && make watchtower && make beszel && make base &&
   make homelab-control && make verify-ntfy`.

## Touched files

```text
ansible/inventory/group_vars/all/main.yml       (+ variables)
ansible/roles/common/tasks/main.yml             (+ include hosts.yml)
ansible/roles/common/tasks/hosts.yml            (new — /etc/hosts pin)
ansible/roles/common/templates/memory-alert.sh.j2
ansible/roles/common/templates/disk-alert.sh.j2
ansible/roles/common/templates/docker-prune.sh.j2
ansible/roles/homelab_control/defaults/main.yml
ansible/roles/homelab_control/templates/junk-scan.sh.j2
ansible/roles/ntfy/defaults/main.yml            (drop dup vars)
ansible/roles/ntfy/templates/docker-compose.yml.j2
ansible/roles/watchtower/defaults/main.yml
ansible/roles/beszel/defaults/main.yml
ansible/roles/watchtower/molecule/default/converge.yml
ansible/roles/beszel/molecule/default/converge.yml
ansible/playbooks/verify-ntfy.yml               (new — assertions)
Makefile                                        (+ verify-ntfy target)
```

## Deploy order (one-shot)

```bash
make base               # /etc/hosts pin + host scripts
make ntfy               # NTFY_BASE_URL → new hostname (action URLs)
make homelab-control    # junk-scan.sh + daily-report.sh
make watchtower         # shoutrrr URL
make beszel             # shoutrrr URL
make verify-ntfy        # hard assertions on the result
```
