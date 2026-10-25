# Mac Worker — On-Demand Compute Offload

Docker-based helpers that run on a Mac to handle work the Pi can't do
efficiently. The Mac is a laptop on battery and Wi-Fi, so sleep is a normal
state: the Pi probes it every minute (`homelab-agent mac-presence`) and
inhibits every Mac-dependent alert while it is away.

## Services

| Component | Purpose | Port |
|-----------|---------|------|
| `homelab-byparr` (`docker-compose.yml`) | Cloudflare-bypass proxy for the **Pi's Prowlarr** (FlareSolverr drop-in) | 8191 |
| `github-runner/` | Ephemeral, single-job self-hosted GitHub Actions runner | — |
| NFS mount supervisor | Keeps the Pi's `/srv` and shared status exports mounted on the host | — |

> **byparr** was moved here from the Pi on 2026-07-13. On the Pi 4B, Camoufox/
> Playwright leaked ~130 PIDs per request and could not solve a Cloudflare
> challenge inside Prowlarr's proxy timeout; on this Mac the same solve takes
> ~3 s. The Pi's Prowlarr FlareSolverr indexer-proxy points at
> `http://192.0.2.102:8191` (this Mac's LAN IP — a DHCP address; if the lease
> changes, update the proxy host + Homepage link). Stateless — no NFS volumes.

<!-- -->

> **GitHub Actions runner** lives in its own subdir, `github-runner/` — a
> containerized, ephemeral, single-job self-hosted runner that serves the homelab
> repos. Separate compose + LaunchAgent;
> see [`github-runner/README.md`](github-runner/README.md) and
> [`docs/design/039-mac-ci-runner.md`](../docs/design/039-mac-ci-runner.md). Install:
> `bash github-runner/install.sh`.

Every compose project is re-asserted by `ensure-compose.sh` from a LaunchAgent
at login and on a 15-minute interval, so a sleep, an OrbStack restart or a
not-yet-reachable NFS export at wake-up never leaves a project down for long.

## Prerequisites

- OrbStack running on the Mac
- Pi's NFS server deployed (`make nfs-server` from the ansible repo)

## OrbStack networking — REQUIRED setting (`network_bridge = false`)

> **If containers show `[Errno 113] No route to host` to the Pi, this is almost
> certainly it.** Full incident + history: runbook
> `docs/runbooks/044-2026-06-05-mac-worker-orbstack-networking.md`.

OrbStack **must** run in **shared NAT mode**, not bridged mode:

```bash
orb config get network_bridge      # must be: false
orb config set network_bridge false && orb stop && orb start
```

On macOS 26 (Tahoe), OrbStack **bridged mode (`network_bridge: true`) gives the
VM no working egress** — containers (and even `--network host`) get
`No route to host` to the LAN *and* the internet, while the host itself is
fine. This survives reboot, `brew reinstall`, OrbStack updates, and VM
teardown — only switching to NAT mode fixes it. This setting lives in
`~/.orbstack/` (**not** version-controlled), so **reapply it after any OrbStack
reinstall**. Trade-off: NAT mode reaches LAN/host-routable hosts, not the
Tailscale tunnel — fine for this stationary node, which talks to the Pi's LAN
IP. (`8.8.8.8:80` timing out is normal — it doesn't serve HTTP; test against
the Pi.)

## NFS volumes — Docker NFS volumes (NOT host bind mounts)

Containers that need the Pi's exports mount them **directly from inside the
OrbStack VM** via Docker `nfs` volumes (`addr=192.0.2.100`,
`nfsvers=3,nolock`) — e.g. the runner's `nfs-generated` volume, which maps the
Pi's shared Mac-worker status directory to `/generated`.

**Why not bind-mount `/Volumes/homelab-*`?** Bind-mounting a *host* NFS mount
through OrbStack's VirtioFS is unreliable — the nested NFS submount flickers
between "File exists" and "No such file or directory" inside the container, so
writes (`mkdir`, `status.json`) fail intermittently (2026-06-05). Mounting NFS
directly from the VM removes the VirtioFS↔NFS double layer. This requires the
VM to have a route to the Pi — i.e. NAT mode above. After changing volume
options: `docker compose down -v && docker compose up -d` (the `-v` drops the
cached NFS volume).

### `/Volumes/homelab-srv` host mount (for non-container tooling)

The `tdarr-nfs-mount.sh` daemon (at `/usr/local/bin/`, supervised by
LaunchDaemon `io.tdarr.nfsmount`, named after its first consumer, a native
Tdarr node) mounts `/Volumes/homelab-srv` + `/Volumes/homelab-generated` on
the **host**. Its Python supervisor checks every 30 seconds, using
`/usr/bin/nfsstat` and bounded sentinel reads in each export. A missing helper
or sentinel is reported as unknown. Two consecutive hung checks permit
recovery, with a five minute cooldown; healthy mounts stay in place even when
LAN becomes available. New mounts prefer `192.0.2.100:2049`, then
`100.64.0.10:2049`. Pi-side exports allow both `192.0.2.0/24` and
`100.64.0.0/10` (`ansible/roles/nfs_server/defaults/main.yml`).

Sources of truth (versioned in this directory):

- `tdarr-nfs-mount.sh` — stable launchd entrypoint
- `nfs_mount.py` — tested health checks, bounded commands and recovery decisions
- `io.tdarr.nfsmount.plist` — the LaunchDaemon
- `install-nfs-mount.sh` — installer/activator (preconditioned, with rollback)
- `check-nfs-permissions.sh` — read-only check of the macOS privacy gate below

To install or refresh:

```bash
# Pi prerequisites, after syntax + --check --diff verification:
cd ansible
ansible-playbook playbooks/nfs-client-health.yml
cd ..
sudo bash mac-worker/install-nfs-mount.sh
# Read-only verification, also usable without root:
/usr/bin/python3 mac-worker/nfs_mount.py --check
```

The installer validates all helpers before activation, preserves healthy mounts,
backs up installed files, and restores the prior daemon on activation failure.
Its success condition is bounded reads from **both** shares. A verification
timeout exits nonzero and leaves the new supervisor running to retry.

The root supervisor manages mounts; its read/publish children use the worker
account. Local state is `/var/run/homelab-nfs.json`; launchd logs go to
`/var/log/tdarr-nfs-mount.log`. A bounded publish to the shared status export
lets the Pi collect `mac_nfs_*` gauges every two minutes. Missing reports,
stopped collectors and failing reads have separate vmalert signals. If the
shared export also fails, the report becomes stale rather than falsely
remaining healthy. These sentinels prove the export can serve a small read;
verify the affected workload before declaring it recovered.

**2026-09-25 limitation:** macOS currently denies background Python access to
Network Volumes even after the child drops to the worker UID. Interactive reads
succeed, but that does not authorize launchd's Python process. `read_denied` is
unknown, never a reason to cycle healthy mounts. Completing supervisor
acceptance requires macOS privacy authorization for its process identity and
fresh successful reads/publication from launchd itself. Reinstalling, repeated
remounts, or changing the TCC database does not satisfy that gate. See
[runbook 053](../docs/runbooks/053-2026-09-26-mac-nfs-supervisor-permissions.md).
