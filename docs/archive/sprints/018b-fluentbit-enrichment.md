# Sprint 18b — Fluent Bit enrichment for LogsQL rules

> **Historical sprint record.** Statuses and checklists below describe the original
> work window, not today's backlog or deployment state. See
> [PROJECT_INDEX](../../../PROJECT_INDEX.md) for dated evidence.

**Status:** shipped 2026-05-22. Lua filter extended to enrich
`container_name` (via `config.v2.json` sidecar lookup, cached) and
`log_level` (bracketed-marker regex). `container_name` added to
`fluentbit_stream_fields` so LogsQL `_stream:{container_name=...}`
selectors hit the stream index. Verified on Pi: all 30 containers
enriched post-restart; live `[Error]`/`[error]` markers correctly
classified (AdGuard upstream timeout, Prowlarr FlareSolverr proxy
fail). `CHECK_LOGSQL=1 make verify-vmalert` will still warn for the
first ~24 h until enriched data fills the probe's lookback window —
that's expected, not a regression.
**Trigger:** `CHECK_LOGSQL=1 make verify-vmalert` reports 5/5
`container-error-rate.yml` rules return zero rows over 24 h. Rules are
loaded and inert.
**Scope:** one role edit (`fluentbit`), one deploy, one verify. No new
services. Targeted at < 2 h end-to-end.
**Why not in Sprint 18:** discovered at deploy time via the
`--check-logsql` probe. Sprint 18 deliberately ships the rules inert
rather than block on Fluent Bit work — the host-saturation PromQL rules
were the load-bearing half and they fire correctly.

## Root cause

Fluent Bit emits per-record fields `container_id` (12-char hash) and
`log_path` but **not** `container_name` or `log_level`. The LogsQL rule
filters reference `_stream:{container_name="sonarr"} log_level:error`,
which resolves to zero streams forever because neither label exists in
VictoriaLogs.

Confirmed via:

```bash
ssh pi@raspberrypi.local '
  curl -s --data-urlencode "query=_stream:{container_name=\"sonarr\"} log_level:error | stats count() as n" \
    http://logs.example:9428/select/logsql/query
' | head
# (empty)
```

And by inspecting a raw sample:

```bash
ssh pi@raspberrypi.local '
  curl -s --data-urlencode "query=_time:5m | head 1" \
    http://logs.example:9428/select/logsql/query
' | python3 -m json.tool
# → fields: _msg, _time, container_id, log_path, source — no container_name/log_level
```

## Two viable paths

### Path A — Add Fluent Bit enrichment (recommended)

Add a Docker-metadata enrichment so records carry `container_name`. Two
sub-options:

**A1 — `filter_docker` plugin** (built-in since Fluent Bit 1.0).
Resolves `container_id` → `container_name` via the Docker socket per
record. ~5 lines in `ansible/roles/fluentbit/templates/fluent-bit.conf.j2`:

```ini
[FILTER]
    Name        docker
    Match       *
    Docker_Mode On
    Container_ID_Field container_id
```

Cost: one Docker socket query per record. On this Pi (~200 records/sec
during downloads) that's measurable but well under the existing CPU
budget. The plugin caches per-container metadata so it's effectively one
socket call per container per restart.

**A2 — Lua mapper from a static file**. Generate `/etc/fluent-bit/container_map.lua`
at deploy time from `docker ps -a --format ...`, source it in a Lua filter.
Zero socket calls at runtime; stale on container recreate until next deploy.

A1 is the right default. A2 only wins if the socket-call cost shows up in
benchmarks (it won't on this load).

`log_level` is a separate problem — each container formats logs
differently. Three options:

1. **Per-image parser** in Fluent Bit (`[PARSER]` blocks matching the
   container's log format). Most accurate, most config to maintain.
2. **Regex parser at the top** that extracts `\b(?i)(error|warn|info|debug)\b`
   into `log_level`. Coarse but catches 80% of cases.
3. **Drop `log_level:error` from the rule, match `_msg` substring instead**
   (`_msg:~"error|exception|failed"`). Most resilient to log-format drift;
   noisier.

Pick option 2 for v1; revisit per-service parsers if false-positive rate
is high.

### Path B — Re-template rules to use `container_id`

Change every rule's filter from `container_name="sonarr"` to a regex
match against the current container's ID. Brittle: IDs change on
recreate, so every `make media` etc. would need a rule template
re-render via inventory facts. Rejected.

## Implementation plan (Path A1 + log_level regex)

1. **Edit `ansible/roles/fluentbit/templates/fluent-bit.conf.j2`** —
   add the `filter_docker` block right after the input section, before
   any existing filters. Add a `[PARSER] regex` block that pulls a
   coarse `log_level` from `_msg`.
2. **Add a Molecule scenario** (or extend the existing one) that
   asserts the rendered config parses cleanly via `fluent-bit -c <path> -D`.
   Not required to ship but cheap insurance.
3. **Deploy:** `make fluentbit` (gated by `DEPLOY_DEEP_CHECK=1`).
4. **Verify with the probe we already built:**

   ```bash
   ssh pi@raspberrypi.local 'CHECK_LOGSQL=1 /usr/local/bin/verify-vmalert.sh'
   ```

   Expect 5/5 LogsQL rules to report `matches >0 rows`. If any are still
   zero, inspect with `_stream:{container_name="<svc>"} | head 5` in the
   VictoriaLogs UI and adjust the parser or the rule filter.

5. **Tune thresholds** during the remaining soak window. Sprint 18 sets
   them at 10 errors/5 min as a guess; real baseline error rates will
   inform the post-soak commit (Sprint 18 Story 5 already plans this).

## Acceptance criteria

- `CHECK_LOGSQL=1 make verify-vmalert` reports `matches >0 rows` for all
  5 rule expressions over 24 h.
- At least one rule fires legitimately during the soak window (or each
  is provably quiet for a documented reason — e.g. service genuinely had
  no errors).
- The `container_name` label is queryable in the VictoriaLogs UI for at
  least 5 distinct services.
- Sprint 18 Story 3 status flips from `⚠ DEPLOYED BUT INERT` to
  `✅ DEPLOYED + FIRING` (or `✅ DEPLOYED + PROVABLY QUIET`).

## Rollback

`git revert` the Fluent Bit config change + `make fluentbit`. Rules stay
loaded but go back to inert. No data loss — VictoriaLogs retention is
30 days; missed enrichment can't be backfilled but nothing is broken.

## Out of scope

- **Per-service parsers** (one `[PARSER]` per container image). Open a
  follow-up only if the coarse `log_level` regex produces noisy alerts.
- **Structured JSON logging** at the container level — would obsolete
  the regex parser, but requires touching every service's compose env.
  Out of scope here; track in `docs/design/` if it becomes attractive.
- **OpenTelemetry collector** as a Fluent Bit replacement — multi-day
  change with no payoff for this homelab's scale.

## Links

- Sprint 18 — [`docs/archive/sprints/018-alerting-substrate.md`](018-alerting-substrate.md)
  (Story 3 carries the cross-reference)
- Verify probe — `scripts/verify-vmalert.sh` (`--check-logsql` flag)
- Soak + decom runbook — [`docs/archive/runbooks/030-vmalert-soak-and-decom.md`](../runbooks/030-vmalert-soak-and-decom.md)
- Fluent Bit Docker filter docs — <https://docs.fluentbit.io/manual/pipeline/filters/docker-metadata>
