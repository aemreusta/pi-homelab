# Runbook 038 — Retention cleanup (30-day, disposable artifacts only)

**Date:** 2026-05-29
**Status:** active
**Related:** [`common/templates/retention-cleanup.sh.j2`](../../ansible/roles/common/templates/retention-cleanup.sh.j2),
[runbook 037](037-2026-05-29-srv-disk-fill-exit-criterion.md) (disk pressure),
[incident log](../incidents) · the no-data-removal rule.

## §1 Why

The old weekly job ran `docker system prune -af --volumes` with **no time
bound** — it removed *all* unused images immediately and `--volumes` could drop
named volumes holding service data. This replaces it with an **age-gated,
volume-safe** retention cleanup: only artifacts older than
`common_retention_days` (default **30**) are removed, and only **disposable**
ones. It pairs with the predictive disk alert (runbook 037) to keep `/srv` from
filling without ever touching personal/library data.

## §2 Scope — what it removes (all age-gated to 30 days)

| Artifact | Mechanism |
| --- | --- |
| Docker images unused > window | `docker system prune -af --filter until=720h` |
| Docker build cache > window | `docker builder prune -af --filter until=720h` |
| Stopped containers / unused networks > window | covered by `system prune` |
| Transcode-cache scratch > window | `find /srv/transcode-cache -type f -mtime +30 -delete` + empty-dir sweep |
| journald logs > window | `MaxRetentionSec=1month` in the journald drop-in (not this script) |

## §3 Protected — what it NEVER touches

This is the load-bearing half. The cleanup is deliberately scoped **away** from
anything that isn't trivially regenerable:

- **`/srv/media`** — library content. Never.
- **`/srv/downloads/**`** — operator-reviewed only; surfacing oldest downloads
  for review is runbook 037 §4, and even that is manual. Never auto-removed.
- **Named Docker volumes** — `--volumes` is OFF
  (`common_retention_docker_prune_volumes: false`). Opt-in only, with a WARN log
  line if ever enabled.
- **`/srv/backups`** — has its own 28-day retention in the backup role.
- This honors the **no-data-removal hard rule**: never plan removing library
  content or any user data.

## §4 Schedule, config, observability

- **Cron:** weekly, Sunday 04:00 (`/usr/local/bin/retention-cleanup.sh`).
- **Tunables** (`common/defaults/main.yml`): `common_retention_days` (30),
  `common_retention_docker_prune_volumes` (false),
  `common_retention_transcode_cache_dir` (`/srv/transcode-cache`),
  `common_retention_dry_run` (false), `common_journald_max_retention_sec`
  (`1month`).
- **Metric** (silent-cron hard rule) → `retention_cleanup.prom`:
  `retention_cleanup_last_run_unix_seconds`, `_last_run_success`,
  `_cache_files_removed`, `_dry_run`.
- **Log:** `/var/log/retention-cleanup.log` (logrotate weekly, keep 8).
- **ntfy:** summary on success (reclaimed bytes + scratch count), high-priority
  on failure.

## §5 Operator drills

```bash
# Dry-run: log intended actions, delete nothing.
ssh pi@homelab 'sudo common_retention_dry_run=1 /usr/local/bin/retention-cleanup.sh' 2>/dev/null
#   (or deploy once with -e common_retention_dry_run=true, inspect, redeploy default)

# What would a 30-day docker prune reclaim right now? (read-only preview)
ssh pi@homelab "docker system df"
ssh pi@homelab "docker image ls --filter 'dangling=false' --format '{{.Repository}}:{{.Tag}} {{.CreatedSince}}'"

# Transcode scratch older than 30d (count, no delete):
ssh pi@homelab "find /srv/transcode-cache -type f -mtime +30 | wc -l"

# Verify the metric after a run:
ssh pi@homelab 'grep ^retention_cleanup /var/lib/node_exporter/textfile/retention_cleanup.prom'
```

## §6 Migration note

The superseded `docker-prune.sh` + its cron + logrotate stanza are removed on
the next `make base`. The old `/var/log/docker-prune.log` is left in place so
its history survives; it ages out under normal rotation.

## §7 Future work (Sprint N+1)

- **`RetentionNeverReclaims` vmalert rule.** The cron emits `retention_cleanup_*`
  metrics but nothing alerts on the cron going silent or reclaiming nothing for a
  long horizon — the silent-cron principle applied to retention
  itself. Two cheap rules: a staleness check on
  `retention_cleanup_last_run_unix_seconds` (cron stopped firing), and a
  long-horizon "no reclaim in 30d" informational check (retention wired up but
  inert — worth knowing). The metrics already exist; the rules are ~8 lines and
  would be validated by `tests/test_vmalert_rules.py`. Deferred deliberately to
  avoid another rule-file deploy over a degraded link the night this shipped.
- **First-run note:** the initial manual run (2026-05-29) reclaimed **0 B** — the
  system had no 30d+ disposable cruft. That is expected and confirms the `/srv`
  pressure is real data (see runbook 037), not pruneable. A future non-zero
  reclaim is informative, not alarming.
