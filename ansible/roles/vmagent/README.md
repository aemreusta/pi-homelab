# vmagent

Scrapes every Prometheus `/metrics` endpoint in the homelab and remote-writes
the samples to VictoriaMetrics.

## What it deploys

One `victoriametrics/vmagent:latest` container (`vmagent`) in
`network_mode: host`. The DOCKER-USER chain blocks bridge→host traffic, so host
networking lets vmagent reach every target on `127.0.0.1`. It runs hardened:
read-only root filesystem, `/tmp` on tmpfs, `cap_drop: [ALL]` and
`no-new-privileges`. The scrape config is rendered from `templates/scrape.yml.j2`.

| Item         | Value                                                      |
|--------------|------------------------------------------------------------|
| Web UI       | host `:8429` (`vmagent_port`), LAN-only in UFW             |
| Traefik      | `https://vmagent.example.internal`                         |
| Remote write | `http://127.0.0.1:8428/api/v1/write`                       |
| Scrape config | `/home/pi/docker/observability/vmagent/config/scrape.yml` |
| Write buffer | `/home/pi/docker/observability/vmagent/data`             |
| Memory limit | `256M` (`vmagent_mem_limit`)                               |

## Deploy

```bash
make vmagent
```

Deploy it after victoriametrics, victorialogs and grafana. Most targets are
other roles' services, so a job reports `up == 0` until that service exists.

## Configuration

- `vmagent_scrape_jobs` — one entry per job: `name`, `target`, optional
  `metrics_path`, `enabled`, `basic_auth_*` and `drop_labels`.
- `vmagent_scrape_interval` / `vmagent_scrape_timeout` — both `30s`. Never set
  the timeout above the interval.
- `vmagent_speedtest_metrics_enabled` — turns the Speedtest Tracker job on or off.
- The `uptime-kuma` job stays disabled until `vault_uptime_kuma_metrics_key` is
  set. Without it the job would get 401s and raise a false `UptimeKumaDown`.
  The `zot` job uses `vault_zot_password`.

## Operations

- **Healthcheck:** `wget --spider http://127.0.0.1:8429/health`.
- **Alerts:** `VMAgentScrapeDown` (crit, `up{job="vmagent-self"} == 0`)
  means every scrape-based alert is blind. `VMAgentRemoteWriteFailing` means
  scraping works but samples are not reaching VictoriaMetrics.
- `--promscrape.disableCompression=true` stops noisy "broken pipe" errors
  during scrapes. The 30s timeout did not fix those errors. The real cause was
  node-exporter's own healthcheck (fixed 2026-07-31).
