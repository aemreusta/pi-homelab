# Archive

Completed, superseded or abandoned documents. They are kept for rationale and
incident history; none of them describes current behavior or open work. Current
state lives in [PROJECT_INDEX](../../PROJECT_INDEX.md). Checklists and
status lines inside archived files are frozen at their last edit — an archived
"in progress" is not a claim that work is still running. Documents deleted
outright remain recoverable from Git history.

The one-line outcome below is the summary; open the file only when you need the
original reasoning or evidence.

## Design records

| Record | Outcome | Living successor |
| --- | --- | --- |
| [006 Monitoring](design/006-monitoring.md) | First Beszel/Uptime/ntfy monitoring baseline; counts and gaps are from its window | [Architecture 001](../design/001-architecture.md), [inventory 037](../design/037-service-inventory-and-relationships.md) |
| [018 Container hardening](design/018-container-hardening.md) | One-time hardening pass (read-only, cap_drop, no-new-privileges) with the upstream bugs it hit | Role compose templates |
| [025 ntfy DNS name](design/025-ntfy-dns-name.md) | Single ntfy hostname over split DNS; shipped 2026-05-18, domain since moved to `example.internal` | `ntfy_external_*` vars |
| [031 Beszel vs vmalert](design/031-beszel-vs-vmalert.md) | Decision gate never scored; both kept, Beszel has independent freshness checks | — |
| [038 homelab → example.org](design/038-homelab-io-domain-migration.md) | Dual-running migration shipped 2026-06-24, then replaced by the 2026-07-15 `example.internal` clean cut | — |

## Product requirements

Every PRD is implemented, retired or superseded; none carries open scope.

| PRD | Outcome |
| --- | --- |
| [002 Control API enhancements](prds/002-control-api-enhancements.md) | Security, `/storage`, `/junk`, cron endpoints shipped |
| [004 AI/ML infrastructure](prds/004-aiml-infrastructure.md) | PostgreSQL, MinIO, MLflow, Label Studio shipped 2026-06-07 |
| [005 External access / CI](prds/005-external-access.md) | CI runner shipped ([design 039](../design/039-mac-ci-runner.md)); Cloudflare tunnel remains a proposal ([design 049](../design/049-cloudflare-tunnel-mlflow-minio.md)) |
| [008 Log aggregation](prds/008-log-aggregation.md) | VictoriaMetrics/Logs + Grafana stack shipped |
| [009 Uptime Kuma](prds/009-uptime-kuma.md) | Implemented |
| [010 Speedtest Tracker](prds/010-speedtest-tracker.md) | Implemented |
| [011 Scrutiny](prds/011-scrutiny.md) | Retired 2026-06-04 (never functional over USB); SMART via cron collector |
| [013 Zot registry](prds/013-zot-registry.md) | Shipped 2026-06-23 |

## Session runbooks and one-off procedures

| Runbook | Outcome | Rule it left behind |
| --- | --- | --- |
| [030 vmalert soak](runbooks/030-vmalert-soak-and-decom.md) | `common_use_vmalert: true`; host cron alerts decommissioned | — |
| [039 Uptime Kuma setup](runbooks/039-2026-05-31-uptime-kuma-setup.md) | 25 monitors + ntfy + maintenance window; done 2026-05-31 | — |

## Research and audits

| Report | Outcome |
| --- | --- |
| [027 Makefile deploy gate](research/027-makefile-deploy-gate-bug.md) | `DEPLOY_GATE` dropped `$(PLAYBOOK)`; fixed 2026-05-22 |

The Go wave 1 report stays live because the
[migration ledger](../operations/go-migration.md) depends on it.

## Sprints

These records describe earlier work windows; archiving a sprint does not
certify every checkbox.

- [Sprint 1: Stability & Security](sprints/001-stability-security.md)
- [Sprint 3: AI/ML Foundation](sprints/003-aiml-foundation.md)
- [Sprint 4: External Access & CI/CD](sprints/004-external-access.md)
- [Sprint 8: Infrastructure Hardening](sprints/008-infrastructure-hardening.md) — completed 2026-03-27
- [Sprint 9: VictoriaLogs + VictoriaMetrics + Grafana](sprints/009-victoria-stack.md)
- [Sprint 10: Automated Backups](sprints/010-automated-backups.md)
- [Sprint 015: Quality debt payback](sprints/015-quality-debt-payback.md)
- [Sprint 018: Alerting substrate (vmalert + Alertmanager)](sprints/018-alerting-substrate.md)
- [Sprint 18b: Fluent Bit enrichment](sprints/018b-fluentbit-enrichment.md)
- [Sprint 020: Observability gap-fillers](sprints/020-observability-gap-fillers.md)

Sprint 013's encrypted off-site proposal was replaced by
[design 043](../design/043-offsite-backup-google-drive.md) (Google Drive,
unencrypted browsable backups) and removed; do not apply its crypt setup.
