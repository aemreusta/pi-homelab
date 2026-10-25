# 044 — 2026-06-05: Mac worker — OrbStack networking on macOS 26 Tahoe

**Status:** resolved 2026-06-05. The Mac worker's Docker containers (on the Mac
mini via OrbStack) went dark 2026-05-31→06-05. Root cause was a **stack of five
faults**; the load-bearing two were an OrbStack **bridged-mode** networking
failure on macOS 26 (Tahoe) and an unreliable **VirtioFS-over-NFS** bind mount.
Both were fixed in the worker compose file + OrbStack config and verified
end-to-end.

## Quick navigation

| § | What | One-line |
|---|---|---|
| 1 | Symptoms | heartbeat-stale alerts, stuck backlog, status=error |
| 2 | The five faults | compose, NFS daemon, Pi API URL, **bridged mode**, **VirtioFS↔NFS** |
| 3 | The two durable fixes | `network_bridge=false` + Docker NFS volumes |
| 4 | Why each remedy that *didn't* work | reboot/reinstall/helper — and why |
| 5 | Recover from scratch | step-by-step |
| 6 | Verify end-to-end | the exact checks |

---

## 1. Symptoms

- ntfy: worker heartbeat stale → offline alerts and a backlog that stopped
  shrinking.
- The workers' status JSON files on the Pi's NFS share frozen at an old
  `status:error`.

## 2. The five faults (all had to clear)

1. **Compose invalid** — the 06-04 bind-mount migration left a bare top-level
   `volumes:` key with every entry commented = empty mapping → Compose rejects
   "volumes must be a mapping" → containers stuck `Created`.
2. **`/Volumes/homelab-generated` never mounted** — the live
   `/usr/local/bin/tdarr-nfs-mount.sh` was a stale pre-`TARGET_GENERATED`
   build; only `homelab-srv` mounted. (Moot after fault #5's fix, which stops
   using the host bind mounts — but it masked the picture during triage.)
3. **The workers' Pi API URL = Tailscale IP while Tailscale stopped** —
   100.64.0.10 unreachable; switched to the LAN IP.
