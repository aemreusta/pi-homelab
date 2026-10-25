# 037 — Self-healing and automation audit

**Date:** 2026-08-03 · **Method:** static analysis, then **live-verified on the Pi**
**Status:** P0-1 shipped the same day; the rest is a tracked roadmap.

---

## Thesis (accepted)

> This homelab is **detection-rich and crash-recovery-strong, but action-poor.**

That framing holds up. Between a failure and a human acting, only three things happen
automatically: Docker restarts a crashed container, the unhealthy-watchdog restarts a
healthcheck-failing one (≤5/24 h, and only for the 26 of 41 containers that *have* a
healthcheck), and a few timed restarts fire. Everything else pages and waits.

```text
failure ──► detect (vmalert / watchdog / cron metric)
             ├─[clean crash]──────► docker restart .............. AUTO
             ├─[healthcheck trip]─► watchdog restart (≤5/24h) ... AUTO  (26/41)
             └─[everything else]──► ntfy ──► HUMAN ............... manual
```

The missing arrow is **detect → remediate** for anything that is not a clean crash or a
healthcheck trip.

## What was verified against the live box

The audit was explicit that it polled no live state and that its disk figures came from a
~48 h-stale `PROJECT_INDEX.md`. Per
[ops-discipline §4](../operations/ops-discipline.md), the load-bearing claims were checked
before acting on them:

| Claim | Verified? | Evidence |
|---|---|---|
| `homelab-backup.sh.j2` emits **zero** textfile metrics | **CONFIRMED** | `grep -cE 'textfile\|\.prom\|emit\(\)'` → `0`; no `trap` |
| No `BackupStale`-equivalent rule exists | **CONFIRMED** | only `gdrive-backup.yml.j2` matched |
| `backup_require_mountpoint: false` | **CONFIRMED** | `backup/defaults/main.yml:33` |
| `ContainerUnhealthy` rule commented out | **CONFIRMED** | `host-saturation.yml.j2:554` |
| Disk at 93–97% | **STALE, still directionally right** | live 2026-08-03: **94%, 120 G free** |

## P0-1 — local backup was invisible · **SHIPPED 2026-08-03**

Agreed with the audit that this was the highest-leverage item, and for the reason it gave:
data loss is the one unrecoverable failure class, and the *fast* restore path had no
machine-readable signal at all. A total failure was detectable only by a human noticing the
weekly ntfy never arrived — or ~30 days later via `GdriveBackupNoData` on the **off-site**
tier, which is a mirror of the local one and would therefore be stale for the same reason.

Shipped:

- `trap emit EXIT` in `homelab-backup.sh.j2` writing `backup.prom` atomically, with
  `backup_last_run_unix_seconds`, `_success`, `_duration_seconds`, `_services_total`,
  `_services_failed`, `_bytes`. Abort paths (unmounted disk, low free space) emit
  `success=0` rather than leaving yesterday's `1`.
- Four rules in `roles/vmalert/templates/rules/backup.yml.j2`:
  `BackupStale` (crit), `BackupFailing` (crit), `BackupPartialFailure` (warn — a single
  persistently-failing service otherwise hides behind a green overall run), and
  `BackupWroteNothing` (crit — the silent-zero shape).
- Tests in `tests/test_backup.py` asserting the trap, the atomic move, every gauge, and
  **that the rule file is registered in `vmalert_metric_rule_files`** — an unregistered rule
  file is silently never deployed.

Verified live: rules loaded (125 total), and the emit executed in isolation produces 6
well-formed metric lines. The gauges themselves populate on the next scheduled run.

**A test bug worth recording:** the registration assertion first passed while the file was
*not* registered, because `"backup.yml" in defaults` matches the substring inside
`gdrive-backup.yml`. Now anchored on the list-item form. A guard that cannot fail is worse
than no guard.

## P0-2 — disk-full auto-remediation · agreed

The audit is right that design 030 item B's ship-gate ("revisit when disk usage trends
toward 80%") was passed weeks ago, and right that this must go through 030's
doc-first → ship-gate → soak process rather than a silent code drop.

## P0-3 — ntfy is the only receiver · agreed, unchanged

Five alerts cannot self-page when their own condition fires, and the off-box dead-man
checks disk + VM but not ntfy reachability or subscriber presence — so the 2026-06-06
"36 h silent, every check green" class can recur with the dead-man still pinging OK. This
is the one item whose absence silently degrades **all** detection, and it remains open.

## P1 items · accepted as written

- **P1-1** — the watchdog, the only thing that restarts wedged containers, is itself a
  silent cron (audit line only, no gauge). Re-enable `ContainerUnhealthy` at the same time.
- **P1-2** — authelia/filebrowser/homepage have no healthcheck, so the watchdog *cannot*
  act on them; a wedged authelia is SSO down for every internal route. Healthchecks first,
  they are the cheap enabling step.
- **P1-3** — ~12 fire-and-forget crons with no metric. Mechanical sweep, established
  pattern.

## Where I would push back

**"Full otomatik calissin" is the right goal but the wrong next step.** The watchdog has
already amplified an incident once (the qBit lockfile respawn loop), which is exactly why
design 030 is conservative. The ordering that respects that:

1. **Observability of the remediators themselves** (P1-1) — an auto-healer with no metric
   is a liability, because when it stops healing nothing says so.
2. **Enable healthchecks** (P1-2) — gives the existing, budget-capped watchdog something to
   act on. No new actor, no new failure mode.
3. **Then** metric-triggered remediation (P0-2), through 030's process.

Adding automatic actions before step 1 means adding actors that can fail silently, which is
the failure mode this repo has paid for most often.
