# 018 — Container Security Hardening

End-to-end Docker container hardening pass across every service in the homelab.
Documents what changed, why, the patterns that emerged, and the upstream bugs
discovered along the way.

**Branch:** `feat/traefik-hardening` (merged direct to `master`, no PR)
**Total commits:** ~20 hardening + fix commits
**Scope:** Every Ansible role that deploys a Docker container

## Goals

1. Drop unnecessary Linux capabilities from every container (`cap_drop: [ALL]`)
2. Read-only root filesystem with explicit `tmpfs` for writable paths
3. PID limits (fork-bomb mitigation)
4. OCI image labels (`org.opencontainers.image.*`) for inventory
5. TLS hardening for Traefik (TLS 1.2 min + Mozilla intermediate ciphers)
6. Replace `privileged: true` with targeted capability sets where possible
7. Fix latent bugs surfaced by the pass (relative volume paths, broken healthchecks)
8. Add Molecule scenarios per role to lock in the hardening as testable contracts

## Per-role changes

### Reverse proxy

#### traefik (`0dba407`)

- `cap_drop: [ALL]` + `cap_add: [NET_BIND_SERVICE]` (binds 80/443)
- `read_only: true`, `tmpfs: /tmp` (only writable path Traefik uses)
- `pids_limit: 100`
- OCI labels
- TLS hardening in `dynamic.yml`: `tls.options.default.minVersion: VersionTLS12`,
  Mozilla intermediate cipher suite list, X25519/CurveP256 curves
- Healthcheck switched from `wget /api/overview` → `traefik healthcheck --ping`
- **Cert ownership fix in `tasks/certs.yml`:** chown Tailscale certs to root:root.
  Pre-hardening the certs were uid 1000-owned and container root could read them
  via `CAP_DAC_OVERRIDE` (default cap). After `cap_drop: [ALL]` removed that cap,
  TLS load failed silently. Fix: own certs as root since `tailscale cert` runs as
  root anyway.

### Core services

#### portainer (`1e39ecc`)

- `cap_drop: [ALL]` (no caps re-added; ports >1024, docker.sock owned by root)
- `read_only: true` + tmpfs `/tmp`
- `pids_limit: 200`
- **Fixed relative volume path bug:** `./data:/data` → `{{ portainer_data_dir }}:/data`.
  The variable existed in defaults but the template wasn't using it.
- **Disabled healthcheck:** Portainer ships as scratch (no shell, no wget/curl,
  binary has no `health` subcommand). The pre-existing `wget` healthcheck has
  been silently failing since v2.x; Portainer is monitored via Beszel instead.

#### watchtower (`ec4d685`)

- `cap_drop: [ALL]`, `pids_limit: 100`, OCI labels
- Healthcheck explicit via `/watchtower --health-check` (was inherited from image)
- Molecule scenario asserts no `com.centurylinklabs.watchtower.enable=false` label
  is ever added (would prevent Watchtower from self-updating)

#### adguard (`bf549b6`)

- `cap_drop: [ALL]` + `cap_add: [NET_BIND_SERVICE]` (binds 53 + 80 inside container)
- `read_only: true` + tmpfs `/tmp`
- `pids_limit: 200`
- **Fixed relative volume paths:** `./data/work` + `./data/conf` → absolute paths
- **Deploy task: chown data dirs to root:root with recurse.** Same class of
  DAC_OVERRIDE issue as Traefik certs. AdGuard runs as root in the container and
  writes to `/opt/adguardhome/{work,conf}`. With `cap_drop: [ALL]` removing
  `CAP_DAC_OVERRIDE`, container root could not write to uid-1000-owned host dirs.
  Crashlooped on "permission denied" until ownership was matched.

#### tailscale_dns (`fb6f1e1`)

- `cap_drop: [ALL]` + cap_add `[NET_BIND_SERVICE, SETUID, SETGID, CHOWN, DAC_OVERRIDE]`
- **Did not work with just NET_BIND_SERVICE** — silent ExitCode 5 on startup until
  the full set was added. The 4km3/dnsmasq image drops to "nobody" user (SETUID/
  SETGID), reads /etc/dnsmasq.conf (DAC_OVERRIDE), and uses CHOWN during init.
