# Sprint 015 — Quality Debt Payback

> **Historical sprint record.** Statuses and checklists below describe the original
> work window, not today's backlog or deployment state. See
> [PROJECT_INDEX](../../../PROJECT_INDEX.md) for dated evidence.
>
> **Durum:** Backlog (skeleton). Bireysel maddeler önceliklendirilebilir
> ve Sprint 11 (offsite) ile paralel ya da sonrası alınabilir.
> **Açılış sebebi:** PM review (Sprint 10 close-out, 2026-05-19) —
> birden fazla sprint boyunca ertelenen bakım maddelerinin durdurulduğu
> tek bir yerde toplanması gerekiyordu.
>
> **Niyet:** Bu doküman sadece liste değil — her maddenin **neden**
> öncelik kazandığı, **kabul kriteri** ne olduğu ve **hangi sprint'ten
> ertelendiği** kayıt altında.

---

## Sprint 10 follow-ups (verification + housekeeping)

### F1. 2026-05-24 cron-fire verification (PM W2)

**Effort:** 10 min on 2026-05-25.
**Why:** Sprint 10 deploy validated three ad-hoc runs; the
`0 3 * * 0` weekly cron has not yet fired. The Sprint 014 observation-
window protocol explicitly requires verifying the scheduled path.

**Verify:**

```bash
ssh pi@raspberrypi.local '
  sudo grep "$(date -u -d "last Sunday" +%Y-%m-%d)T03" \
    /var/log/homelab-backup.log
'
# Expect: one "preflight" event + 11 "service" events + one "summary"
# event between 03:00 and 03:05.
```

If absent: check `journalctl -u cron` for the same window and
investigate.

### F2. Workspace hygiene chore commit (PM W5)

**Effort:** 2 min. **Branch:** `chore/ruff-bump-v0.15.13`.

Two unstaged files have ridden across three sprints:

- `.pre-commit-config.yaml` — ruff hook `v0.15.10 → v0.15.13`
- `.claude/settings.local.json` — Claude Code permission additions

Commit as one chore. `chore` is in the conventional-commit allow-list.

### F3. Backup disk quota cap (PM W3)

**Effort:** 30 min.

Sprint 10's role defaults set retention by **time** (28 days) but not
by **size**. After Sprint 015 a plugin install or an application DB
bloat could push `/srv/backups/` past the disk-alert threshold without
warning. Add:

```yaml
# In ansible/roles/backup/defaults/main.yml
backup_disk_quota_gb: 50          # abort+ntfy if du > this
```

Plus a pre-flight gate in `homelab-backup.sh`:

```bash
used_gb=$(du -sBG "$BACKUP_ROOT" | awk '{print int($1)}')
if [ "$used_gb" -gt "$BACKUP_DISK_QUOTA_GB" ]; then
    ntfy_send "Backup ABORTED — quota" urgent ...
    exit 3
fi
```

Decide on policy: abort vs degrade (keep N most recent, prune older
beyond retention). Current recommendation: **abort + urgent ntfy**;
operator picks what to delete.

### F4. Disk-alert escape policy in design/024 (PM W3)

**Effort:** 15 min. Document-only.

Current behavior: re-fire on `+5 pp` since last fire OR 24 h cooldown.
This is in `common/templates/disk-alert.sh.j2` line 33 but not in
`docs/design/024-alert-discipline.md`. Add a row to the taxonomy table
explicitly so a future reader sees "disk is sustained-state with a
5 pp delta escape" without having to grep the script.

---

## Silent-failure ADR (PM I1, blocks Sprint 11)

### F5. docs/design/027-silent-failure-detection.md

**Effort:** 4 h. **Priority:** Should land **before** Sprint 11 starts
so the offsite role inherits the discipline. Otherwise we bake new
silent-failure modes into the freshly-deployed offsite path.

**Class of bug:** systems that report OK while doing nothing useful.
Canonical cases gathered in the homelab include:

1. **byparr** — `/docs` returns 200 while real bypass returns 500
   (PM W8). Healthcheck is upstream-blind. Fix: replace `/docs` probe
   with a small bypass-style request (e.g. `request.get` to a stable
   public URL with a short timeout).
2. **Backup recipe upload** — Sprint 11 will face exactly this for
   rclone (`rclone copy` exit 0 doesn't prove the bytes landed —
   need `rclone lsf` verification).
3. **qBittorrent fastresume duality** — the 2026-05-13 incident: the
   service reported "OK, torrents loaded" while operating on a stale
   path. Same shape.

**Fix shape (the universal pattern):**

```text
1. Validate the RESPONSE content, not just the exit code.
2. Compare against an expected invariant (row count, byte count, file
   presence) before declaring success.
3. Distinguish "happy zero" from "broken zero" in the status line.
```

The ADR establishes this pattern, then refers to it from each future
recipe (Sprint 11 rclone, Sprint 015 byparr healthcheck rewrite).

---

## Pipeline polish (Sprint 014 deferred)

