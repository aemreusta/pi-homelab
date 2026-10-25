# 2026-08-02 — Phase 3 migration took the Pi down under sustained load; ssd2 rolled back entirely

- **What happened:** With design 042 Phases 1–2 live, Phase 3 began copying `/srv/media` + `/srv/downloads` (107 GiB real) to the Kingston. About an hour in — roughly 37 GiB copied — the Pi went down: pings fine, sshd refusing, node_exporter dead, every port closed. Fifth outage in ~12 hours. Recovered by power-cycle. The operator then removed the Kingston from the Pi and attached it to the Mac, and the whole ssd2 integration was rolled back.
- **Why:** Same 1200 mA ceiling, new trigger. The previous four failures were **spin-up transients** (seconds). This one was **sustained concurrent I/O on both drives for an hour** — reading from the root SanDisk while writing to the Kingston, both sharing the budget. The migration is, by construction, the single heaviest load this configuration can be put under, and it was run for an hour on hardware already known to be marginal.
- **Class:** USB power-budget exhaustion (5th), but a **new sub-class: sustained load rather than inrush**. Also a planning failure of my own — see below.
- **Occurrence:** 5th of the class. See [boot dropout](2026-08-01-usb-ssd-root-dropout-readonly.md), [hot-plug](2026-08-01-usb-ssd-hotplug-readonly-recurrence.md), [premature USB2 resolution](2026-08-02-usb2-not-sufficient-premature-resolution.md).
- **Structural fix:** **Rollback, not mitigation.** `storage_ssd2_enabled: false` — and that flag is an active teardown, not a skip: it unbinds `/srv/backups`, unmounts `/mnt/ssd2`, and **removes both fstab entries**, so re-plugging the drive cannot silently remount it. `backup_root` is back on the root filesystem with `backup_require_mountpoint: false` and the free-space gates rescaled to 10/30 GiB. The alternatives (router USB, Mac+NFS, bootloader/EEPROM, `uhubctl`) were researched and are all recorded as rejected in [the convention doc](../conventions/pi4-usb-power-budget.md). Durable backup coverage moves to **Google Drive via rclone** instead of a second local disk.

## What I got wrong

I started a one-hour, both-drives-saturated operation on hardware I had, hours
earlier, documented as failing **without warning under sustained conditions** —
and I reported "kernel faults zero" from fifteen minutes of observation as if it
were reassurance. That is the same error as the premature USB 2.0 call, repeated
inside the same night: **treating a short clean window as evidence about a
marginal-power fault whose defining property is that it fires late.**

The rule was already written down. I did not apply it to my own plan.

**Concretely, for next time:** on marginal hardware, an operation's *duration* is
part of its risk, not just its steps. A 107 GiB copy is not "Phase 3" — it is a
one-hour stress test of the exact failure mode under investigation, and it should
have been chunked (10–15 GiB with rests) or deferred behind the powered hub. I
offered both options *after* the outage; they should have been the proposal.

## Data status — nothing lost

- `/srv/media` and `/srv/downloads` were **never moved**. The migration was a
  one-way rsync; the sources on the root disk were untouched, and no bind mount
  or service configuration was ever switched over. There was nothing to "copy
  back".
- `/srv/backups` **had** moved to the Kingston, so after rollback the Pi briefly
  had no backups. Recovering them would have meant re-attaching the Kingston to
  the Pi (macOS cannot read ext4, and the Mac has no ext4 tooling), i.e. risking
  another outage for 6 GiB of **regenerable** data — a full backup run takes 265 s
  and produces ~950 MB. Regenerated instead. Only the 28-day snapshot history was
  lost.
- Root filesystem: ext4 `clean`, no I/O errors, 41/41 containers back.

## Kept for the record

The partial Kingston copy (~37 GiB of media/downloads) is orphaned on a drive now
attached to the Mac. It is a redundant copy of data that still exists on the Pi;
the drive will be reformatted or repurposed rather than reconciled.
