# A liveness gauge is emitted at the START of the work, not the end

**Earned 2026-08-03** (a multi-hour catalog-discovery cron), reinforcing the repo's two
silent-cron rules. Hard rule #1 says *every backend cron must emit a counter*. This
convention adds the part that rule does not cover: **when**.

## The rule

For any job that can run longer than its own staleness threshold, split the emit:

| Gauge | Written | Answers |
| --- | --- | --- |
| `<job>_last_run_unix_seconds` | at **start** | "did the cron fire?" |
| `<job>_running` | 1 at start, 0 from the `EXIT` trap | "is it still working?" |
| `<job>_last_exit_code` | `-1` at start, `$?` from the trap | "how did it end?" |
| `<job>_task_success` / `_duration_seconds` | at **end** | "did the work succeed?" |

Liveness and outcome are different questions with different lifetimes. One file written once
at the end cannot answer both, and the failure is not symmetric — it fails toward *false
alarm on a healthy system*, which is the expensive direction.

## Why: the signal inverts

The discovery cron wrote a single `.prom` after both of its multi-hour passes finished.
Its `<job>_last_run_unix_seconds` gauge had HELP text saying *cron-liveness*, but it could
not advance until the whole pass completed.

Measured live 2026-08-03 09:10:

```text
<job>_last_run_unix_seconds  1785587488   # 2026-08-01 15:31
now                          1785737430   # 41 h 39 m later
stale-rule threshold             129600   # 36 h  -> FIRING
```

…while that day's pass had **started at 05:30**, completed its first stage (`ok=1 dur=2168s`)
at 06:06, and was still running the second. The box was working. The alert said it was
dead, and it named the cron — which was fine.

**The harder the job works, the deader it looks.** Any pass longer than the threshold reports
a healthy system as failed, which is precisely how an alert stops being believed. An alert
that is permanently lit is worse than no alert, because it also discredits the ones next to
it.

## Why: no trap means a death records nothing

The same script died on 2026-08-02 between its start marker and its first poll result — no
result line, no error, no end marker, and a `.prom` still holding the previous day's values.
Both of its guarded abort paths log *and* emit, so neither had fired. There was simply no
`trap`, so any exit outside those two branches was invisible.

Three things this requires, and each has bitten something in this repo:

1. **`trap ... EXIT`** — otherwise only the paths you predicted are observable.
2. **Explicit `TERM`/`INT` traps.** Bash does **not** run an `EXIT` trap when a
   non-interactive shell is killed by an untrapped signal. Without them, the OOM-kill and
   `systemctl stop` cases still record nothing.
3. **Arm the trap AFTER taking the lock.** A run that skips on a held lock owns nothing and
   must write nothing — otherwise it overwrites the live run's telemetry. A skip-clobber
   bug of exactly this shape (2026-08-03) fired two false alerts on a healthy system and
   destroyed a 4-hour run's metrics.

## Consequences to get right

- **Separate files.** Liveness has a different write lifecycle from results, and a mid-pass
  death must not leave a half-written merge of the two.
- **A metric name may appear in exactly one file.** The node_exporter textfile collector
  fails an **entire scrape** on a duplicate, so it drops every gauge on the host, not just
  yours. When you move a gauge between files, Ansible must delete the old file on deploy.
- **`-1` for "in flight"** keeps the exit-code series continuous, so the rule is
  `last_exit_code > 0` and a long run never trips it.
- **The mid-pass rule should fire SOONER than the stale rule.** "Started and died" is
  actionable; "hasn't run" is not. The discovery cron used 12 h in-flight against a
  36 h stale threshold.

## Testing it

Static assertions do not cover this. `assertIn("trap", template)` would have passed against
both defects above. Test it by **executing** the rendered script:

- a fake `curl` that snapshots the liveness file on its first invocation proves the file
  existed *before* the job touched its first dependency;
- comparing the gauge against wall-clock start/end proves it is a start stamp and not a
  completion stamp;
- sending a real `SIGTERM` mid-run proves the signal traps work.

Making script paths (`LOG`, textfile dir, lock file, module path) templatable with production
defaults is what makes this possible. That is a testability requirement, not a preference —
a hardcoded `/var/lib/node_exporter/textfile` cannot be exercised off the target host.
