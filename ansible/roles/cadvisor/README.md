# cadvisor

Google cAdvisor exports per-container CPU, memory, network and block-I/O
metrics for vmagent to scrape into VictoriaMetrics.

## What it deploys

A single `ghcr.io/google/cadvisor:latest` container (`cadvisor`). Instead of
`privileged: true` it runs hardened: `cap_drop: [ALL]` plus `DAC_READ_SEARCH`,
`SYS_PTRACE` and `SYS_ADMIN`, a read-only root filesystem with a tmpfs `/tmp`,
and `/dev/kmsg`. It mounts `/`, `/var/run`, `/sys` and `/etc/machine-id`
read-only, plus the Docker data root at its own absolute path (`ro,rslave`).

| Item         | Value                                                   |
|--------------|---------------------------------------------------------|
| Metrics / UI | host `:8081` → container `:8080` (UFW: `192.168.0.0/16`) |
| Compose dir  | `/home/pi/docker/observability/cadvisor`              |
| Memory limit | `cadvisor_mem_limit` (384M)                             |
| PID limit    | `cadvisor_pids_limit` (200)                             |

Host port 8081 is used because AdGuard already holds 8080.

## Deploy

```bash
make cadvisor      # or `make observability` for the whole stack
```

The role reads `docker info` for `DockerRootDir` and asserts that it is an
absolute path other than `/` before mounting it. The containerd handler
resolves container paths there, so `/rootfs` alone is not enough.

## Configuration

- `cadvisor_disabled_metrics`: upstream defaults plus `disk`. Recursive filesystem size and inode walks are disabled. `diskIO` is kept, and host capacity comes from node-exporter.
- Flags: `--housekeeping_interval=30s`, `--docker_only=true`, `--store_container_labels=false`.
- `cadvisor_mem_limit`: 384M. The comment explains that the charged inode/dentry cache from recursive accounting exhausted even this ceiling.

## Operations

- Healthcheck: `wget --spider http://localhost:8080/healthz`. A passing
  healthcheck does not prove scrapes are fast. After upgrades, check
  `/metrics` latency and memory as well.
- vmagent job `cadvisor` scrapes `127.0.0.1:8081`. The `container_health_state`
  metric feeds `ContainerUnhealthy` / `ContainerUnhealthyCritical` in
  `roles/vmalert/templates/rules/host-saturation.yml.j2`.
- Incident: [scrapes stalled at the memory cap](../../../docs/incidents/2026-09-25-cadvisor-scrapes-stalled-at-memory-cap.md).
- Memory ceilings are containment caps, not reservations:
  [container-mem-limits](../../../docs/conventions/container-mem-limits.md).
