# 030 — Auto-Control Policy

**Status:** decision doc (Sprint 017 Story 8)
**Date:** 2026-05-22
**Supersedes:** _ad-hoc decisions from Sprints 014–016_

## Why this exists

Issue #10 of the original 11-issue operator complaint list was "auto control over
system not exists." That phrasing collapses ~5 different automation decisions
into one bucket, each with its own failure mode. Without a documented decision,
every new outage tempts us to bolt another cron script onto `common/monitoring.yml`,
and we accumulate auto-control mechanisms that nobody can reason about together.

This doc is the matrix. Each automation has a trigger, a risk profile, an approval
gate, and an explicit rollback. Anything not on this list is _not implemented_
until added here.

## Definitions

- **Trigger** — the metric / event that fires the automation.
- **Action** — what the system does.
- **Risk profile** — what's the worst case if the automation misfires? Categorised
  as **L** (recoverable in <5 min, no data loss), **M** (operator-visible outage,
  no data loss), **H** (data loss or extended outage).
- **Approval gate** — the conditions that must hold before we ship the automation.
  Risk **H** items always require an operator decision in this doc.
- **Rollback** — how we disable the automation if it goes wrong. Always an
  Ansible-variable toggle so we can re-deploy clean.

## Status legend

| Symbol | Meaning |
|---|---|
| ✅ | shipped, currently active |
| ⏳ | spec'd here, not implemented |
| ❌ | rejected — reason recorded below |

## Matrix

### A. Auto-restart unhealthy containers ✅

**Status:** shipped Sprint 016 Item A (`unhealthy-watchdog.sh.j2`)
**Trigger:** `docker ps --filter health=unhealthy` lists the same container for
N consecutive 5-min polls (default 3).
**Action:** `docker restart <name>`. Up to `max_restarts` per rolling 24 h
(default 5), then escalate to URGENT ntfy and stop trying.
**Risk:** **L** — restart-loop budget prevents amplification; a genuinely broken
container yields one URGENT page after 25 min instead of silent
permanent-unhealthy state.
**Approval gate:** budget cap + cooldown verified during Sprint 016 deploy.
**Rollback:** remove the cron entry via `common_unhealthy_watchdog_enabled: false`
(currently always-on; toggle variable not yet implemented — add if rollback
ever needed).

### B. Auto-pause Sonarr / Radarr on disk-full ⏳

**Status:** **NOT IMPLEMENTED — needs ship gate**
**Trigger:** `/srv` disk usage ≥ 95 % AND > 0 active grabs in any *arr.
**Proposed action:** `PUT /api/v3/config/host` with `analyticsEnabled` toggled
off, OR `PUT /api/v3/indexer/{id}` with `enableRss=false` for every indexer
(harder to undo). OR `docker pause` on the `*arr` container (cleanest but breaks
the API for the duration).
**Risk:** **M** — `docker pause` of an `*arr` mid-grab leaves a half-imported file
in `/srv/downloads/`. qBit keeps the torrent. On unpause, the app's
RefreshMonitoredDownloads picks up where it left off. _Tested manually
2026-05-12; no data loss observed._
**Approval gate (un-met):**

1. Manual test of pause → unpause cycle on a live in-flight import.
2. Decision on which level pauses: indexer toggle (subtle), single-app
   pause, or all-arr pause (most aggressive).
3. Anti-flap: hysteresis ≥ 90 % to pause, ≤ 85 % to unpause, 30-min cooldown.
**Rollback:** the operator can always `docker unpause <name>`. If the
auto-control becomes flappy, set `common_disk_full_autopause_enabled: false`
in defaults and re-deploy.
**Decision date:** revisit when disk usage trends toward 80 % (currently 24 %).

### C. Auto-throttle indexers on CPU saturation ⏳