### F6. M4 "cleared" notifications for sustained alerts

**Effort:** 1 h.

Currently sustained alerts such as `log_silence` fire and stay quiet for 24 h
but never emit a "resolved" event. Mirror the swap rule's clear-path:

```bash
# In pipeline-alerts.sh — after the existing fire logic
if [ "$was_firing" = "true" ] && [ "$condition_holds" = "false" ]; then
    ntfy_send "$name CLEARED" low "white_check_mark" \
        "$condition was true for $duration, now resolved"
    rm -f "$state_file"
fi
```

State file is the natural gate: presence = firing, absence = not.
Reuse `disk-alert.sh.j2`'s state-file shape, not a re-invention.

### F7. Idempotency drift fix — 6 tasks (PM W3 from Sprint 014)

**Effort:** 1.5–2 h (the realistic estimate, not the 30 min punch-list
claim).

Six tasks still report `changed=` on every re-run, including:

| Role | Task | Fix shape |
|---|---|---|
| storage | "Remove obsolete storage paths" | Convert to `state: absent` per path, OR `changed_when: false` (rmdir-fails-silently always changes when targets exist). |

Each idempotency fix is its own commit. Wrap as one PR but trace
back to root causes.

### F8. PRD-008 refresh — drop vmalert/Alertmanager (PM M5 from Sprint 014)

**Effort:** 30 min.

`docs/archive/prds/008-log-aggregation.md` still describes the vmalert +
Alertmanager future state that Sprint 014 explicitly chose NOT to
build (cron-script alerting won). Refresh the PRD so the next reader
doesn't re-litigate the decision. Cross-reference
`docs/design/024-alert-discipline.md` as the authoritative answer.

---

## byparr (PM W8)

### F9. byparr healthcheck — real bypass probe

**Effort:** 30 min.

Replace `curl :8191/docs` (returns 200 always) with a tiny bypass
request that exercises the real Camoufox/Firefox path:

```yaml
# In ansible/roles/media/templates/docker-compose.yml.j2 (byparr block)
healthcheck:
  test: ["CMD-SHELL", "curl -sf -X POST http://localhost:8191/v1 \
    -H 'Content-Type: application/json' \
    -d '{\"url\":\"https://httpbin.org/status/200\",\"cmd\":\"request.get\"}' \
    -m 20 | grep -q '\"status\":\"ok\"' || exit 1"]
  interval: 5m         # not 30s — bypass is expensive
  timeout: 30s
  retries: 2
  start_period: 60s
```

Risk: a heavier healthcheck increases byparr load. Counter-risk: the
weekly-restart cron is already a load contributor; doing one
bypass-probe every 5 min is cheaper than the unattended bypass
volume a since-disabled indexer used to drive.

**Out of scope:** fixing the upstream Camoufox leak — the reason that
indexer stays disabled; see `memory/byparr_leak_observation.md`.

---

## Cadence (PM W7)

### F10. Quarterly noise-floor audit cron stub (next: 2026-08-16)

**Effort:** 10 min.

Pattern: monthly cron that ntfys two weeks before the quarterly date.

```yaml
# In ansible/roles/homelab_control/templates/audit-reminder.sh.j2
# Cron: 0 9 1 2,5,8,11 *   # 09:00 on the 1st of Feb/May/Aug/Nov
```

The reminder body cites `docs/runbooks/020-known-log-noise.md` audit
procedure. Operator runs the procedure in the two-week window.

Deferred decision: do we want a Linear issue auto-created instead of
ntfy? Lower priority — ntfy is fine if the operator actually reads
notifications.

### F11. Sprint 014 Beszel LoadAvg5 stress smoke-test (PM I5, never run)

**Effort:** 10 min, one-shot.

```bash
ssh pi@raspberrypi.local 'stress --cpu 4 --timeout 360'
```

Watch ntfy for the LoadAvg5 alert. Expected fire at ~5 min in (the
`min: 5` field in the Beszel rule). If it doesn't fire, the rule is
silently broken — like the disk-alert was silently in
fired-state for two sprints (PM W3).

This belongs in Sprint 015 NOT Sprint 11 because it's about the
existing Beszel rule, not about offsite backups.

---

## Acceptance criteria for Sprint 015

Not all items must land in one sprint — this is a backlog, not a
single-sprint plan. Per-item acceptance:

- F1, F11: one-off verification, output captured in memory/MEMORY.md
- F2, F4, F8: one chore-or-docs commit each
- F3, F6, F9: code + test + deploy + idempotency check
- F5: ADR landed in `docs/design/027-*` + cross-references from
  each case above
- F7: six small commits, one per task
- F10: Ansible role/task + first reminder confirmed received

## Out of scope

- Sprint 11 (offsite) — separate plan, sequenced after F5 (ADR)
- byparr upstream Camoufox leak — not us
- Alert architecture rewrite — done (Sprint 014, see
  [`docs/design/024-alert-discipline.md`](../../design/024-alert-discipline.md))
