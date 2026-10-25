# Container watchdog

The five-minute root cron calls `/usr/local/bin/unhealthy-watchdog.sh` under a
nonblocking flock and a 240-second outer ceiling. The selected Go engine is
`homelab-agent unhealthy-watchdog`; shell invokes it and the shared audited
ntfy helper. `common` probes the installed subcommand before selecting it.

The policy remains: three consecutive Docker-unhealthy polls trigger a restart;
five successful restarts exhaust the budget until 24 hours after the last
successful restart. Absence from Docker's unhealthy list resets consecutive count
and urgent cooldown as in the legacy policy. A successful command is not readiness
proof; the next poll
determines recovery.

Both Docker inventories have ten-second bounds and are validated before state
mutation. Failed, malformed or inconsistent inventories fail the scan; they never
authorize orphan-state GC. Every existing live state is validated before any
restart. Docker restart is bounded to 45 seconds, notification delivery to 15
seconds (the shared helper's curl to ten seconds), and command output to 1 MiB.

The four-line `watchdog-<container>.state` contract is retained; three-line old
files remain accepted. New writes are atomic and 0600. Durable counters and
current observations are private in `/var/lib/homelab/watchdog/status.json`
(0600, directory 0700). Restart intention and an unconfirmed-failure flag are
persisted before Docker. A failed or interrupted restart stays visible until a
successful command or actual running/healthy observation; stopped and starting
containers do not clear that flag. Cumulative failures survive recovery.
The legacy log remains `/var/log/unhealthy-watchdog.log`.

Failed-command urgent messages now respect the existing six-hour cooldown, as
budget-exhausted messages already did. Restart attempts retain the five-minute
cadence and successful-restart budget semantics. Failed notification helpers have
their own counter and shared audit logs. Successful HTTP responses do not prove
Android background delivery.

`/var/lib/node_exporter/textfile/watchdog.prom` publishes scan timestamp/success,
Docker-read success, persistent restart/notification counters and current
per-container unhealthy/consecutive/restart-budget/failure gauges. The shared
directory keeps its `docker_user` owner. Error scans retain previous observations
behind failed-read flags. State/publication errors return nonzero; stale/absence
rules cover a job unable to publish. Four rules cover missing/stale observations,
failed scans, active failed/unconfirmed restarts and exhausted budgets. Existing
generic health rules remain. A restart cannot recreate a missing Docker endpoint.

For rollback, set `common_watchdog_engine: legacy` in shared inventory group vars
so common and vmalert both see it. Deploy common's **monitoring tag** and vmalert.
The wrapper selects retained `unhealthy-watchdog-legacy.sh`, and new rules render
`groups: []`. Preserve the ledger and all legacy states. Switch the consumer
before choosing a runtime without this subcommand. Legacy rollback restores its
older observability and failed-restart notification behavior.

Tags deploy runtime and vmalert; base remains denied automatic deployment.
The authorized rollout applies only common's monitoring tag after syntax and
deep-check once the installed runtime probe succeeds. This is not full provisioning.
For first provisioning without a runtime binary, explicitly select `legacy` for
base, install `homelab-runtime`, then select Go and apply monitoring/vmalert. An
explicit Go selection fails its consumer probe until that prerequisite is present.

Validation covers legacy-shell differential fixtures, actual command cancellation,
invalid/inconsistent inventories, publication-before-action, durable counters,
urgent cooldown, budget suppression and private publication.
