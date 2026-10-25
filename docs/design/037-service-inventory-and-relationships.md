# 037 — Service inventory and relationships

Repository inventory reviewed **2026-09-27** against role directories, shared
variables and compose templates. This describes ownership and dependencies, not
a fresh live-container count. Runtime observations are linked from
[PROJECT_INDEX](../../PROJECT_INDEX.md); diagrams live in
[architecture 001](001-architecture.md).

## Component ownership

| Group | Components | Repository owner |
| --- | --- | --- |
| Provisioning | Packages, system policy, firewall, storage, Docker | `common`, `security`, `storage`, `docker` |
| Network | LAN DNS, VPN, tailnet DNS, reverse proxy, SSO | `adguard`, `tailscale`, `tailscale_dns`, `traefik`, `authelia` |
| Media | qBittorrent, Sonarr, Radarr, Prowlarr, Jellyfin | `media` |
| Media requests | Jellyseerr | `jellyseerr` |
| Exports | NFS exports for Mac workers | `nfs_server` |
| Mac compute | byparr, runner supervisor | [mac-worker](../../mac-worker/README.md), OrbStack compose |
| Native Mac | NFS supervisor | scripts under `mac-worker` |
| Metrics | VictoriaMetrics, vmagent, node_exporter, cAdvisor | `victoriametrics`, `vmagent`, `node_exporter`, `cadvisor` |
| Logs | VictoriaLogs, Fluent Bit | `victorialogs`, `fluentbit` |
| Rules and delivery | Two vmalert evaluators, Alertmanager, ntfy bridge, ntfy | `vmalert`, `alertmanager`, `ntfy` |
| Dashboards and probes | Grafana, Beszel hub/agent, Uptime Kuma, Speedtest Tracker | `grafana`, `beszel`, `uptime_kuma`, `speedtest_tracker` |
| Management | Homepage, Portainer, FileBrowser, Control API, Watchtower | `homepage`, `portainer`, `filebrowser`, `homelab_control`, `watchtower` |
| Data and ML | PostgreSQL, MinIO, MLflow, Label Studio | `postgresql`, `minio`, `mlflow`, `label_studio` |
| Backup | Local snapshots, Drive sync | `backup`, `gdrive_backup` |
| Build and deployment | Zot registry, Pi CD, runner metrics | `zot`, `homelab_autodeploy`, `github_runner_metrics` |

All role paths are under [ansible/roles](../../ansible/roles). A role can manage
multiple containers or a host service; role count and container count are not
interchangeable. Maintainerr and Scrutiny are absent from the current role tree;
old PRDs/sprints mentioning them are historical. SMART monitoring is owned by `common`.

## Configuration sources

| Setting | Authority |
| --- | --- |
| Domain, LAN/Tailscale address, shared user and overrides | [group_vars/all/main.yml](../../ansible/inventory/group_vars/all/main.yml) |
| Service ports, memory ceilings, image defaults | Each role's `defaults/main.yml` and compose template |
| Browser routes and forward-auth selection | [Traefik defaults](../../ansible/roles/traefik/defaults/main.yml) and [Authelia defaults](../../ansible/roles/authelia/defaults/main.yml) |
| Scheduled host jobs | Role tasks and templates; inspect user crontabs as well as `/etc/cron.d` |
| Mac services and image builds | [runner compose](../../mac-worker/github-runner/compose.yml) and native launchd installers |
| Commands and deploy ordering | [Makefile](../../Makefile), `make help`, playbooks |
| Secrets schema | [vault.yml.example](../../ansible/inventory/group_vars/all/vault.yml.example); actual vault stays encrypted |

Browser hostnames use `example.internal`. Avoid copying port/image tables into
several guides: the templates are the maintained source and overrides affect
what is actually deployed. `make lint-ports` checks collisions.

## Dependency boundaries

- `media` supplies download/indexer APIs to Jellyseerr; byparr runs
  on the Mac and can break Pi-side grabs even while Pi containers stay healthy.
- PostgreSQL serves multiple tenants, including Authelia, MLflow and Label Studio.
  MLflow additionally uses MinIO.
- Traefik and consumers need reachable backend networks/endpoints. A published
  direct port can bypass browser SSO; see the
  [auth-boundary convention](../conventions/reverse-proxy-not-an-auth-boundary.md).
- Metrics and logs converge through vmalert → Alertmanager → ntfy bridge → ntfy.
  Beszel's independent freshness collector prevents a healthy web UI from hiding
  missing samples. Off-Pi failure detection is still an open workstream.

## Updates and resource limits

Watchtower updates eligible images according to their labels and configured
schedule. Locally built images such as MLflow have explicit update ownership; “Watchtower updates every container” is no longer accurate.
Check role labels and the live digest when diagnosing drift.

A version tag can still be mutable. Classify registry digests, version/line tags,
rolling tags and local builds separately before changing update policy. Stateful
services need migration and rollback evidence, not just a successful image pull.
Memory ceilings contain runaway workloads; they are not reservations. See
[container memory limits](../conventions/container-mem-limits.md).

## Verification

For repository facts, inspect defaults, overrides, compose mounts/networks,
playbooks and the associated tests. For live facts, inspect Docker status/digests,
host services, listening ports and actual workload reads with a timestamp.
Compare those observations to the selected deployment tag and controller state;
manual repairs may have changed live files after the last tagged release.
