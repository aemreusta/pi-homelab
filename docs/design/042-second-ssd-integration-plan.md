# 042 — Integrating the second SSD (Kingston XS1000, 1 TB)

**Status:** **BLOCKED on a powered USB3 hub.** All four attempts to run the Kingston
directly off the Pi failed — three on USB 3.0, and one on USB 2.0 after two clean
boots had wrongly been read as a resolution (see the correction in §1b). Until a
powered hub is in place the drive should stay **physically unplugged**: it is
unformatted and unused, so leaving it attached is pure risk against the 1.7 T root
SSD for zero benefit. §2 onward (format, fstab, capacity phases) is designed and
ready, and starts the day the hub arrives.
**Date:** 2026-08-01
**Trigger:** [incident 2026-08-01](../incidents/2026-08-01-usb-ssd-root-dropout-readonly.md) — the first attempt took the whole homelab down for ~15 minutes.

---

## 0. Read this first

The drive is fine. The Pi's USB power budget is not. Attaching a second
bus-powered USB3 SSD to a Pi 4B **degrades the drive you already depend on** —
the root SSD browns out during concurrent spin-up, is registered as a 0-byte
disk, and takes the root filesystem read-only. Nothing about the Kingston is at
fault.

**The answer is §1: a powered USB3 hub.** Four direct-attach attempts failed —
three on USB 3.0 and one on USB 2.0. USB 2.0 measurably reduces the failure rate
but does not eliminate it (§1b). The generalised hardware rule, the full
scoreboard, the research, and what does *not* fix it live in
[docs/conventions/pi4-usb-power-budget.md](../conventions/pi4-usb-power-budget.md).

**Until the hub arrives, keep the Kingston unplugged.** It is unformatted and
unused, so an attached drive is pure risk against the root SSD for zero benefit.

Sections 1a and 1b are the record of what was tried and why it failed — read them
for *why*, not for what to do.

---

## 1a. HISTORICAL — operator decision: direct USB3, no hub (2026-08-01, failed twice)

The operator chose to reattach the Kingston directly rather than buy a powered hub.
Kept as the record of that decision and its outcome. Note item 1 below: the
three-clean-reboot bar was set here, and then not honoured — see the §1b correction.

**What the risk actually is.** The same failure can recur: root SSD brownout
during concurrent spin-up, 0-byte enumeration, read-only root. It is most likely
**at boot**, when both drives spin up together — not during steady-state use,
where the Kingston is idle and drawing little. So the exposure is concentrated in
reboots, which are infrequent and operator-initiated.

**What now guards it (this is the material change since the first attempt).**
The failure is no longer silent. Shipped and drill-verified 2026-08-01:

- `HostStorageLinkDropout` — fires on a **single** `USB disconnect` or
  over-current. This is the line the kernel logged during the incident.
- `HostStorageFaultsFatal` — fires on `disk_zero_capacity`, the 0-byte
  enumeration signature (scored 4 on the real incident log).
- `RootFilesystemReadOnly` — the terminal state, read from node_exporter's
  in-memory `node_filesystem_readonly`, which survives a read-only root.

The first attempt cost ~15 minutes of undetected downtime. A recurrence should
now page within ~2–3 minutes of the first disconnect, usually **before** the
filesystem gives up.

**Operating rules under this decision:**

1. **Watch the first three reboots.** After each, check
   `curl -s <pi>:9100/metrics | grep homelab_kernel_io_events | grep -v ' 0$'`.
   Non-empty means the brownout is recurring.
2. **If `HostStorageLinkDropout` fires and nobody touched the hardware — unplug
   the Kingston.** Do not wait to see whether it recovers; the incident showed
   an ~8 minute gap between the first disconnect and the read-only root. That
   gap is the entire budget.
3. **Do not put anything on the Kingston that the Pi depends on to boot or to
   alert.** In particular §3 Step A (backups) is still fine, but the drive must
   stay `nofail` in fstab so a dropout cannot block boot.
4. **Reduce concurrent spin-up where possible** — if the Kingston is only needed
   for weekly backups, leaving it physically detached between runs removes the
   boot-time contention entirely and is the cheapest mitigation available
   without buying hardware.
5. If dropouts recur, the decision should be revisited: a powered hub or a single
   larger root SSD (§1) are the only real fixes. Alerting makes the failure
   visible; it does not make it stop happening.

---

## 1. THE ANSWER — powered USB3 hub (blocking; the only option left after four failures)

Get a **powered (self-powered / externally-powered) USB 3.0+ hub** — one with
its own DC adapter, not a bus-powered pass-through. The Kingston goes on the
hub; the hub supplies its spin-up current instead of the Pi.

Acceptance test before anything else, in order:

1. Hub plugged into mains **and** into the Pi, Kingston on the hub, Pi powered off.
2. Boot the Pi. Wait 5 minutes for the container fleet to settle.
3. `findmnt -no SOURCE,OPTIONS /` → must show `rw`, and `lsblk` must show the
   root SSD at its full 1.8 T (not 0 B).
