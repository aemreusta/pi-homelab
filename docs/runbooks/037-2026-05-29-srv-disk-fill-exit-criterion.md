# Runbook 037 — /srv disk fill: predictive alert + 95% exit criterion

**Date:** 2026-05-29
**Status:** active
**Related:** [host-saturation rules](../../ansible/roles/vmalert/templates/rules/host-saturation.yml.j2),
[storage layout](../design/001-architecture.md#storage-layout),
[incident log](../incidents).

## §1 Why

`/srv` (a directory on the root ext4 SSD — see §2) sat at **91% used, 163 GB
free of 1.8 TB on 2026-05-29**. The operator chose to leave downloads alone and
rely on the `SrvDiskCritical` alert (fires at 90%). That alert is a *wall*, not
a *ramp*: by the time it pages you have hours, and at 100% imports fail and you
risk \*arr SQLite corruption.

This runbook adds (a) a **predictive** alert that pages a week ahead on the fill
*rate*, and (b) a pre-decided **exit criterion** so the 95% decision is made now
— calmly — instead of improvised at 02:47 under pressure.

## §2 Filesystem fact (verified 2026-05-29)

`/dev/sda` is mounted at `/` **only**. `/srv` is a *directory* on that root ext4
filesystem, not a separate mount:

```text
$ grep -E ' / | /srv ' /proc/mounts
/dev/sda / ext4 rw,noatime,stripe=256 0 0
$ df -h /srv          # → /dev/sda mounted at /, 1.8T, 91%
```

**Consequence for alerting:** node_exporter exports `node_filesystem_*` only for
`mountpoint="/"`. A rule matching `mountpoint="/srv"` returns an **empty series
and never fires** (this is also why the existing `SrvDiskHigh`/`SrvDiskCritical`
rules use `/`). The predictive rule below matches `/` for the same reason. This
corrects the assumption that `/srv` is a separate filesystem — it is not on this
host. If a dedicated data disk is ever mounted at `/srv`, flip all three rules.

## §3 The predictive alert — `SrvDiskFillRateHigh`

Defined in `host-saturation.yml.j2`. Label-safe single-series ratio form:

```yaml
expr: |
  predict_linear(
    (
      node_filesystem_avail_bytes{mountpoint="/",fstype!="tmpfs"} /
      node_filesystem_size_bytes{mountpoint="/",fstype!="tmpfs"}
    )[24h:1h],
    7 * 86400
  ) < 0.05            # vmalert_srv_disk_fill_rate_floor
for: 2h               # vmalert_srv_disk_fill_rate_for
```

"On the last-24h fill rate, projected free space drops below 5% (95% full)
within 7 days." Gives ~a week of lead time vs the `SrvDiskCritical` wall.
Thresholds are hoisted to `vmalert/defaults/main.yml` so a drill can compress
them. The single-series ratio (avail/size as one expression) avoids a
cross-series label join, so `predict_linear` stays label-safe.

## §4 Exit criterion — what happens at 95%

Decided in advance. **No action here removes user data** (no-data-removal hard
rule). At 95% (`df / | tail -1` shows 95%, or `SrvDiskFillRateHigh` has been
firing and `SrvDiskCritical` is imminent), in order:

1. **Pause new acquisition first** (stops the bleeding, reversible): pause
   Sonarr/Radarr search activity and qBittorrent so the fill rate goes
   flat while you investigate. Nothing is deleted.
2. **Surface the oldest cold downloads for operator review** — list, do not
   delete:

   ```bash
   ssh pi@homelab 'du -sh /srv/downloads/* 2>/dev/null | sort -h | tail -20'
   ssh pi@homelab 'find /srv/downloads -type f -printf "%T+ %s %p\n" 2>/dev/null | sort | head -40'
   ```

   The operator decides what (if anything) leaves — the agent does not.
3. **Hard-link audit on `/srv/downloads/`** — find completed torrents whose
   media is already hard-linked into the library (so the download copy is
   redundant and qBittorrent can release it without losing the library file):

   ```bash
   ssh pi@homelab 'find /srv/downloads -type f -links +1 | head'   # links>1 = hardlinked, safe to drop the dl copy
   ssh pi@homelab 'find /srv/downloads -type f -links 1  | head'   # links==1 = NOT hardlinked, do NOT touch
   ```

   Releasing a redundant download copy (links>1) frees space without removing
   any library content. Decision stays with the operator.
4. **Check the transcode cache** (`/srv/transcode-cache`) for straggler files
   from failed transcodes — these are genuinely disposable scratch, prune-safe.

Each step is reversible or non-destructive before the next. The point is that
this ladder exists *before* the pressure, not invented during it.

## §5 Why no automated prune

This repo's hard rule: **never plan removing library content or any user data**.
The predictive alert + this exit criterion deliberately stop at "surface for
operator review." Automating deletion at a disk threshold is exactly the
foot-gun the rule forbids. The alert buys a week; the operator spends it.