**Status:** **NOT IMPLEMENTED — needs ship gate**
**Trigger:** 5-min loadavg ≥ 3×cores AND > 0 active grabs in any `*arr`.
**Proposed action:** `PUT /api/v3/indexer/{id}` setting `enableRss: false`,
`enableInteractiveSearch: false`, `enableAutomaticSearch: false` for every
indexer until load5 drops back below 1.5×cores.
**Risk:** **M** — false-positive throttling delays incoming releases (worst case:
miss a 24 h scarcity window for a popular release). A misfire on every indexer
of every `*arr` is recoverable in 30 s but visible in the ntfy timeline.
**Approval gate (un-met):**

1. CPU-load-alert (Sprint 017 Story 1, shipped) must have run for ≥ 1 week
   to establish baseline CPU patterns. If load5 ≥ 3×cores fires more than
   2× a week without a paired outage, throttle would be over-triggered.
2. Migration to vmalert (Sprint 018) — once we have a real alerting
   substrate, this becomes a webhook on a vmalert rule instead of a cron
   script, and the throttle action is composable with other policies.
**Rollback:** `common_indexer_autothrottle_enabled: false`. The toggle restores
all indexers to `enableRss: true` on next deploy.
**Decision date:** Sprint 018 close-out + 1 wk soak.

### D. Auto-clean disk on critical fill ❌

**Status:** **REJECTED**
**Proposal:** at ≥ 90 % full, run `docker system prune -af --volumes` immediately
instead of waiting for the Sunday 04:00 cron.
**Why rejected:** the Sunday cron already runs `docker system prune -af --volumes`;
on a steady-state homelab the only way to be at 90 % full and still have prunable
space is if a half-completed import + a transcode-cache leftover collide on the
same week. That's exactly the case where we want a human looking at the disk before
anything gets deleted. Auto-cleaning hides the underlying bug.
**Alternative:** the disk-alert already pages at 75 % and 90 %; the
homelab-control `/clean` endpoint exists for the operator to fire a manual prune
from the ntfy action button if desired (single click). Risk **H** on the auto
path is not worth the convenience.

### E. Auto-page on byparr memory leak ✅

**Status:** shipped Sprint 015 via cron weekly restart at Sun 03:30
**Trigger:** time-based — every Sunday at 03:30.
**Action:** `docker restart byparr`. Mitigates the Camoufox browser-process
memory leak that grows to ~30 % RAM over a week of bypass requests.
**Risk:** **L** — byparr is stateless; restart in < 10 s; off-hours window.
**Approval gate:** Sprint 015 G2 ramp date 2026-06-21 — revisit whether we still
need this once upstream fixes the leak (or replace byparr with the FlareSolverr
fork that doesn't ship Camoufox).
**Rollback:** delete the cron entry.

### F. Auto-disable broken indexer ❌

**Status:** **REJECTED**
**Proposal:** on Prowlarr indexer health-check failure, `PUT /api/v1/indexer/{id}`
with `enable: false`.
**Why rejected:** Prowlarr already has an internal disable mechanism that fires
on repeated 4xx/5xx, AND it sends a Connect notification on failure. Adding our
own auto-disable would race with Prowlarr's; the failure cases that bit us
historically (EZTV down 2 h, comes back fine) are exactly where we DON'T want
to disable.

## Out-of-scope (intentionally never auto-controlled)

- **Vault rotation.** Runbook 029. Always operator-driven.
- **Restore-drill execution.** Sprint 017 Story 6 schedules a quarterly _nudge_;
  the actual drill is operator-run. Auto-drilling against production risks
  overwriting live config on a misfire.
- **Container removal.** `docker rm` for stopped containers — runs only via
  Sunday `docker-prune.sh`, never on metric-triggered path.
- **Indexer credential rotation.** Vault path, operator-driven.

## Process for adding to this matrix

1. Open a sprint story.
2. Draft the row (trigger / action / risk / gate / rollback) and commit it here
   first.
3. Implement only after the row lands in `master`.
4. Ship with the toggle variable defaulted **off** for one week.
5. Flip the toggle default to **on** in a separate commit after the soak.
6. Update the status icon (⏳ → ✅).

This sequence is deliberate. The doc-first rule means the decision is reviewable
before any code lands, and the soak gate means we never auto-control on the same
day we ship the code.
