# Pi 4B USB power budget — a second bus-powered drive needs a powered hub

**Earned:** 2026-08-01/02, **five** outages in ~12 hours.
**Rule:** On this Pi, do not run a second bus-powered USB drive at all without a
**powered** hub. USB 2.0 reduces the failure rate but does **not** eliminate it.
**Status 2026-08-02: rolled back.** The drive is off the Pi; `storage_ssd2_enabled`
is `false`, which actively tears down the mount and removes its fstab entries.
Durable backup coverage moved to Google Drive (rclone) instead of a second disk.

> **CORRECTION (2026-08-02 03:23).** An earlier version of this document said the
> USB 2.0 port *was* the answer, on the strength of two clean cold boots and a
> concurrent-read load test. That conclusion was published before meeting its own
> stated acceptance criterion (three clean boots) and it was **wrong**. On the
> third boot — Kingston physically confirmed on the black port, `lsusb -t` showing
> `480M / usb-storage` — the root filesystem went read-only ~10 minutes in.
> Read §"Why USB 2.0 is not enough" below before acting on anything here.

---

## The constraint

The [official Raspberry Pi documentation](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/raspberry-pi/usb-bus-on-raspberry-pi.adoc)
states that Pi 2/3/4 supply **1200 mA total across all USB ports**. That ceiling is
enforced by the board's over-current protection hardware. It is not a kernel
parameter, not a config.txt setting, and **not something a bigger PSU changes** —
the Pi 5's `usb_max_current_enable` has no Pi 4B equivalent.

Against that budget:

| Link | Max device draw (spec) |
|---|---|
| USB 3.0 | **900 mA** |
| USB 2.0 | **500 mA** |

Two bus-powered USB3 SSDs negotiating 900 mA each exceed 1200 mA before either has
finished spinning up. Forcing one of them to USB 2.0 caps it at 500 mA and brings
the pair under the ceiling. That is the whole mechanism.

## What it looks like when you get it wrong

**The drive that fails is the one you already depend on, not the one you just
plugged in.** The root SSD browns out, drops off the bus, and re-enumerates:

```text
usb 2-1: USB disconnect, device number 2          ← the ROOT disk, not the new one
sd 0:0:0:0: [sdb] Read Capacity(16) failed: Result: hostbyte=0x07
sd 0:0:0:0: [sdb] 0 512-byte logical blocks: (0 B/0 B)    ← registered as a 0-byte disk
```

Then `/` is still mounted on a device node that no longer exists, ext4 honours
`errors=remount-ro`, and the host goes read-only. Symptom triad: **pings fine, sshd
resets every connection, load near zero.** The device letter changes (`sdb` → `sdc`),
which is survivable only because `/etc/fstab` mounts root by UUID.

Timing depends on when you do it:

| When | Grace period before read-only |
|---|---|
| Cold boot (both drives spin up together) | ~8 minutes |
| **Hot-plug while the system is running** | **~15 seconds** |

Hot-plug is the *higher*-risk operation, not the lower-risk one — it adds the new
drive's spin-up transient on top of a root SSD already serving live I/O.

`vcgencmd get_throttled` reads `0x0` throughout and **does not rule this out**: it
measures the SoC supply rail, not per-port USB current.

## What does not fix it

| Attempted fix | Why it fails |
|---|---|
| Bigger power supply (e.g. 27 W) | The limit is the Pi's over-current protection, not the PSU. **Verified on this host 2026-08-02: the PSU is a Spigen 20 W USB-C with a known-good cable, confirmed adequate by the operator. The input side is not the problem — stop re-testing it.** |
| Y-cable across two Pi USB ports | 1200 mA is a **shared** budget across all ports. A second port adds no headroom. |
| **Moving BOTH drives to USB 2.0** | See the dedicated entry in "Options evaluated and rejected" — it costs the root disk ~10× throughput and its UAS command queueing, while leaving the shared current budget untouched. |
| `usb-storage.quirks=VID:PID:u` | A **protocol** fix (UAS → BOT), not a power fix. It cures `uas_eh_abort_handler` stalls and missing SMART. Jeff Geerling measured UASP as ~8% *more* power-efficient than BOT, so disabling UAS can very slightly worsen a power problem. |

