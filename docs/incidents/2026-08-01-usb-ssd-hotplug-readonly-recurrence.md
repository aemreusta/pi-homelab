# 2026-08-01 — Hot-plugging the second SSD took the root fs read-only in under 15 seconds; on-Pi alerting produced nothing

- **What happened:** Second attempt at attaching the 1 TB Kingston, this time hot-plugged into the Pi's **USB3** port while the system was running. Root filesystem went read-only **~15 seconds** after insertion (23:50:59), node_exporter itself died ~30 s later, and the Pi degraded to the same state as the first incident: pings fine, sshd resets every connection, containers dead. Recovered by unplugging the drive and hard power-cycling. No data loss (ext4 `clean`, 41/41 containers back).
- **Why:** Same root cause as [the boot-time incident earlier that day](2026-08-01-usb-ssd-root-dropout-readonly.md) — the Pi 4B's ~1.2 A shared USB budget. But hot-plug is **worse than boot**, the opposite of what was predicted before the attempt: at boot the system is idle, whereas hot-plugging adds the new drive's spin-up transient on top of a root SSD already serving 41 running containers. There is no ~8 minute grace window; the root device drops almost immediately.
- **Class:** Shared-resource exhaustion from an additive change (2nd occurrence, same day), plus a **new** sub-finding: *the assumption that a less-disruptive-seeming operation is lower-risk was wrong, and was stated as guidance before being tested.*
- **Occurrence:** 2nd of the USB power-budget class. 4th of the "alerting silent during a real outage" family. Critically, this is the first where **node_exporter itself died** — the first incident's survival of the exporter was, as recorded at the time, an accident and not a property to rely on.
- **Structural fix:** Nothing new to ship — the alerting from the first incident was already deployed. What it proved is the *shape* of the remaining gap (below). The working configuration found afterwards is documented in [design 042 §1b](../design/042-second-ssd-integration-plan.md).

## What this changed in our understanding

1. **The only thing that detected this was the off-Pi watchdog, via its scrape-failure branch — not a metric value.** Every on-Pi alert shipped hours earlier (`RootFilesystemReadOnly`, `HostStorageLinkDropout`, …) produced nothing, because vmalert, Alertmanager, ntfy *and this time node_exporter* were all on the failed disk. The follow-up note written after incident #1 said the watchdog "must treat scrape failure itself as the alert, not just a bad value". That prediction was confirmed within four hours. It raises the off-Pi watchdog from P0-on-paper to P0-demonstrated.

2. **Hot-plug is the higher-risk operation, not the lower-risk one.** Recorded because the opposite was asserted to the operator beforehand.

3. **journald again persisted nothing.** `journalctl -k -b -1` for the failed boot ends at normal startup messages — no disconnect, no ext4 error. Third confirmation that the `dmesg`-sourced scanner was the right design choice, and that the evidence for a root-fs failure cannot live on the root fs.

## Resolution: USB 2.0 port (verified working)

The operator then moved the Kingston to a **USB 2.0** port. Both disks have coexisted cleanly since:

- Kingston enumerates on `usb 1-1.3` at 480M via the internal hub, driver `usb-storage` (not `uas`), attached as `sdb` at 932 GiB. Root SanDisk stays alone on the USB3 controller as `sda`.
- Clean boot with **both** drives spinning up — no `USB disconnect`, no `Read Capacity failed`, no 0-byte enumeration. `homelab_kernel_io_events` all zero.
- **Concurrent-read load test:** 3.1 GB from the Kingston while the root SSD read 4 GB simultaneously → **34.5 MB/s**, 91 s, zero faults, root still `rw`, `throttled=0x0`, 41/41 containers healthy.

Mechanism: USB 2.0 caps device draw at **500 mA** versus USB 3.0's **900 mA**, roughly halving the Kingston's demand and bringing the pair under the Pi's budget. The cost is bandwidth — ~34.5 MB/s versus ~400 MB/s on USB3. Acceptable for backups (5.3 GiB weekly ≈ 2.5 min) and workable for media serving; unsuitable for a transcode cache.