4. `curl -s localhost:9100/metrics | grep homelab_kernel_io_events | grep -v ' 0$'`
   → must be empty apart from the timestamp line.
5. Repeat the reboot **three times**. Spin-up contention is a race; one clean
   boot proves nothing.

If any reboot trips `disk_zero_capacity` or `usb_disconnect`, stop. The hub is
inadequate or the topology is wrong. The alerts shipped today exist exactly so
this is visible in seconds instead of after an outage.

**Alternative if you'd rather not add a hub:** don't run two drives. The
capacity problem (§3) can also be solved by replacing the 1.8 T root SSD with a
larger single drive — one device, no contention, no new failure mode. Slower to
execute (full clone) but architecturally cleaner.

---

## 1b. USB 2.0 — helps, but does NOT fix it (2026-08-01, corrected 2026-08-02)

> **CORRECTION.** This section originally read "THE WORKING ANSWER". It was written
> after two clean cold boots and a passing load test, and it declared success before
> reaching its own stated bar of three clean boots. The third boot failed: Kingston
> physically confirmed on the black port, `lsusb -t` showing `480M / usb-storage`,
> root filesystem read-only ~10 minutes in. Worse, that failure came in **steady
> state** with a completely clean boot log — no disconnect, no 0-byte enumeration —
> so the USB 2.0 current cap did not even produce a warning window. The corrected
> conclusion and the full scoreboard are in
> [docs/conventions/pi4-usb-power-budget.md](../conventions/pi4-usb-power-budget.md).
> **Everything below is accurate about what USB 2.0 does; it is simply not
> sufficient.**

After two failures on USB3 (boot: read-only after ~8 min; hot-plug: read-only in
**~15 s**), the drive was moved to a **USB 2.0** port. It survived two cold boots
and a load test, then failed on the third boot. Here is what it does and does not
buy you.

**Why it helps.** USB 2.0 caps device current at **500 mA**; USB 3.0 permits
**900 mA**. Forcing the Kingston to negotiate USB 2.0 nearly halves its draw and
brings the pair under the Pi 4B's ~1.2 A shared budget. The kernel also binds it
with `usb-storage` rather than `uas`, which queues less aggressively. The root
SanDisk keeps the USB3 controller to itself.

**Verified 2026-08-01:**

- Kingston enumerates on `usb 1-1.3` @ 480M, `usb-storage`, `sdb`, 932 GiB;
  root SanDisk alone on bus 2 as `sda`.
- Clean boot with **both** drives spinning up simultaneously — the exact
  condition that failed the first time. No disconnect, no `Read Capacity failed`,
  no 0-byte enumeration; `homelab_kernel_io_events` all zero.
- Concurrent-read load test (Kingston 3.1 GB + root 4 GB at the same time):
  **34.5 MB/s**, 91 s, zero faults, root `rw`, `throttled=0x0`, 41/41 containers.

**The cost is bandwidth: ~34.5 MB/s instead of ~400 MB/s.** What that means:

| Workload | At 34.5 MB/s | Verdict |
|---|---|---|
| Weekly backup (5.3 GiB) | ~2.5 min | fine |
| One-time media+downloads migration (106 GiB) | ~50 min | fine |
| Jellyfin direct play (~0.6–3 MB/s per stream) | many streams | fine |
| Transcode cache | bottleneck | **do not** |

See §3 for the full capacity plan. Do not put the transcode cache, PostgreSQL,
SQLite or any latency-sensitive scratch on this drive.

**Confirmation status: FAILED.** Two clean cold boots on USB 2.0 (2026-08-01 and
2026-08-02 03:02) plus a passing concurrent-read load test, then the third boot went
read-only ~10 minutes in with the drive verified on the black port. A passing load
test does not establish stability here — only elapsed time does.

**Warning learned the hard way (2026-08-02):** the drive was moved back to a blue
USB3 port during re-cabling and the failure recurred immediately — read-only root
within ~8 minutes of boot. **After any re-cabling, verify the port** before
trusting the machine:

```bash
lsusb -t    # Kingston MUST read 480M / usb-storage, NOT 5000M / uas
```

A powered hub (§1) remains the answer if USB 2.0 bandwidth ever becomes the
constraint.

---

## 2. Filesystem prep (after one more clean reboot)

The drive currently holds one **empty** NTFS partition (128 MB, just
`System Volume Information`). Nothing to preserve — confirmed by read-only mount
on 2026-08-01.

```bash
# Destructive. Confirm the device letter FIRST — it will NOT be sda now.
lsblk -o NAME,SIZE,MODEL,TRAN          # find the 931.5G Kingston XS1000
sudo wipefs -a /dev/<dev>
sudo mkfs.ext4 -L homelab-ssd2 -m 0 /dev/<dev>
```

Notes:

- **ext4, not NTFS.** ntfs-3g is FUSE: slow, CPU-hungry on a Pi, and it cannot
  express the PUID/PGID 1000 ownership model every container depends on.
- **`-m 0`** — skip the 5% root reserve. This is a data disk, not a root fs;
  5% of 931 GiB is 46 GiB thrown away.