- `read_only` intentionally NOT applied — dnsmasq writes to multiple paths beyond
  /tmp, and enumerating them proved fragile across image rebuilds.

### Media stack

#### media (sonarr/radarr/prowlarr/jellyfin/byparr/qbittorrent) (`6a82d59`, `74d716e`)

LinuxServer.io standard hardening pattern emerged:

- `cap_drop: [ALL]` + `cap_add: [CHOWN, DAC_OVERRIDE, FOWNER, SETGID, SETUID, KILL]`
  - These six caps are required by the s6-overlay init phase before it drops to
    `abc` (uid 1000). Without them, the container crashloops during init.
- byparr (not LSIO): `cap_drop ALL` + `cap_add [CHOWN, DAC_OVERRIDE, FOWNER,
  SETGID, SETUID, SYS_ADMIN]`. SYS_ADMIN is required for Firefox's user-namespace
  sandbox.
- Per-service `pids_limit` (sonarr/radarr/prowlarr/byparr 300, jellyfin 600,
  qbittorrent 500)
- OCI labels per service
- `read_only` intentionally NOT applied — s6-overlay writes to /run, /var/log/s6
  and several other paths during init; enumerating them via tmpfs proved
  fragile across LSIO release bumps.

**qBittorrent 5.2.0 silent-exit bug (worked around via custom-cont-init.d):**

The LSIO qBittorrent image at version `5.2.0_v2.0.12-ls454` (pulled 2026-05-09)
has a regression where the supervised run command silently exits within 1
second of every start, killed by s6-notifyoncheck because the WebUI port
never opens. The run script invokes:

```sh
s6-setuidgid abc /app/qbittorrent-nox --webui-port=8181
```

qBit silently exits even though `HOME`, `XDG_CONFIG_HOME`, and `XDG_DATA_HOME`
are all correctly set to `/config`. Adding `--profile=/config` explicitly to
the same command makes qBit pick up the existing config and bind the WebUI
port normally. Likely a qBit 5.x regression in default profile resolution
under `s6-setuidgid`.

**Fix:** new `custom-cont-init.d/01-profile-fix.sh` script (rendered into
`{{ media_config_dir }}/qbittorrent-init.d/`) bind-mounted to
`/custom-cont-init.d/01-profile-fix.sh:ro`. Runs at LSIO image init time;
sed-patches `/run/s6-rc/servicedirs/svc-qbittorrent/run` to insert
`--profile=/config` before s6 starts supervision. Fully reversible — when
LSIO ships a fix, remove the bind mount + the init script template.

**qBit v5 deprecated `WebUI\LocalHostAuth`** in favor of
`WebUI\AuthSubnetWhitelist` + `WebUI\AuthSubnetWhitelistEnabled`. Deploy task
now sets all three. Whitelist covers `127.0.0.0/8` + `172.16.0.0/12` (all
Docker bridge subnets) + `192.168.0.0/16` (LAN) + `100.64.0.0/10` (Tailscale).

#### maintainerr (`f2c2ffb`)

- `cap_drop: [ALL]` (already runs as user 1000:1000, no caps needed)
- `read_only: true` + tmpfs `/tmp`
- `pids_limit: 300`
- OCI labels

#### jellyseerr (`4a70e8e`)

- `cap_drop: [ALL]` (image runs as node user 1000 by default)
- `read_only: true` + tmpfs `/tmp`
- `pids_limit: 300`
- OCI labels

#### ntfy (`5ceb0ed`)

- `cap_drop: [ALL]` + `cap_add: [NET_BIND_SERVICE]` (ntfy binds port 80 inside)
- `read_only: true` + tmpfs `/tmp 32m`
- `pids_limit: 200`
- Data dir chown to root (same DAC pattern)

### Observability stack

#### victoriametrics, victorialogs, vmagent, grafana

