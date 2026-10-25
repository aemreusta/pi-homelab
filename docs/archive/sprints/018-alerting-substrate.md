# Sprint 018 — Alerting Substrate (vmalert + Alertmanager)

> **Historical sprint record.** Statuses and checklists below describe the original
> work window, not today's backlog or deployment state. See
> [PROJECT_INDEX](../../../PROJECT_INDEX.md) for dated evidence.

**Status:** planned (2026-05-22). Roles + rules + ntfy bridge committed in
this sprint; **deploy + 1-week soak gated on operator approval** of the rule
choices in `host-saturation.yml.j2` + `container-error-rate.yml.j2`.

**Goal.** Stand up vmalert + Alertmanager + a webhook→ntfy bridge so the
observability substrate (vmagent + VictoriaMetrics + VictoriaLogs + Fluent Bit,
running since 2026-04-28) actually evaluates alert rules. Closes G-B
(observability substrate without rules), G-E (no rules for non-pipeline
containers), G-F (no notification-delivery proof beyond local witness), and
issue #11 (Beszel threshold recalibration — needs baseline data this
substrate will produce).

**Why not bundle with Sprint 17.** Sprint 17 was all shell + docs — deployable
the same day with zero new services. Sprint 18 deploys two new containers
and runs a parallel-soak before decom — the cadence is fundamentally
different. Keeping them separate means each ships on its own clock.

## Stories