- **No partition table needed** (whole-device fs is fine), but partitioning is
  also fine — just be consistent with what fstab references.
- **Mount by UUID in `/etc/fstab`, never by `/dev/sdX`.** This is precisely what
  saved the recovery on 2026-08-01: root is `UUID=78ea8134-…`, so the
  `sdb`→`sdc` re-enumeration was survivable. Add `nofail` so a missing second
  drive can never block boot.

---

## 3. What goes on it — the capacity plan

**Operator intent (2026-08-02):** the Kingston is a **capacity expansion for
`media`**, not a backup-only disk. Backups may move there too.
Anything speed-sensitive — PostgreSQL, the Docker data root, SQLite databases,
the transcode cache — **stays on the SanDisk**.

Current state: `/` is **97% full — 1.7 T used, ~55 GiB free**, past the
`SrvDiskCritical` 95% threshold.

| Path | Size | Destination |
|---|---|---|
| `/srv/media` | 60 G | → Kingston |
| `/srv/downloads` | 46 G | → Kingston (must move *with* media) |
| `/srv/backups` | 5.3 G | → Kingston |
| `/srv/docker` | 16 G | stays — Docker data root |
| `/srv/data` | 9 G | stays |
| `/srv/transcode-cache` | ~0 | **stays** — burst scratch, USB2 would bottleneck it |
| `/home/pi/docker/**` (Postgres, SQLite, configs) | **235 G** | mostly **stays** |

> **The table above understated `/home/pi/docker` as "—" until 2026-08-02.** It is
> **235 G**, and it was invisible to this plan.

### The constraints that shape this

1. **`/srv/downloads` and `/srv/media` are welded together.**
   `storage/tasks/main.yml:30` asserts they share one `stat.dev`, because
   Sonarr/Radarr hardlink between them. Move both or neither — moving one alone
   fails `make base` by design.
2. **A new mount is invisible to alerting until the rules change.** Every disk
   rule in `host-saturation.yml.j2` hardcodes `mountpoint="/"`. node_exporter
   *will* export the new filesystem; no rule selects it. Ship the rule change in
   the **same commit** as the mount, or you get a second disk that silently fills.

### Phases — each one validates the disk further before it is trusted with more

**Phase 1 — format, mount, alerting.** No data moves. §2 for the commands.
fstab by **UUID** with **`nofail`**. Extend the disk alert rules to the new
mountpoint in the same commit. Reboot and confirm both drives enumerate clean.

**Phase 2 — backups (5.3 G).** Repoint `backup_root`. Small, reversible, and it is
the first *sustained write* the drive sees — a real load test with a trivial blast
radius. Also fixes a standing weakness: backups currently live on the same physical
device as the data they protect. Recalibrate `backup_min_free_gb` /
`backup_warn_free_gb`, which are measured against `backup_root`'s filesystem and are
tuned today for a nearly-full disk. Expect ~2.5 min per run at 34.5 MB/s.

**Phase 3 — media + downloads (106 G, together).** The step that actually relieves
root pressure: `/` goes from ~55 GiB free to **~161 GiB**, and media gets ~800 GiB
of growth room. Budget **~50–60 minutes** for the copy. Use `rsync -aHAX` — the
`-H` is load-bearing, without it the Sonarr/Radarr hardlinks between `downloads`
and `media` expand into full copies and 106 GiB becomes far more.

### Speed sanity check at 34.5 MB/s

| Workload | Reality |
|---|---|
| Jellyfin direct play (~2.5–5 MB/s per stream) | ~7–13 concurrent streams — fine |
| qBittorrent writes (100 Mbps line ≈ 12.5 MB/s) | fine |
| Weekly backup (5.3 GiB) | ~2.5 min |
| Phase 3 one-time migration (106 GiB) | ~50–60 min |
| Transcode cache, Postgres, SQLite | **do not put them here** |

---

## 4. Rollback

Every phase is reversible while the source data is still in place: mount the new
drive, `rsync -aHAX` into it, verify, then repoint. **Do not delete the source until
the new layout has survived a reboot and a full backup cycle.** `rsync -H` matters —
see Phase 3.

---

## 5. Checklist

- [x] Kingston on a **USB 2.0** (black) port — verified `480M` / `usb-storage` in `lsusb -t`
- [x] Clean boot with both drives, `homelab_kernel_io_events` all zero
- [x] Concurrent-read load test — 34.5 MB/s, zero faults
- [ ] One more clean reboot before Phase 1 (spin-up is a race; two so far)
- [ ] **Phase 1** — `wipefs` + `mkfs.ext4 -L homelab-ssd2 -m 0`; fstab by UUID + `nofail`
- [ ] **Phase 1** — disk alert rules cover the new mountpoint (same commit)
- [ ] **Phase 1** — `make validate` green; storage hardlink assert still passes
- [ ] **Phase 2** — `backup_root` repointed, free-space gates recalibrated
- [ ] **Phase 3** — media + downloads moved together with `rsync -aHAX`
- [ ] **Phase 3** — containers **recreated** (not just restarted) after the mount change