Use `usb-storage.quirks` when the symptom is UAS command timeouts (`uas_eh_abort_handler`
as the *first* error). Ours starts with `USB disconnect` and only shows `uas_zap_pending`
afterwards, as a consequence — that is power, not protocol.

## What does fix it

1. **Powered USB 3.0 hub** (its own mains adapter) — full USB3 speed, the consensus
   solution in every source below. Caveat: some hubs **backfeed** 5 V into the Pi,
   bypassing its input protection; pick one that doesn't.
2. **Externally-powered enclosure, or a Y-cable whose second leg goes to a separate
   adapter** (not to another Pi port) — cheaper variant of the same idea.
3. **USB 2.0 port** — what this homelab does. Free, and it fixes *two* things at once:
   the current cap drops to 500 mA **and** the kernel binds `usb-storage` instead of
   `uas` (UAS is not used on USB 2.0), removing both risk factors.

## Why USB 2.0 is not enough

Scoreboard from the night of 2026-08-01/02, Kingston XS1000 alongside the SanDisk
root SSD:

| Configuration | Clean | Failed |
|---|---|---|
| USB 3.0 (blue) | 0 | 3 — boot (~8 min), hot-plug (**~15 s**), boot (~8 min) |
| USB 2.0 (black) | 2 cold boots + load test | 1 — boot, read-only ~10 min in |

USB 2.0 measurably helps: the two clean cold boots and a 91-second concurrent-read
test at 34.5 MB/s all passed, where USB 3.0 never survived a single attempt. But it
is a **risk reduction, not a fix**.

The fourth failure is also qualitatively different, and worse:

- The persisted kernel log for that boot is **clean**. Kingston enumerated on
  `usb 1-1.3` as `usb-storage`; the SanDisk reported its full
  `3907029168 512-byte logical blocks (2.00 TB)` — no `USB disconnect`, no
  `Read Capacity failed`, no 0-byte enumeration. ext4 mounted `r/w` normally.
- The journal then simply **stops**, with no storage error of any kind, because
  journald could no longer write once the device was gone.
- So the drop happened in **steady state, roughly ten minutes after a clean
  spin-up** — not during the inrush transient the USB 2.0 current cap addresses.

That is why the cap is not sufficient: it lowers peak draw at spin-up, but the
budget stays marginal for whatever transient tipped it later. And a steady-state
drop is harder to plan around than a boot-time one — there is no safe window.

**Practical rule:** while a second bus-powered drive is attached without a powered
hub, the Pi is one transient away from a read-only root, at any moment. If the
drive is not actively earning its keep, unplug it.

## Measured here

Kingston XS1000 on `usb 1-1.3` @ 480M, `usb-storage`; SanDisk root SSD alone on
bus 2 @ 5000M, `uas`:

- Clean boot with **both** drives spinning up — the exact condition that failed on
  USB3. No disconnect, no `Read Capacity` failure, no 0-byte enumeration.
- Concurrent-read load test (Kingston 3.1 GB while the root SSD read 4 GB at the
  same time): **34.5 MB/s**, 91 s, zero kernel faults, root `rw`, 41/41 containers.

**~34.5 MB/s is the ceiling to plan against** *if* the drive is ever made stable
(i.e. behind a powered hub). Fine for backups and for streaming (a 4K HEVC
direct-play stream is ~2.5–5 MB/s, so ~7–13 concurrent streams fit). Not fine for
a transcode cache, a database, or anything latency-sensitive.

Note the load test passed and the configuration still failed later — **a passing
load test does not establish stability here.** Only time does.

## Can software stop the drive from taking the Pi down — no

Asked directly on 2026-08-02, after the fifth outage. Three angles, all dead ends:

**Cut power to just that port — impossible.** `uhubctl` *does* support the Pi 4B
(v2.4.0+, with VL805 firmware ≥ `00137ad`), but the Pi's ports are **ganged**:
you can only switch ALL USB power off or on, never one port. And on this box the
**root SSD is itself on USB**, so cutting power to the offender cuts power to the
disk you are trying to protect. The feature exists and is useless here.