| # | Story | Status | Effort |
|---|---|---|---|
| 1 | vmalert + Alertmanager Ansible roles | **SCAFFOLDED** | half day |
| 2 | Port host alerts (cpu-load, pid-budget, disk, memory) to vmalert PromQL | **SCAFFOLDED** | half day |
| 3 | LogsQL error-rate rules for non-pipeline containers (G-E) | **SCAFFOLDED** | 2 h |
| 4 | ntfy-bridge: Alertmanager webhook → audit-log + ntfy (G-F) | **SCAFFOLDED** | 1 h |
| 5 | Beszel threshold recalibration (#11) | _gated on Story 6 soak_ | 1 h |
| 6 | 1-week parallel-run + cron decom toggle | _gated on operator deploy_ | observation |
| 7 | Sprint 9 Phase 4 — Beszel vs vmalert decision doc | **SHIPPED** (separate doc) | 30 min |

## Story details

### Story 1 — vmalert + Alertmanager roles — ✅ SCAFFOLDED

**Roles shipped** (committed but not yet deployed):

- `ansible/roles/vmalert/` — wraps `victoriametrics/vmalert:latest`. Two
  containers (vmalert v1.x supports one `--datasource.url` per process):
  - **vmalert** (port 8880) — datasource VictoriaMetrics, evaluates the
    PromQL rules under `rules/metrics/`.
  - **vmalert-logs** (port 8881) — datasource VictoriaLogs,
    `--rule.defaultRuleType=vlogs`, evaluates LogsQL rules under `rules/logs/`.
  Both notify the same Alertmanager. Hardened (read-only fs, ALL caps
  dropped, pids_limit 128, mem 192M, no-new-privileges).
- `ansible/roles/alertmanager/` — wraps `prom/alertmanager:latest` + a
  Python-stdlib `ntfy-bridge` sidecar (image `python:3.13-alpine`). Port 9093.
  Bridge listens on :9097 inside the observability network. Same hardening
  profile.
- `ansible/playbooks/vmalert.yml` + `playbooks/alertmanager.yml`.
- `make vmalert` and `make alertmanager` targets. `make observability` updated
  to include both.

**Deploy sequence (operator step):**

```bash
DEPLOY_DEEP_CHECK=1 make vmalert        # rules + two containers
DEPLOY_DEEP_CHECK=1 make alertmanager   # AM + ntfy-bridge + verify-vmalert.sh
make verify-vmalert                     # end-to-end synthetic alert probe
CHECK_LOGSQL=1 make verify-vmalert      # also probes each LogsQL rule against live VL
```

Expected: `make verify-vmalert` exits 0 with audit-log line
`script=vmalert step=alert status=ok alertname=VerifyVmalertProbe …` and a
single ntfy notification arrives on the operator's phone (safe to dismiss).
If the probe fails, the script prints which component is unhealthy.

**Why two passes.** The default probe injects a synthetic alert at the
Alertmanager API — it confirms AM → bridge → ntfy → audit-log works but
bypasses vmalert entirely. A rule whose filter expression doesn't resolve
to ANY log streams (e.g. Fluent Bit emits `level=error` instead of
`log_level=error`) would silently never fire forever. `CHECK_LOGSQL=1`
queries each rule's filter against the live VictoriaLogs over 24 h and
warns when zero rows match — catching the silent-failure mode before the
soak window starts.

### Story 2 — Host-saturation PromQL rules — ✅ SCAFFOLDED

`ansible/roles/vmalert/templates/rules/host-saturation.yml.j2`:

| Alert | Mirror of | PromQL |
|---|---|---|
| `CPULoadHigh` / `CPULoadCritical` | `cpu-load-alert.sh` | `node_load5 / count(...) >= ratio` |
| `SwapUsageHigh` | `memory-alert.sh` swap branch | `(SwapTotal - SwapFree) / SwapTotal * 100` |
| `OOMKilled` | `memory-alert.sh` OOM branch | `increase(node_vmstat_oom_kill[1h]) > 0` |
| `SrvDiskHigh` / `SrvDiskCritical` | `disk-alert.sh` | `(size - avail) / size * 100` |
| `ContainerPIDBudgetHigh` / `Critical` | `pid-budget-alert.sh` | `container_processes / container_spec_pids_limit_total * 100` |

Thresholds read from the same `common_*_pct` / `common_*_ratio` defaults that
the cron scripts use — single source of truth across both alert paths during
the parallel-run.

**Known gaps to validate during soak:**

- `node_vmstat_oom_kill` may not exist on Pi's kernel without textfile
  collector — verify after deploy.
- `container_spec_pids_limit_total` label set depends on cAdvisor build; the
  `unless on(id) container_spec_pids_limit_total == 0` guard skips containers
  without a PID limit set.
- A `ContainerUnhealthy` rule is commented out — Docker health status isn't
  exported by default cAdvisor; add a textfile collector probe if we want
  this rule.

### Story 3 — LogsQL error-rate rules — ⚠ DEPLOYED BUT INERT (Sprint 18b follow-up)

`ansible/roles/vmalert/templates/rules/container-error-rate.yml.j2`. Five
rules covering Sonarr / Radarr / Prowlarr / Traefik / Jellyseerr. Threshold:
≥ 10 error-level log lines in 5 min (Traefik uses ≥ 25 HTTP-500 hits — different
volume profile).

Rules use `type: vlogs` at the group level AND the `vmalert-logs` container
has `--rule.defaultRuleType=vlogs` on the command line — belt + braces.
`vmalert-logs` is its own process with `--datasource.url=http://victorialogs:9428/`
so the LogsQL pipe operators (`stats by … | filter total:>N`) resolve
against the right backend. See
[the official multi-datasource guidance][vmalert-multi-ds].

[vmalert-multi-ds]: https://docs.victoriametrics.com/victorialogs/vmalert/

**Tuning expected:** the 10/5min threshold is a guess. After 1 week of
parallel-run we'll see real baseline error rates per service and adjust.
Whatever thresholds emerge get committed via a follow-up sprint.

**⚠ 2026-05-22 deploy finding:** `CHECK_LOGSQL=1 make verify-vmalert`
confirmed 5/5 rules return zero rows over 24 h. Fluent Bit emits
`container_id` (12-char hash) + `log_path` per record but does NOT emit
`container_name` or `log_level`. Two fixes available, both Sprint 18b:

- **Add Fluent Bit enrichment** — `docker_metadata` filter (queries the
  Docker socket per record) OR a lua filter that maps `container_id` →
  `container_name` from a static map regenerated each deploy. Adds a
  proper `container_name` field; rules work unchanged.
- **Re-template rules to use `container_id`** — quick but brittle (IDs
  change on container recreate).

For the soak window: rules stay loaded but inert. The PromQL rules
(host-saturation) WILL fire on real conditions — Story 3 just isn't
contributing data yet. Story 6 (cron decom) is NOT gated on this.

### Story 4 — ntfy-bridge — ✅ SCAFFOLDED

`ansible/roles/alertmanager/templates/ntfy-bridge.py.j2` is a zero-dep
Python stdlib HTTP server. Receives Alertmanager webhooks at `/`, sends
one ntfy per alert with severity-mapped Priority/Tags, and appends a
structured logfmt line to `/var/log/alert-audit.log` (same file the
host-side scripts write via `alert-lib.sh`). Closes G-F.

**Audit line format** (matches alert-lib convention):

```text
<ISO8601> script=vmalert step=alert status=<ok|fail> alertname=<...> \
    severity=<warn|crit> service=<...> firing=<firing|resolved> http=<ntfy_code>
```

Why a custom shim instead of `prometheus-msteams` or `karma`: those don't
write our audit log. The shim is 100 lines of stdlib, runs as nobody (UID
65534), read-only filesystem, dropped all caps. Operationally cheaper than
adopting an off-the-shelf bridge we'd have to fork to add the audit hook.

### Story 5 — Beszel threshold recalibration — ⏳ GATED ON SOAK

Beszel's current thresholds are uncalibrated (issue #11). With vmalert
producing 1-min-resolution PromQL data, we can compute p95 / p99 for each
metric over a 1-week window and set Beszel's thresholds to match.

**Not in this sprint.** Open a follow-up commit after 2026-05-29 (1 week from
deploy) that updates `ansible/roles/beszel/defaults/main.yml` with the new
threshold values, with a comment linking the vmalert query that produced
the p95.

### Story 6 — Parallel-run + cron decom — ⏳ GATED ON OPERATOR DEPLOY

**During soak (week 1):**

- Both alert paths active. Cron scripts write to `/var/log/alert-audit.log`
  via `alert-lib.sh`. vmalert→Alertmanager→ntfy-bridge writes to the SAME
  file. Different `script=` tag (`cpu-load-alert` vs `vmalert`) so they're
  distinguishable.
- Daily: grep for `vmalert step=alert status=fail` to catch ntfy bridge
  delivery failures.
- Verify each rule has fired at least once or is provably quiet. A rule
  that has never fired during a week of normal operation either has the
  threshold wrong or is a dead rule.

**Decom step (week 2):**

Flip `common_use_vmalert: false` → `true` in `group_vars/all/main.yml`,
then `make base --tags monitoring`. The cron `state:` clauses already shipped
in this sprint will remove the disk-alert, memory-alert, cpu-load-alert,
and pid-budget-alert entries. **unhealthy-watchdog stays as cron** under
all configurations — it's an action-side automation (`docker restart`),
not a metrics alert, and has no vmalert equivalent.

**Rollback:** flip the toggle back to `false` and re-deploy. Scripts are
still on disk; cron entries get re-added in one Ansible run.

### Story 7 — Sprint 9 Phase 4 (Beszel vs vmalert) — ✅ SHIPPED

Spec: [`docs/archive/design/031-beszel-vs-vmalert.md`](../design/031-beszel-vs-vmalert.md).
Decision deferred to 2026-06-12 (2 weeks after Sprint 18 deploy + 1 week
post-soak buffer). The decision doc captures the criteria; the recommendation
defaults to "keep Beszel for the dashboard / UI, use vmalert for the
alert-rule path" unless something surprising shows up during soak.

## Out of scope (Sprint 19 candidates)

- Migrate Alertmanager → Grafana Alerting (would centralize on one UI but
  loses Alertmanager's silence / inhibition tooling — not yet justified).
- Custom Grafana dashboard for vmalert rule status (vmalert has its own
  `/api/v1/rules` UI on port 8880; sufficient for now).
- Per-receiver routing (split severity=crit → urgent ntfy vs warn → default
  ntfy) — defer until we have a week of real fire-rate data.
- High-availability Alertmanager — single-node Pi, single Alertmanager.

## Exit criteria

Sprint 18 closes when:

1. Stories 1-4 are deployed on Pi and `make observability` returns clean.
2. Story 6: 1 week of parallel-run, no missed alerts vs cron, cron entries
   gated off via `common_use_vmalert: true`, and a passing audit-log entry
   for every rule that's expected to fire.
3. Story 5: Beszel defaults updated with vmalert-derived p95 values.
4. Story 7: decision recorded in 031-beszel-vs-vmalert.md.

After (1–4), open Sprint 19 (or close the operations book on alerting for
this homelab cycle).
