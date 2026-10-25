# 2026-05-29 — qBittorrent stale lockfile → silent-exit respawn loop

- **What happened:** From ~21:50 the qBittorrent WebUI stopped responding
  (`curl` exit 7 / login http 000). The container showed `Up … (unhealthy)`;
  `qbittorrent-nox` kept starting and exiting 0 silently within seconds while
  the unhealthy-watchdog `docker restart`ed it every 5 min. Every
  pipeline-metrics tick logged `fail stage=qbit_login http=0`, firing
  pipeline alerts; the download pipeline (imports, cleanup, reconcile) stalled.
  Recovered manually: `s6-svc -d`, `pkill -9 qbittorrent-nox`, `rm`
  `/config/qBittorrent/config/lockfile`, `s6-svc -u` → nox stayed up, WebUI 200,
  814 torrents reloaded, container healthy.
- **Why:** qBittorrent's `QLockFile` stores the holder's PID **and hostname**.
  The lockfile (dated 2026-05-27, hostname `4ef60b4017c3`) outlived its writer
  — a container recreate (LSIO image rebuilt 2026-05-26, almost certainly a
  Watchtower update) and/or a watchdog SIGKILL left it behind. On every
  subsequent start the new container's hostname differed, so `QLockFile`
  refused to steal the lock and nox exited 0 immediately — the exact symptom of
  the v5.2.0 missing-`--profile` bug, different root cause.
- **Class:** stale single-instance lockfile surviving an ungraceful
  restart/recreate → silent-exit respawn loop. Adjacent to the existing
  "qBit 5.2.0 silent-exit without --profile" workaround (01-profile-fix.sh):
  same fatal symptom (nox exits 0, no log), different trigger.
- **Occurrence:** 1st of this class. (The profile-fix custom-init is the prior
  *same-symptom* fix; this is a distinct trigger.)
- **Structural fix:** **shipped** — `02-clear-stale-lock.sh` custom-init
  (media role) removes `/config/qBittorrent/config/lockfile` before s6 starts
  qbittorrent. The hook runs when no nox can legitimately hold the lock, so any
  lockfile present is by definition stale. Secondary gap (deferred): the
  unhealthy-watchdog SIGKILLs without a pre-restart lock sweep, and the qBit
  healthcheck reports unhealthy without distinguishing "loading" from "wedged".