4. **OrbStack bridged-mode = no VM egress (load-bearing).** `network_bridge:
   true` does not work on this LAN under macOS 26 Tahoe. The VM gets layer-2
   (host `bridge100`/`vmenet0` learn the VM's MACs) but **no NAT'd route out**,
   so every container — and the OrbStack VM itself (`--network host`) — hits
   `[Errno 113] No route to host` to the Pi **and** the internet. The
   `0.250.250.x` host helper range is unassigned and `vmnetd`/`bootpd` aren't
   running. **Crucially it survives:** Mac reboot, OrbStack auto-update
   (2.1.3→2.2.0), `brew reinstall`, `orb stop/start`, full VM teardown,
   privileged-helper kickstart. Only the network *mode* change fixes it.
5. **VirtioFS-over-NFS bind mounts flicker.** With egress fixed, the containers
   still failed to write: bind-mounting a host NFS mount (`/Volumes/homelab-*`)
   through OrbStack's VirtioFS is unreliable — the nested NFS submount appears
   as "File exists" AND "No such file or directory" at once inside the
   container, so directory creation and `status.json` writes fail
   intermittently.

## 3. The two durable fixes

**A. OrbStack NAT/shared mode (fixes egress):**

```bash
orb config get network_bridge          # was: true
orb config set network_bridge false    # shared NAT — VM egresses via host LAN
orb stop && orb start                  # mode change needs a full restart
```

This is OrbStack-local config (`~/.orbstack/`), **not** version-controlled — a
reinstall can reset it. It is documented in `mac-worker/README.md` so it's
reapplied. Trade-off: NAT mode reaches LAN/host-routable destinations, not the
Tailscale tunnel — fine here, the mac-mini is stationary and uses the Pi's LAN
IP. (If this node ever must reach a Tailscale-only address from a container,
that needs a different approach — bridged mode is not it on Tahoe.)

**B. Docker NFS volumes, not host bind mounts (fixes writes):**
the worker compose file mounts the Pi's exports **directly from inside the VM**
as Docker NFS volumes (`addr=192.0.2.100`, `nfsvers=3,nolock`) instead of
bind-mounting `/Volumes/homelab-*`. No VirtioFS↔NFS double layer.
This is the pre-06-04 design; it only needed fix A to work again.

## 4. Remedies that did NOT work (so you don't repeat them)

| Tried | Result |
|---|---|
| Mac reboot | egress still dead (bridged mode is the cause, not stale state) |
| `brew reinstall --cask orbstack` (→ 2.1.3) | no change |
| OrbStack auto-update 2.1.3→2.2.0 | no change |
| `orb stop/start`, full VM bridge teardown | `0.250.250.x` still unplumbed |
| privileged-helper kickstart / remove+reinstall | helper isn't the cause; NAT mode works without re-blessing it |
| Blanking `HTTP_PROXY` in compose | **made it worse** — the orb proxy was the only container→Tailscale route; blanking gave "No route to host" |

Lesson: when an OrbStack container has *no route to anything* (LAN **and**
internet, including `--network host`), suspect the **network mode**, not DNS,
the proxy, the helper, or stale routes.

## 5. Recover from scratch

```bash
# 1. NAT mode
orb config set network_bridge false && orb stop && orb start
# wait until: orb status = Running && docker info works

# 2. Bring up with NFS volumes (down -v drops any stale volume handles)
cd <worker compose project dir>
docker compose down -v && docker compose up -d

# 3. (Only if heartbeats must also reach the Pi via the host for other tooling)
#    ensure the host NFS mounts exist — but the containers no longer depend on
#    them; they mount NFS themselves.
```

## 6. Verify end-to-end

```bash
# a. Container reaches a Pi service over the LAN (NAT egress works)
docker exec <container> python3 -c \
 "import urllib.request as u;print(u.urlopen('http://192.0.2.100:<port>/',timeout=8).status)"

# b. Mounts read+write (no VirtioFS flicker)
docker exec <container> sh -c 'ls /data >/dev/null && touch /generated/.t && rm /generated/.t && echo OK'
```

## 7. Restoring the privileged helper (if it was removed)

The dead-ends table above is about networking *causation* — the privileged
helper (`dev.orbstack.OrbStack.privhelper`) is **not** the egress cause, and NAT
mode works without re-blessing it. But the helper should still exist in steady
state; it was deleted during this debug and must be restored the
OrbStack-sanctioned way. **Never hand-install the root LaunchDaemon.**

What it is: OrbStack's privileged-setup daemon — it creates the
`/usr/local/bin` CLI shims and the `/var/run/docker.sock` symlink. Optional and
unrelated to networking; `orbctl doctor` stays green without it.

The reliable trigger (verified 2026-06-05) is an **update** — it re-runs
privileged setup and re-blesses the helper via SMJobBless:

```bash
orbctl update    # complete the updater + approve the macOS password prompt
```

Gotchas that wasted time here:

- The GUI **Settings ▸ System ▸ "Use admin privileges"** OFF→ON toggle (the
  method in OrbStack's GitHub issues) **no-ops when the `docker.sock` symlink +
  shims already exist** — OrbStack sees no work and fires no prompt. Then
  `orbctl update` is the only trigger.
- A CLI `setup.use_admin` false→true flip (even with `orbctl stop`/`start`)
  does **not** prompt on Tahoe. Don't bounce the VM for it.
- `brew reinstall --cask orbstack` does **not** restore the helper (and may
  *downgrade* the app — it pulled 2.1.3 over the self-updated 2.2.0 here).
- The check `[ -x helper ]` is wrong in scripts: the helper is `-r-xr--r--`
  (root-only executable), so test `-f`, not `-x`.

The update preserves `network_bridge=false` and does not interrupt running
containers (verified: 2.1.3→2.2.1, helper `state = running`, doctor green).

## See also

- `mac-worker/README.md` → "OrbStack networking" + "OrbStack privileged helper"
