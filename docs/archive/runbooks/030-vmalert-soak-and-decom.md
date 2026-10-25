# Runbook 030 — vmalert soak + cron-alert decom

> **Completed transition record.** `common_use_vmalert: true` is already set in
> shared inventory. This is the original soak/decommission procedure, not an
> instruction to repeat the transition.

**Purpose:** Document the operator-facing procedure for running vmalert in
parallel with the host-side cron alerts for 7 days, then flipping
`common_use_vmalert: true` to decommission the cron scripts.

**Audience:** operator, post-Sprint-18 deploy.

**Deploy date:** 2026-05-22.
**Earliest decom date:** 2026-05-29 (7-day soak window minimum).

## The two paths running in parallel

| Path | Trigger | Audit log tag | Owner |
|---|---|---|---|
| Cron-based (legacy) | Per-script cron every 5-30 min | `script=disk-alert`, `script=memory-alert`, `script=cpu-load-alert`, `script=pid-budget-alert` | `common` role |
| vmalert (new) | Continuous PromQL/LogsQL evaluation | `script=vmalert` | `vmalert`/`alertmanager` roles |

Both write to `/var/log/alert-audit.log` via the shared logfmt convention.
That makes side-by-side diffs trivial:

```bash
# All vmalert-originated alerts today
grep 'script=vmalert' /var/log/alert-audit.log | grep "$(date +%Y-%m-%d)"

# All cron-originated alerts today
grep -E 'script=(disk|memory|cpu-load|pid-budget)-alert' /var/log/alert-audit.log \
  | grep "$(date +%Y-%m-%d)"
```

## Daily soak checks (~2 min/day)

Run on the Pi or via Tailscale:

```bash
ssh pi@raspberrypi.local '
  echo "--- bridge delivery failures today ---"
  grep "script=vmalert step=alert status=fail" /var/log/alert-audit.log \
    | grep "$(date +%Y-%m-%d)" || echo "(none — good)"

  echo "--- rule fires by alertname today ---"
  grep "script=vmalert" /var/log/alert-audit.log \
    | grep "$(date +%Y-%m-%d)" \
    | grep -oP "alertname=\K\S+" | sort | uniq -c

  echo "--- cron-side fires today (for comparison) ---"
  grep -E "script=(disk|memory|cpu-load|pid-budget|unhealthy-watchdog)-alert" \
    /var/log/alert-audit.log | grep "$(date +%Y-%m-%d)" \
    | grep -oP "script=\K\S+" | sort | uniq -c
'
```

**Red flags to escalate immediately:**

- `status=fail` lines from vmalert → ntfy bridge is broken or ntfy is
  unreachable. Run `make verify-vmalert` end-to-end and check
  `docker logs ntfy-bridge`.
- A cron alert fires for a condition vmalert misses (or vice versa).
  This is the whole point of the soak — record the mismatch in the
  incident log.
- vmalert-logs container restarts unexpectedly. Check
  `docker logs vmalert-logs` for LogsQL parse errors.

## End-of-soak checklist (2026-05-29 or later)

Before flipping the toggle:

1. **No `status=fail` lines** for the last 24 h in `alert-audit.log`.
2. **Each PromQL rule has either fired at least once OR is provably
   quiet** (no condition exceeded its threshold all week). A rule that
   never fired AND never had its underlying metric near threshold is
   either dead or its threshold is wrong.
3. **Sprint 18b** is either shipped (LogsQL rules now firing) or
   explicitly accepted-as-inert for this cycle.
4. **At least one synthetic verify run** within the last 24 h:
   `make verify-vmalert` returns 0.

If all four are green:

## Decom step

```bash
# 1. Flip the toggle.
$EDITOR ansible/inventory/group_vars/all/main.yml
#    common_use_vmalert: false   →   common_use_vmalert: true

# 2. Re-deploy the common role's monitoring tasks. The cron `state:` clauses
#    already shipped in Sprint 18 will remove the gated cron entries.
DEPLOY_DEEP_CHECK=1 make base

# 3. Verify cron entries are gone.
ssh pi@raspberrypi.local 'sudo crontab -l -u root | grep -E "disk-alert|memory-alert|cpu-load-alert|pid-budget-alert"'
#    → expect empty (no output)

# 4. Verify vmalert + Alertmanager + bridge still healthy.
make verify-vmalert

# 5. Commit the toggle flip.
git add ansible/inventory/group_vars/all/main.yml
git commit -m "feat(common): flip common_use_vmalert=true — decom cron alerts post-soak"
git push origin master
```

**What stays under all configurations:**

- `unhealthy-watchdog.sh` — it's an action-side automation
  (`docker restart`), not a metrics alert. No vmalert equivalent.
- `heartbeat.sh` — dead-man's switch. vmalert IS one of the things it's
  watching, so it can't replace itself.
- `smart-check.sh` — daily SMART probe. No vmalert equivalent because
  no metric exporter for SMART is deployed.
- `docker-prune.sh` — weekly maintenance, not an alert.
- `restore-drill-nudge.sh` — quarterly reminder, not an alert.

## Rollback

If anything regresses after the flip:

```bash
# 1. Flip the toggle back.
$EDITOR ansible/inventory/group_vars/all/main.yml
#    common_use_vmalert: true   →   common_use_vmalert: false

# 2. Re-deploy.
DEPLOY_DEEP_CHECK=1 make base

# 3. Verify cron entries are back.
ssh pi@raspberrypi.local 'sudo crontab -l -u root | grep -cE "disk-alert|memory-alert|cpu-load-alert|pid-budget-alert"'
#    → expect 4

# 4. Commit the revert with the reason.
git revert <flip-commit-sha>
git push origin master
```

Scripts under `/usr/local/bin/` are never removed by the toggle — only
the cron entries. Rollback is reversible in one Ansible run.

## Common questions

**Q: Can I deploy Sprint 18b (Fluent Bit enrichment) during the soak?**

Yes. The soak is for the PromQL rules. LogsQL rules going from inert →
firing during the soak is a *good* outcome, not a soak restart.

**Q: What if a new alert rule needs adding mid-soak?**

Add it. The soak counts from the date the last *infrastructure* change
landed (vmalert + AM containers), not from rule-file churn. Rule files
are pure data; vmalert auto-reloads them.

**Q: What if Beszel and vmalert fire on the same condition?**

That's expected — they're both running. The Beszel-vs-vmalert decision
(`docs/archive/design/031-beszel-vs-vmalert.md`) is parked at 2026-06-12 with
soak data to inform it.

**Q: Should the cron scripts under `/usr/local/bin/` be deleted after
decom?**

No. They're managed by Ansible. The toggle removes the cron *entries*,
not the files. Keeping the files on disk makes rollback a one-toggle
operation rather than a re-deploy of the common role.

## Links

- Sprint 18 — [`docs/archive/sprints/018-alerting-substrate.md`](../sprints/018-alerting-substrate.md)
- Sprint 18b — [`docs/archive/sprints/018b-fluentbit-enrichment.md`](../sprints/018b-fluentbit-enrichment.md)
- Beszel-vs-vmalert decision — [`docs/archive/design/031-beszel-vs-vmalert.md`](../design/031-beszel-vs-vmalert.md)
- Verify probe — `scripts/verify-vmalert.sh`
