# Documentation

Start with [PROJECT_INDEX](../PROJECT_INDEX.md) for dated system state.
Repository configuration is authoritative for declared behavior; dated reports
provide runtime evidence only for their observation window.

## Architecture and service setup

| Topic | Maintained entry point |
| --- | --- |
| Network, storage, media/monitoring flow, boot and tag-gated CD | [Architecture 001](design/001-architecture.md) |
| Component ownership and dependencies | [Service inventory 037](design/037-service-inventory-and-relationships.md) |
| Media hardlinks and playback | [Media stack 003](design/003-media-stack.md) |
| Proxy and remote access | [Traefik](guides/004-traefik.md), [Tailscale](guides/005-tailscale.md) |
| Mac workers | [Worker README](../mac-worker/README.md) |
| ML/data stack | [MinIO](../ansible/roles/minio/README.md), [MLflow](../ansible/roles/mlflow/README.md), [Label Studio](../ansible/roles/label_studio/README.md) |
| CI runners | [Runner guide](../mac-worker/github-runner/README.md), [design 039](design/039-mac-ci-runner.md) |
| UI assets | [Design system](design/design-system.md) |

Other [guides](guides/) and [design records](design/) include dated rationale
and proposals; implemented PRDs are
[archived](archive/README.md#product-requirements). Their status/date
matters: a proposal is not deployed behavior, and historical domain/image examples are not current configuration.

## Operations and conventions

- [Development checks](operations/development-checks.md) — locked toolchain and validation.
- [Container watchdog](operations/watchdog.md) — durable restart/failure signals,
  bounded commands, retained legacy rollback and monitoring-only deployment.
- [Operational discipline](operations/ops-discipline.md) — live-patch reconciliation,
  ownership, deploy hygiene and verification of assumptions.
- [Indexer health](operations/indexer-health.md) — Prowlarr and Byparr functional observation.
- [Cron liveness](conventions/cron-liveness-emit-at-start.md) — visible starts and failures.
- [Container memory limits](conventions/container-mem-limits.md) — containment caps.
- [Alert rule quoting](conventions/vmalert-rule-quoting.md) — consumer-valid rendered YAML.
- [Proxy/auth boundary](conventions/reverse-proxy-not-an-auth-boundary.md) — direct ports and SSO.
- [Pi USB power](conventions/pi4-usb-power-budget.md) — second-disk constraints.

## Recovery and runbooks

| Situation | Procedure |
| --- | --- |
| Boot or DNS outage | [002 — Boot recovery](runbooks/002-boot-recovery.md) |
| Backup restore | [028 — Backup and restore](runbooks/028-backup-restore.md) |
| Credential rotation | [029 — Vault rotation](runbooks/029-vault-rotation.md) |
| Drive OAuth client | [049 — Own client ID](runbooks/049-gdrive-own-client-id.md) |
| macOS NFS permissions | [053 — NFS supervisor permissions](runbooks/053-2026-09-26-mac-nfs-supervisor-permissions.md) |
| Mac engine/network failure | [044 — OrbStack](runbooks/044-2026-06-05-mac-worker-orbstack-networking.md) |
| Client CA trust / VPN DNS | [045 — CA trust](runbooks/045-2026-06-07-macos-homelab-ca-trust.md), [046 — VPN DNS](runbooks/046-2026-07-08-macos-corp-vpn-homelab-dns.md) |
| Notifications | [001 — ntfy phone setup](runbooks/001-ntfy-phone-setup.md) |

Dated session runbooks are in the [archive](archive/README.md#session-runbooks-and-one-off-procedures).
[Incident records](incidents/README.md) preserve failure causes and structural fixes;
they are not removed merely because the outage is old.

## Research and verification

| Observation window | Report |
| --- | --- |
| 27 September, first Go cutover | [Go wave 1: implementation and Pi benchmarks](research/2026-09-27-go-wave1.md) |
| 3 August | [Self-healing and automation audit](research/037-self-healing-and-automation-audit.md) |
| Evaluation only | [SSO identity provider options](research/036-sso-identity-provider-options.md) |

Dated observation reports are not standing health claims.

## Archive

Finished sprints, implemented PRDs, closed RCAs, session runbooks and superseded
reports live in [archive/](archive/README.md), which gives a one-line outcome and
the living successor for each. Move a document there once its work has shipped or
been abandoned and its lasting rule has a home elsewhere; delete it only when it
carries no decision or evidence worth keeping (Git retains it).

Current proposals include [second SSD](design/042-second-ssd-integration-plan.md),
[Go control platform](design/048-homelab-control-v2-platform.md) and
[external ML access](design/049-cloudflare-tunnel-mlflow-minio.md).

## Maintenance rules

1. Keep one authoritative home per fact: configuration in Ansible/Make, state in
   dated evidence linked from PROJECT_INDEX.
2. Merge duplicate overview documents and update incoming links. Remove obsolete
   redirects or plans only after preserving useful decisions in their successor;
   Git retains the previous version.
3. Preserve incident/evidence records and explicitly label superseded plans.
   Do not rewrite historical measurements or old domains as if they were current.
4. Use unique runbook numbers. Refer to a document by its full repository path
   (`docs/runbooks/020-known-log-noise.md`) or a relative Markdown link — never a
   bare number shorthand or a path wrapped across comment lines. In compose,
   config and Dockerfile artifacts keep pointers out of the rendered output
   (column-0 `{# Docs: … #}` line) so a document move restarts nothing.
5. Moving a document updates every reference in the same commit, including role
   comments, even if that widens the next deploy tag.
   [`tests/test_doc_references.py`](../tests/test_doc_references.py) fails on any
   dangling path or link; section anchors still need a manual check.

## Go runtime migration

[Accepted migration ledger and rollback procedure](operations/go-migration.md)
tracks the first collectors, deployment contracts and remaining waves.
