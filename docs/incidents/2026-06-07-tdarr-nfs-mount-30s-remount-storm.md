# 2026-06-07 — Tdarr transcode errors were a 30s self-inflicted NFS remount storm

- **What happened:** The same day's NFS-workdir-EACCES investigation
  correctly proved the 37 `Transcode error` files were healthy h264 (ffprobe clean, videotoolbox encodes
  fine) but **misattributed the cause** to a `soft` mount surfacing *random* server hiccups, and concluded
  "requeue is futile." Deeper diagnosis found the real mechanism: `mac-worker/tdarr-nfs-mount.sh` was
  **force-unmounting BOTH NFS shares every 30s, on every tick, forever.** A plain `dd` of a 4.5 GB source
  over NFS died at **412 MB / 15 s** (`Device not configured`); the daemon log showed a `switching …
  (network state changed)` + force-unmount at every 30s tick. Anything reading >~30 s — i.e. every
  multi-GB file — got the mount yanked mid-read (`EACCES` on workdir mkdir if killed before start, `EIO`
  if mid-encode). Small files that finished inside one 30s window survived → the "only the biggest fail"
  pattern. The earlier investigation's own canary evidence confirms it: the 5-file canary "re-errored within
  ~90s" = exactly **3 force-unmount ticks**, not a random flaky window.
- **Why:** A string-comparison bug in the daemon's switch-on-network-change check. `current_source()`
  returns the mounted source as `<ip>:<path>` (`192.0.2.100:/srv`), but `pick_source()` returns a bare
  `<ip>` (`192.0.2.100`). The guard `if [ "$cur" != "$best" ]` compared `192.0.2.100:/srv` against
  `192.0.2.100` — **always unequal** → the daemon believed the network changed every loop and
  force-unmounted + remounted both shares every `LOOP_INTERVAL` (30s). The `soft` mount was a **red
  herring**: it only chose the *error flavor* (`EACCES`/`EIO` vs a block); the *trigger* was the daemon
  unmounting its own healthy mount on a fixed 30s clock. A `hard` mount would NOT have fixed it — the
  force-unmount happens regardless of mount semantics.
- **Class:** Self-inflicted resource churn from a `ip` vs `ip:path` comparison mismatch — a monitoring/
  self-healing loop whose "has-state-changed?" predicate is structurally always-true, so it "heals"
  every tick and destroys the thing it guards. Sibling of the silent-cron family (a loop doing the wrong
  thing on a timer) and of the "Mac compute-offload dark via macOS networking" class — but the prior 4
  instances were genuine network/mount faults; this 5th was the *daemon meant to heal them* misfiring.
- **Occurrence:** 1st of this exact shape; it is the **true root cause** of the
  06-07 EACCES errors (whose earlier `soft`-mount
  "Why" and "requeue is futile" conclusion are now superseded).
- **Structural fix:** **Shipped + verified.** `mac-worker/tdarr-nfs-mount.sh` now computes the full
  expected source (`$best:/srv` / the generated path) once per target and compares `cur` against *that*,
  so a switch fires only on a real LAN↔Tailscale change. Also dropped the invalid `nofurl` mount option
  (macOS warned "option not known" on every mount). Installed to `/usr/local/bin`, LaunchDaemon
  rebootstrapped. **Verified:** zero `switching` log lines after restart (was every 30s); the full 4.5 GB
  read that died at 412 MB now completes end-to-end (154 s); all 37 files requeued (cruddb → Queued) and
  the Mac node is actively transcoding them at 110–139 fps (4–5× realtime) — the requeue the earlier
  incident called "futile" now succeeds because the mount is stable. Follow-up still worth doing (cheap
  insurance, not the fix): the daemon's health probe could test a real `mkdir` in transcode-cache and
  remount on `EACCES`/`ESTALE`/`EIO`, per the earlier investigation's deferred note.
