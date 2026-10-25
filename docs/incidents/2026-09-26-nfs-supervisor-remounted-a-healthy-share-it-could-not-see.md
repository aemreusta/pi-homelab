# 2026-09-26 — the NFS supervisor remounted a healthy share it was not allowed to see

- **What happened:** The operator kept getting macOS "Server connections interrupted:
  homelab-srv" dialogs, and `MacNfsStatusMissing` had been firing since 2026-09-25 00:26.
  Both NFS mounts were in fact **healthy**: `mount` listed them, a read of each probe file
  from a shell returned `rc=0`, and the macOS kernel logged **zero** `nfs server … not
  responding` events in 24h. The supervisor disagreed — `/var/run/homelab-nfs.json` showed
  both exports `state=unknown reason=read_denied`, `last_read_ok: 0`, and for `srv`
  `"recoveries": 1`. It had unmounted and remounted a share that was working.
- **Why:** `io.tdarr.nfsmount` runs as a **LaunchDaemon**, which does not inherit the Full
  Disk Access grant that a Terminal-launched process gets. Every read probe against
  `/Volumes/homelab-*` therefore returned `read_denied` (12 occurrences on 09-25), and
  publishing the shared status to `/Volumes/homelab-generated/mac-nfs-status.json` failed
  with `PermissionError: [Errno 1] Operation not permitted` — so the Pi never received a
  status at all and `MacNfsStatusMissing` fired correctly about a monitor, not a mount.
  `decide()` already refused to act on `unknown`, and its comment says so explicitly. But
  a blocked probe does not always fail the same way: sometimes it **timed out** at the 8 s
  ceiling instead of being denied, and `timeout` maps to `hung`. Two of those satisfied
  `state == "hung" and failures >= 2`, and the supervisor remounted. The macOS unmount
  assistant then showed the operator exactly the dialog the supervisor exists to prevent.
- **Class:** *A monitor that manufactures the symptom it exists to detect* — remount storm
  **#2** after the 2026-06-07 30-second storm. Different trigger, identical shape: an
  unreliable signal was treated as authoritative and converted into a destructive action.
  Also a second instance of **"a macOS LaunchDaemon cannot write the `/Volumes` NFS mount"**,
  two days after the same root cause was found making `GHRunnerRebuildStale` lie for 20 days
  (found 2026-09-24). That one was fixed by routing through the Docker NFS volume; this
  one cannot use that escape hatch, because reading `/Volumes` **is** its job.
- **Occurrence:** 2nd of the remount-storm class; 2nd of the LaunchDaemon-vs-`/Volumes`
  class in three days.
- **Structural fix:** SHIPPED (partial — the rest is an operator action).
  `decide()` now takes `ever_read_ok` and refuses to recover any share it has never once
  read successfully. The reasoning is that a supervisor in that state **cannot distinguish
  "the mount is hung" from "I am not allowed to look at it"**, so it has no standing to act:
  prove you can read the share once, then you may disrupt it. `unmounted` is deliberately
  still actionable, because that state is read from `mount(8)` rather than from a file probe
  and is therefore unaffected by the blindness — without that carve-out the guard would
  leave a genuinely absent mount down forever. Guarded by three tests in
  `tests/test_mac_nfs_mount.py`.
  **Still open — operator:** granting Full Disk Access to the daemon is the only thing that
  restores *visibility*; until then the supervisor stays blind (and now harmless), and
  `MacNfsStatusMissing` keeps firing truthfully. See
  [runbook 053](../runbooks/053-2026-09-26-mac-nfs-supervisor-permissions.md).
  **Lesson to carry:** before a monitor is allowed to take a destructive action, require it
  to have observed the healthy state at least once. "I cannot see it" and "it is broken"
  produce the same reading and must not produce the same response.