All four hardened with the standard pattern (`c024dfe`, `5f294d0`, `c77279a`, `04737d6`):

- `cap_drop: [ALL]` (no caps re-added; non-privileged ports, no init phase)
- `read_only: true` + tmpfs `/tmp`
- `pids_limit: 200`
- OCI labels per service
- Grafana already runs as user 472 (no DAC fix needed)
- VM/VL/vmagent: chown data dirs to root for DAC_OVERRIDE bridge

**Healthcheck IPv6 fix (44153ed):** VM/VL/vmagent healthchecks used
`http://localhost:` but glibc inside `read_only` containers resolved `localhost`
to `::1` (IPv6) first, while the Go services bind IPv4-only. Result:
"Connection refused" on every probe even though the service worked fine.
Fixed by switching to `http://127.0.0.1:` explicitly.

#### cadvisor (`403db1d`)

- **Replaced `privileged: true` with targeted caps:**
  - `cap_drop: [ALL]` + `cap_add: [DAC_READ_SEARCH, SYS_PTRACE, SYS_ADMIN]`
  - Same minimal set used by Kubernetes deployments of cAdvisor
  - `/dev/kmsg` device kept
  - All host bind mounts (`/rootfs`, `/sys`, `/var/lib/docker`, `/var/run`)
    remain `:ro`
- `read_only: true` + tmpfs `/tmp` + `pids_limit: 200`
- Metrics collection verified intact (145 `container_cpu_*` series exported,
  real `container_memory_usage_bytes` for actual containers)

#### fluentbit (`44153ed`)

- **Disabled healthcheck.** The image is scratch/distroless: no `/bin/sh`, no
  `wget`, no `curl`. The pre-existing `CMD-SHELL`-based healthcheck never
  actually worked (image was rebuilt at some point). Fluent Bit exposes
  `/api/v1/health` on :2020 which vmagent already scrapes — alert there.

#### beszel hub + agent (`df535cf`)

- `cap_drop: [ALL]` for both services (scratch images, ports >1024)
- `pids_limit: 200` / 100
- OCI labels per service
- Data dirs chowned to root
- `network_mode: host` preserved on both

## Common patterns established

### Pattern 1: container-root vs host-uid mismatch

Most containers run as root internally. Pre-hardening, container root used
default `CAP_DAC_OVERRIDE` to bypass file-permission checks regardless of host
ownership. `cap_drop: [ALL]` removes that bypass.

Two ways to fix:

- **Chown host data dirs to root** (preferred where data is system-managed)
- **Add `cap_add: [DAC_OVERRIDE, DAC_READ_SEARCH]`** (where data must remain
  user-readable under `/srv`)

Affected roles: traefik (certs), adguard (data), ntfy (data), vmagent (data),
victoriametrics (data), victorialogs (data), beszel hub+agent (data).

### Pattern 2: linuxserver.io s6-overlay cap set

LinuxServer.io images run as root briefly to chown `/config` for PUID:PGID,
then drop to `abc` (uid 1000) via `s6-setuidgid`. The minimum cap set:

```yaml
cap_drop: [ALL]
cap_add:
  - CHOWN          # chown /config to PUID during init
  - DAC_OVERRIDE   # bypass perm checks while bootstrapping
  - FOWNER         # treat as file owner for chmod/chown
  - SETGID         # drop to PGID
  - SETUID         # drop to PUID
  - KILL           # s6 supervision signals
```

Applied to: sonarr, radarr, prowlarr, jellyfin.

### Pattern 3: read_only is not always achievable

`read_only: true` works cleanly for stateless Go binaries (Traefik, Watchtower,
ntfy, VM/VL/vmagent, Grafana, AdGuard with proper data-dir chown). It does
NOT work cleanly for:

- **s6-overlay LSIO images** — s6 writes to /run, /var/log/s6 etc. Enumerating
  the tmpfs set is fragile across release bumps.
- **dnsmasq** — writes to /run and other paths during init.
- **Some media apps** — runtime state written to non-bind-mounted paths.

