# Project Index: raspi-homelab (public snapshot)

Reviewed snapshot of the private working repository, taken on **2026-10-25**
from commit `9f6a957`. Roles that serve private workloads are not included; their
tests, alert rules, Go subcommands and write-ups were removed with them, and
remaining references were generalized. The private repo's live status log is
not published.

## What is in the snapshot

| Area | Contents |
| --- | --- |
| Base provisioning | `common`, `security`, `storage`, `docker` (`make base`) |
| Network and access | `tailscale`, `tailscale_dns`, `adguard`, `traefik`, `authelia` |
| Media | `media` (Jellyfin, Sonarr, Radarr, Prowlarr, qBittorrent), `jellyseerr` |
| Dashboards and tools | `homepage`, `portainer`, `filebrowser`, `homelab_control`, `watchtower`, `beszel`, `uptime_kuma`, `speedtest_tracker` |
| Observability | `victoriametrics`, `victorialogs`, `vmagent`, `vmalert`, `alertmanager`, `grafana`, `node_exporter`, `cadvisor`, `fluentbit`, `ntfy` |
| Data / ML | `postgresql`, `minio`, `mlflow`, `label_studio` |
| Backup | `backup` (local, restore-first), `gdrive_backup` (off-site) |
| Delivery and runtime | `homelab_autodeploy` (tag-gated GitOps CD), `homelab_runtime` (Go agent install), `zot` (local OCI registry), `github_runner_metrics`, `nfs_server` |

40 roles and 42 playbooks under [ansible/](ansible). The Go runtime in
[runtime/](runtime) builds one `homelab-agent` binary with the `unhealthy-watchdog`,
`mac-presence`, `indexer-health`, `backup-observe`, `runner-metrics` and
`log-count` subcommands (see the [migration ledger](docs/operations/go-migration.md)).

## Code, validation and release

Run `make dev` then `make validate`: Ansible lint and syntax checks, the
Python suite, Bats shell tests, Go race tests/vet and ARM64 builds, port
uniqueness and ShellCheck over every shell template. See
[development checks](docs/operations/development-checks.md). At snapshot time
the Python suite had 681 passing tests, 1 skipped and 661 subtests; 20 Bats
tests and 42 Go test functions also passed.

Production deploys are tag-gated: a push to `master` runs CI only, and the
Pi-side controller deploys the commit the newest `deploy-v*` tag points at —
syntax check, `--check --diff`, apply, and rollback on failure. Host
provisioning (`base`) and the controller itself are never auto-deployed.

## Repository map

| Path | Responsibility |
| --- | --- |
| [ansible/inventory](ansible/inventory) | Hosts, shared variables, vault template |
| [ansible/roles](ansible/roles) · [playbooks](ansible/playbooks) | Declarative provisioning, services, cron/systemd jobs |
| [runtime](runtime) | Go `homelab-agent` used by cron jobs and alert emitters |
| [mac-worker](mac-worker/README.md) | OrbStack proxy/runner services and native NFS supervision |
| [scripts](scripts) · [tests](tests) | Operator utilities, deploy gates and regression checks |
| [Makefile](Makefile) | Current commands (`make help`) |
| [docs](docs/README.md) | Designs, runbooks, conventions, incidents and archived records |

## Architecture and constraints

- **Network:** only `example.internal` is current. Shared variables are in
  [group_vars/all/main.yml](ansible/inventory/group_vars/all/main.yml).
- **Storage:** `/srv` is on the root filesystem. Live application data under
  `/home/pi/docker` and image layers under `/var/lib/containerd` also consume
  that disk. Measure named subtrees; do not treat old size snapshots as capacity.
- **Second SSD:** `storage_ssd2_enabled: false`; powered-hub/boot acceptance remains
  in [design 042](docs/design/042-second-ssd-integration-plan.md).
- **Playback:** Jellyfin direct play; encoding runs on workers, not the Pi.
- **Deployment:** base provisioning precedes service roles; service metadata must
  not execute base roles implicitly. Production CD is tag-gated, with `base` and
  `homelab-autodeploy` denied automatic deployment.
- **Safety:** preserve media and recovery artifacts; scheduled production jobs
  need liveness metrics and tested decision logic.

The [architecture](docs/design/001-architecture.md) owns topology and deployment
flow; the [service inventory](docs/design/037-service-inventory-and-relationships.md)
owns component relationships.

## Navigation

- [Documentation index](docs/README.md) — current entry points and document lifecycle.
- [Incidents](docs/incidents/README.md) — failures, causes and durable lessons.
- [Operational discipline](docs/operations/ops-discipline.md) — cross-cutting deploy rules.