**Deauthorize the device instead — doesn't help.** `echo 0 >
/sys/bus/usb/devices/<id>/authorized`, or unbinding the `uas`/`usb-storage`
driver, stops the kernel *talking* to the drive but leaves **VBUS powered**. The
drive keeps drawing current; only the data path goes away. uhubctl's own docs and
multiple vendor forums say this explicitly.

**Cap the current in config — no such knob.** The 1200 mA ceiling is enforced by
the board's over-current protection. See the table above.

So the only real levers are physical: external power, or don't attach the drive.
Everything this repo ships (`nofail`, the `mountpoint -q` gate in
`storage/tasks/ssd2.yml`, the `DataDisk*` rules) limits the *blast radius* of a
dropout — it cannot prevent one.

## Options evaluated and rejected (2026-08-02)

| Option | Verdict |
|---|---|
| **Plug the drive into the router** (TP-Link EX20v) | **No.** Probably no USB port at all — the sibling EX220 is 1 WAN + 4 LAN, and the EX20v web UI exposes no USB strings. Even with one: TP-Link firmware mounts only FAT32/exFAT/NTFS, **never ext4**, so the PUID/PGID 1000 model breaks; sharing is SMB/FTP, which gives **no hardlinks**, so the `storage` role's downloads↔media invariant fails and *arr imports become full copies; router USB 2.0 behind a weak SoC typically manages 5–15 MB/s, *slower* than the 34.5 MB/s the drive did on the Pi; and it is an ISP-managed TR-069 gateway that can be firmware-pushed and rebooted remotely. |
| **Drive on the Mac, exported over NFS** | **No — operationally.** Technically the strongest option (mains power, USB3 locally, ~110 MB/s over gigabit, NFS preserves POSIX perms and hardlinks). Ruled out by the operator 2026-08-02: **the Mac is not always on**, so anything depending on it — weekly backups, *arr imports, media serving — would fail whenever it is asleep. |
| **New bootloader / EEPROM config** | **No.** `max_usb_current=1` is a **Pi B+/Pi 2** knob; the Pi 4 already supplies its maximum and there is nothing to raise. `usb_max_current_enable` is **Pi 5** only (and needs a 5 A PSU to reach 1.6 A). The Pi 4 EEPROM's USB settings (`USB_MSD_STARTUP_DELAY`, `USB_MSD_DISCOVER_TIMEOUT`, `USB_MSD_PWR_OFF_TIME`) are **timing, not current**, and apply only during the bootloader stage — before Linux, and therefore nowhere near the sustained-load failure that actually broke us. |
| **Both drives on USB 2.0** | **No — actively worse.** Asked 2026-08-02. Three reasons. (1) **It does not address power.** The ~1200 mA budget is shared across all four ports and fed from one 5 V rail; moving a device to a USB 2.0 port does not give it a separate supply. The descriptor's `MaxPower` (SanDisk 896 mA, Kingston 500 mA) is an *advertisement*, not a governor — the drive draws what its workload demands. USB 2.0 helps only indirectly, by making the link slow enough that the drive spends less time at peak draw. That is a throughput throttle, not a power fix, which is exactly why §"Why USB 2.0 is not enough" exists. (2) **Both USB 2.0 ports share ONE 480 Mbps bus.** Live topology: `Bus 001` root hub is single-port (`xhci_hcd/1p`) with the Pi's onboard 4-port hub behind it. Two drives there would *split* ~35–40 MB/s, not get 35 each — and the Kingston alone was already measured at 15 MB/s under the Phase 3 copy. (3) **The root disk would lose UAS.** The SanDisk runs `uas` on USB 3.0 today; on USB 2.0 the kernel binds `usb-storage` instead, and the loss of command queueing costs far more than the bandwidth for this workload — 41 containers, PostgreSQL, and a 235 G small-file config tree (millions of files). Trading 10× root-disk performance for a partial reduction in a risk you have not eliminated is the wrong trade. |
| **Move BOTH drives to USB 2.0** | **No.** Caps the *root* SSD at 500 mA and drops it from `uas` to `usb-storage`, costing it ~10× throughput and its command queueing — on the disk every container runs from. And it does not touch the shared budget: 500 + 500 mA still contends with the same 1200 mA ceiling once the SoC, Ethernet and Wi-Fi take their share. Pays the whole cost of the fix and buys none of it. |
| **Powered USB3 hub** | **Yes — still the answer** if the drive is to live on the Pi. Pick one that does not backfeed 5 V into the Pi. |
| **Larger single root SSD** | **Yes** — one device, no contention, no new failure mode. Costs a full clone. |
| **Pi 5 + 5 A PSU** | Real hardware fix (1.6 A USB budget, NVMe HAT options), but that is an upgrade decision, not a setting. |

## Reading `lsusb` on this host — the hub is the Pi's own

A correction worth stating plainly, because it was misread once (2026-08-02) and led to
"a hub is already present, so power should be fine":

```bash
Bus 001 Device 002: ID 2109:3431 VIA Labs, Inc. Hub     ← the Pi 4B's ONBOARD USB 2.0 hub
Bus 002 Device 002: ID 0781:55bb SanDisk Corp. Portable SSD
```

`2109:3431` is **not** an external accessory. The Pi 4B's two USB 2.0 sockets hang off an
onboard VIA Labs hub behind a single-port USB 2.0 root hub, so this line is present on
every boot — including boots with nothing plugged into USB 2.0 at all. Its descriptor
even reports `Self Powered`, which reinforces the wrong conclusion.

**A powered hub would appear as an ADDITIONAL device with its own VID:PID.** If the only
hub you see is `2109:3431`, there is no powered hub on this machine and §"What does fix
it" item 1 is still outstanding. Check what a hub is before crediting it.

## Detection

Shipped 2026-08-01 — see [design 042](../design/042-second-ssd-integration-plan.md)
and the [node_exporter kernel scanner](../../ansible/roles/node_exporter/files/kernel_io_scan.py):

- `HostStorageLinkDropout` — fires on **one** `USB disconnect` or over-current.
- `HostStorageFaultsFatal` — fires on the 0-byte enumeration signature.
- `RootFilesystemReadOnly` — the terminal state, from node_exporter's in-memory
  `node_filesystem_readonly`.

Caveat that cost us twice: **all of these run on the Pi.** In the second outage
node_exporter itself died and every on-Pi alert produced nothing. Only an off-Pi
watchdog firing on *scrape failure* caught it.

Quick manual check after any reboot or re-cabling:

```bash
curl -s 192.0.2.100:9100/metrics | grep homelab_kernel_io_events | grep -v ' 0$'
# empty (apart from the timestamp line) = clean
lsusb -t   # the second drive must show 480M / usb-storage, not 5000M / uas
```

## Sources

- [Raspberry Pi documentation — USB bus on Raspberry Pi](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/raspberry-pi/usb-bus-on-raspberry-pi.adoc) — the 1200 mA total figure
- [Storj Community forum — two disks on a Pi 4, identical failure](https://forum.storj.io/t/setting-up-rpi4-64bit/23613?page=4)
- [Raspberry Pi Stack Exchange — USB SSD disconnecting, `sdb`→`sdc` renumbering](https://raspberrypi.stackexchange.com/questions/136593/usb-ssd-disconnecting)
- [PragmaticLinux — three fixes for USB3 SSDs on a Pi 4](https://www.pragmaticlinux.com/2021/03/fix-for-getting-your-ssd-working-via-usb-3-on-your-raspberry-pi)
- [DietPi forum — the shared-current limit, why a Y-cable doesn't help](https://dietpi.com/forum/t/external-hdd-issues/3829?page=3)
- [Ubuntu bug #1914866 — same `0 512-byte logical blocks` signature, inrush-current diagnosis](https://bugs.launchpad.net/ubuntu/+source/linux-raspi/+bug/1914866)
- [What I Made Today — powered-hub backfeed hazard](http://www.whatimade.today/how-to-protect-a-rasperry-pi-from-a-powered-usb-hub)

Incidents: [boot dropout](../incidents/2026-08-01-usb-ssd-root-dropout-readonly.md) ·
[hot-plug recurrence](../incidents/2026-08-01-usb-ssd-hotplug-readonly-recurrence.md)