For these, hardening relies on `cap_drop ALL`, non-root user, OCI labels,
pids_limit, mem_limit, and `no-new-privileges: true` instead.

### Pattern 4: IPv6 localhost gotcha under read_only

Discovered when VM/VL/vmagent healthchecks failed post-hardening: under
`read_only: true`, glibc's getaddrinfo may resolve `localhost` to `::1` (IPv6)
first. Go services that bind `:port` listen on both AF_INET and AF_INET6 by
default, but some images bind IPv4-only.

Always use `http://127.0.0.1:` in healthchecks to avoid this entirely.

## Testing — Molecule scenarios

Every hardened role has a Molecule scenario at
`ansible/roles/<role>/molecule/default/`:

- **No DinD required.** Uses the `default` driver with `managed: false` +
  local connection. Renders the role's templates into `/tmp/molecule-<role>/`
  and asserts the compose YAML contains every hardening directive.
- **Sequence:** `dependency → converge → idempotence → verify` — locks in that
  the templates are stable across re-runs and that no hardening regresses.
- **Run all scenarios at once:** `make molecule` loops over every
  `ansible/roles/*/molecule/` directory.
- **Local-only:** dev dependencies in `requirements-dev.txt` (ansible-core,
  ansible-lint, molecule, molecule-plugins[docker]).

## Lint integration

`.ansible-lint` excludes Molecule directories in both possible cwd forms
(production-profile passing):

```yaml
exclude_paths:
  - roles/*/tests/
  - roles/*/molecule/
  - ansible/roles/*/tests/
  - ansible/roles/*/molecule/
  - inventory/group_vars/all/vault.yml
  - ansible/inventory/group_vars/all/vault.yml
```

Reason: ansible-lint via the Makefile runs with cwd `ansible/`, so paths
begin with `roles/`. Pre-commit ansible-lint runs from repo root, so paths
begin with `ansible/roles/`. Both forms required to match.

## Known issues (NOT caused by this branch)

### qBittorrent 5.2.0 — AuthSubnetWhitelist broken

Even setting `WebUI\AuthSubnetWhitelist=0.0.0.0/0` returns 403 Forbidden on
`/api/v2/app/*` from any non-localhost source IP. The login API at
`/api/v2/auth/login` also returns 401 with verified-correct credentials
(the PBKDF2-SHA512 hash of the vault password was independently
recomputed and matches the stored value).

Impact: Sonarr/Radarr `Test connection` against qBittorrent fails.
Existing downloads continue to work (session cookies were cached pre-bug).

**Workaround:** manually reset qBit admin password via the Web UI, then
re-enter the new password in each *arr's Download Client settings.

### Fluent Bit healthcheck unavailable

The Fluent Bit image is scratch/distroless — no `/bin/sh`, no `wget`, no
`curl`. Container shows "Up" without health-status (Docker has no built-in
HTTP healthcheck mechanism). Monitor via Grafana on the Prometheus metrics
that vmagent already scrapes from `:2020/api/v1/metrics`.

### Portainer healthcheck unavailable

Same as Fluent Bit — scratch image with no probe binary. Container shows
"Up" without health-status. Monitor via Traefik (`portainer.example`) and
Beszel.

## Future follow-ups

1. Pin qBittorrent to a working version (`lscr.io/linuxserver/qbittorrent:4.6.7-r0-ls348`?)
   when the 5.x silent-exit + auth issues are escalating; revisit the custom-cont-init.d
   patch once LSIO ships a fix.
2. Add Molecule scenarios for the host-only roles: `common`, `security`,
   `storage`, `docker`, `tailscale`, `homelab_control`, `nfs_server`.
3. Live-DinD Molecule scenario for at least the Traefik role to catch
   runtime regressions before deploying to the Pi.
4. CI pipeline: re-enable `.github/workflows/ci.yml` (currently
   `workflow_dispatch` only due to exhausted quota) and have it run
   `make molecule` on every PR.
5. Switch read_only: false roles (LSIO/dnsmasq) to read_only +
   targeted tmpfs once we have time to enumerate the writable paths reliably.
