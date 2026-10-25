# Known-Benign Log Noise Catalog

Status: living document — update when adding/removing a suppression pattern or running the audit.

## Why this doc exists

Upstream services in this homelab occasionally emit high-volume `[Error]` / `[Fatal]` log lines that are functionally benign — they originate from inherited code paths, cosmetic NPEs in cold caches, or interop quirks between unrelated services. We suppress these at the Fluent Bit grep filter before they reach VictoriaLogs so:

- Raw-log exploration in Grafana stays signal-dense (operators dropping into LogsQL explorer otherwise hit the noise constantly).
- VictoriaLogs retains 30 days of logs on the SSD; suppression keeps the noise floor from dominating disk usage.
- When `vmalert` + Alertmanager land (Sprint 9 Phase 3, still pending), error-rate alerts will be first-class. This catalog is the noise floor below alert thresholds — suppress here so alerts there don't fire spuriously.

This is the documented form of a pattern that already exists: the first Exclude (node-exporter broken-pipe) was added ad-hoc with an inline comment. When a second instance showed up, the right move was to generalize: list-driven `fluentbit_docker_exclude_patterns` + this doc.

Implementation: `ansible/roles/fluentbit/defaults/main.yml` (variable) and `ansible/roles/fluentbit/templates/fluent-bit.conf.j2` (template loop).

## Inclusion criteria

A pattern qualifies for the Exclude list only if **all three** hold:

1. **Upstream code path with no actionable signal in this homelab.** It originates from a library, framework, or fork inheritance that we cannot reasonably fix in our own code. Examples: TCP write races between vmagent and node-exporter, upstream update-check pollers hitting GitHub API rate limits.
2. **Verified non-functional.** Affected operation still succeeds. For import errors: the file still lands in target. For API errors: the corresponding feature still works in the UI. Evidence is captured in the "Verification" column below.
3. **Sustained volume OR high noise-to-signal in raw exploration.** Either >50/week or appearing as the top-3 stem in a 7d audit query against the source container. One-off / transient errors stay un-suppressed even if benign — they are too informative to silence.

Cosmetic but rare errors (< ~20/week, not a top-3 stem) stay un-suppressed and are tracked in the "Watched but not yet suppressed" section. We'd rather see them once than blacklist a pattern and hide a real future failure mode of the same shape.

## Current entries

Patterns listed in `fluentbit_docker_exclude_patterns`. Order is presentation only; Fluent Bit `grep` evaluates all Excludes equally.

| Pattern (regex body, between `/…/`) | Source | First seen | Verification | Volume (window) | Reference |
|---|---|---|---|---|---|
| `error encoding and sending metric family.*write: (broken pipe\|connection reset by peer)` | `node-exporter` (host metrics) | pre-2026-04 | `curl /metrics` returns 0 errors; vmagent `up=1` continuously; metrics ingest verified in VictoriaMetrics | ~270 / scrape (high-frequency, every 30 s) | original inline comment at `fluent-bit.conf.j2` |
| `failed to collect filesystem stats.*no such file or directory` | cadvisor | 2026-05-16 | Container filesystem metrics still ingest correctly in VictoriaMetrics (`container_fs_*` series unbroken). Race between cAdvisor stat probe and Docker's overlayfs layer cleanup during container restart/recreate; long-standing upstream issue (google/cadvisor#2786). | ~2 221 events / 24 h sample (extrapolated, awaiting 7 d confirmation) | this doc audit 2026-05-16 |
| `Couldn't fetch app version status` | maintainerr | 2026-05-16 | Maintainerr's update-check polls GitHub API every ~30 s and gets rate-limited. UI works, rules execute, version-check feature has no operational impact. UI "check for updates off" toggle is wiped on container recreate (no env-var equivalent upstream). | ~1 500 events / 24 h sample (extrapolated, awaiting 7 d confirmation) | this doc audit 2026-05-16 |
| `Failed to retrieve GitHub releases` | jellyseerr | 2026-05-16 | Same as Maintainerr — GitHub API rate-limited update poller. Jellyseerr's UI, request flow, and Plex/Jellyfin sync are all unaffected. No env var to disable; config flag lives in DB inside mounted volume but resets to default on schema migrations. | ~1 500 events / 24 h sample (extrapolated, awaiting 7 d confirmation) | this doc audit 2026-05-16 |

## Watched but not yet suppressed

Patterns flagged by the audit query but below the inclusion threshold. Re-evaluate if volume climbs. No pattern is currently recorded here; when the audit finds one, add a table with Pattern, Source, Volume (window) and Note columns.

## Audit procedure

Run **quarterly** and after any `*arr` / observability stack major-version bump. Goal: catch new high-volume upstream noise patterns before they pollute the floor.

Queries target the unauthenticated VictoriaLogs endpoint on the homelab host (port 9428).

```bash
# (1) Top distinct [Error] stems in the Servarr containers over 7d
for c in sonarr radarr prowlarr; do
    cid=$(docker inspect "$c" --format '{{.Id}}' | head -c 12)
    echo "=== $c ($cid) ==="
    curl -s --data-urlencode \
      "query=container_id:${cid} AND _msg:\"[Error]\" AND _time:7d | stats by (_msg) count() as cnt | sort by (cnt) desc | limit 10" \
      http://localhost:9428/select/logsql/query
done

# (2) Volume check on each suppressed pattern — confirms suppression is actually firing.
for p in "failed to collect filesystem stats.*no such file or directory" "Couldn't fetch app version status"; do
    cnt=$(curl -s --data-urlencode "query=_msg:~\"${p}\" AND _time:7d | stats count() as cnt" http://localhost:9428/select/logsql/query)
    echo "$p → $cnt (should be 0 / very low)"
done
```

For each new top-3 stem, apply the inclusion criteria above. If qualified, add to `fluentbit_docker_exclude_patterns` and a row to "Current entries"; if not, add to "Watched but not yet suppressed".

## Long-term path: vmalert + Alertmanager (Sprint 9 Phase 3)

When `vmalert` lands, error-rate alerts become first-class: rules like "an *arr `[Error]` rate > 100/min over 10m" alert directly from VictoriaMetrics/VictoriaLogs. This suppression catalog stays as the noise floor below those thresholds — patterns here are guaranteed-benign and never alert; everything else does.
