# Sprint 9: VictoriaLogs + VictoriaMetrics + Grafana Stack

> **Historical sprint record.** Statuses and checklists below describe the original
> work window, not today's backlog or deployment state. See
> [PROJECT_INDEX](../../../PROJECT_INDEX.md) for dated evidence.
>
> **Öncelik:** Yüksek
> **Hedef:** Container error/restart loop/healthcheck failure'ları artık fark edilebilsin. Beszel'in "tahmini" eşiklerini gerçek tarihsel veriyle kalibre etmek için altyapı kur.
> **PRD Referansları:** [PRD-008](../prds/008-log-aggregation.md)
> **Bağımlılık:** Mevcut Sprint 1 (homelab-control) + Beszel deploy'u + ntfy.
> **Durum (2026-04-28 verified live):** Phase 1 + Phase 2 deployed and running. 28 containers healthy on Pi; vmagent 7/7 targets up; VL+VM+Grafana+exporters+Fluent Bit all live. Phase 3 (alerting) and Phase 4 (Beszel benchmark gate) outstanding.

---

## Phase 1 — Core (VL, VM, exporters, Grafana) — DONE 2026-04-27

### 9.1.1 victorialogs role — DONE

- [x] `ansible/roles/victorialogs/` (full role skeleton with healthcheck, mem_limit 768M, retention 30d)
- [x] `ansible/playbooks/victorialogs.yml`
- [x] `Makefile` target: `victorialogs`
- [x] Smoke test: `/health` → 200, 119 MB data accumulated

### 9.1.2 victoriametrics role — DONE

- [x] `ansible/roles/victoriametrics/` (retention 90d, mem_limit 384M)
- [x] `ansible/playbooks/victoriametrics.yml`
- [x] `Makefile` target: `victoriametrics`
- [x] Smoke test: `/health` → 200, 157 MB data accumulated

### 9.1.3 node_exporter role — DONE

- [x] `ansible/roles/node_exporter/` (host network, full collector set, mem_limit 64M)
- [x] `ansible/playbooks/node-exporter.yml`
- [x] `Makefile` target: `node-exporter`

### 9.1.4 cadvisor role — DONE

- [x] `ansible/roles/cadvisor/` (privileged, host port 8081→container 8080)
- [x] `ansible/playbooks/cadvisor.yml`
- [x] `Makefile` target: `cadvisor`

### 9.1.5 vmagent role — DONE

- [x] `ansible/roles/vmagent/` (host network, scrape_timeout 20s, scrape_interval 30s)
- [x] `templates/scrape.yml.j2` — 7 scrape jobs (node-exporter, cadvisor, traefik, ntfy, VL, VM, vmagent-self) all `up=1`
- [x] `ansible/playbooks/vmagent.yml`
- [x] `Makefile` target: `vmagent`

### 9.1.6 grafana role — DONE

- [x] `ansible/roles/grafana/` (mem_limit 384M, admin user `pi`)
- [x] `templates/datasources.yml.j2` — VictoriaMetrics (prometheus type) + VictoriaLogs (**victoriametrics-logs-datasource** plugin, NOT loki — VL only implements Loki *push* API not query)
- [x] `templates/dashboards.yml.j2` provisioner
- [x] Dashboards downloaded via `get_url` from grafana.com (rev pinned):
  - victorialogs-single-node (id 22084 rev 11)
  - victoriametrics-single-node (id 10229 rev 51)
  - node-exporter-full (id 1860 rev 45)
  - docker-cadvisor (id 193 rev 1)
- [x] `GF_PLUGINS_PREINSTALL_SYNC=victoriametrics-logs-datasource` (replaces deprecated `GF_INSTALL_PLUGINS`)
- [x] `ansible/playbooks/grafana.yml` + `Makefile` target
- [x] Smoke test: `/api/health` → 200

### 9.1.7 Traefik routes & Homepage widgets — DONE

- [x] `ansible/roles/traefik/defaults/main.yml` → 4 routes added:

  ```yaml
  - { name: logs,    port: 9428 }
  - { name: metrics, port: 8428 }
  - { name: vmagent, port: 8429 }
  - { name: grafana, port: 3003 }
  ```

- [x] Homepage Infrastructure section: 4 widgets (Grafana, VictoriaMetrics, VictoriaLogs, vmagent)

### 9.1.8 Vault değişkenleri — DONE

- [x] `vault.yml.example` updated with `vault_grafana_admin_password`
- [x] Role default falls back to `admin` so first deploy works without vault edit; Grafana forces password change on first login

---

## Phase 2 — Logging (Fluent Bit) — DONE 2026-04-28

### 9.2.1 vlagent image availability check — DONE 2026-04-28

- [x] `docker manifest inspect victoriametrics/vlagent:v1.50.0` — arm64 var (5 archs)
- [x] **Decision gate hit, FluentBit'e pivot:** vlagent inputs only support syslog/journald/HTTP — no `tail` source for Docker container json logs. vlagent is a relay, not a collector. Fluent Bit is the planned fallback and the right tool for file-tailing.

### 9.2.2 fluentbit role — DONE

- [x] `ansible/roles/fluentbit/` (host network, mem_limit 128M)
- [x] `templates/fluent-bit.conf.j2` — tail input on `/srv/docker/containers/*/*-json.log` (Pi's docker root is `/srv/docker`, not `/var/lib/docker`), HTTP output to VL `/insert/jsonline?_stream_fields=host,container_id&_msg_field=log&_time_field=time`
- [x] `templates/enrich.lua.j2` — Lua filter extracts 12-char `container_id` from `log_path` (Path_Key) so VL partitions streams per container
- [x] `[FILTER] grep` — drops known-benign noise pattern (node-exporter "broken pipe/connection reset" — see big block comment for why)
- [x] `ansible/playbooks/fluentbit.yml` + `Makefile` target: `fluentbit`
- [x] Verified ingestion: ~1400 records / 2 min during smoke, per-container streams confirmed via `_stream={container_id=...,host=homelab}`

### 9.2.3 Verify ingestion — DONE

- [x] Real container log lines queryable via LogsQL (e.g. `container_id:bb042cab3671`)

### 9.2.4 7 gün soak

- [x] Stack live since 2026-04-28; 44+ hours uptime confirmed. Disk growth: VL ~119 MB → on track for ~15 GB / 30d budget.

---

## Phase 3 — Alerting (Alertmanager + vmalert)

### 9.3.0 alertmanager role (deploy first — vmalert needs it as notifier)

- [ ] `ansible/roles/alertmanager/`
- [ ] `defaults/main.yml`: port 9093, mem_limit 64M, ntfy mapping config
- [ ] `templates/alertmanager.yml.j2` — single receiver `ntfy-webhook`, route group_by `[alertname, severity]`, group_wait 30s, group_interval 5m, repeat_interval 4h
- [ ] **Webhook → ntfy adapter:** Alertmanager `webhook_config` POST'lar JSON gövdesinde fire/resolve event'i. ntfy'in HTTP API'si raw body kabul eder. İki seçenek: (a) Alertmanager'ın `template_files` ile body'yi düz text formatına çevirip direkt ntfy'a postlamak — Alertmanager v0.27+ destekliyor; (b) küçük Python relay script + sidecar. (a) tercih edilecek; tek `template_files` yeterli.
- [ ] `templates/notify.tmpl.j2` — title/body/priority/tags rendering for ntfy
- [ ] `ansible/playbooks/alertmanager.yml`
- [ ] `Makefile` target: `alertmanager:`
- [ ] Smoke test: `curl -X POST http://192.0.2.100:9093/api/v2/alerts -H 'Content-Type: application/json' -d '[{"labels":{"alertname":"SmokeTest","severity":"info"},"annotations":{"summary":"sprint 9 hello"}}]'` — ntfy'da görünmeli

### 9.3.1 vmalert role

- [ ] `ansible/roles/vmalert/`
- [ ] `templates/rules/container_errors.yml.j2` — LogsQL kuralları
- [ ] `templates/rules/container_health.yml.j2` — PromQL kuralları (cadvisor metrics üzerinden)
- [ ] `templates/rules/host_health.yml.j2` — Beszel mirror (CPU/Mem/Temp/Disk)
- [ ] vmalert flags: `-datasource.url=http://victoriametrics:8428`, `-remoteWrite.url=http://victoriametrics:8428/api/v1/write`, `-rule=/etc/vmalert/rules.d/*.yml`, `-notifier.url=http://alertmanager:9093` (Alertmanager v2 API)
- [ ] Memory limit: 128 MB
- [ ] `ansible/playbooks/vmalert.yml`
- [ ] `Makefile` target: `vmalert:`

### 9.3.2 İlk kural seti (PRD-008 §10)

- [ ] Container error spike (LogsQL count > 10/5m)
- [ ] Container restart loop (PromQL increase > 3/10m)
- [ ] Watchtower failure (LogsQL match)
- [ ] Traefik backend down (PromQL `traefik_service_server_up == 0`)
- [ ] Disk > 90%, Temp > 75°C, CPU > 85% sustained 5m, Mem > 80%

### 9.3.3 Test fire

- [ ] Manuel olarak bir kuralın koşulunu sağlayan veri inject et (örn `docker run --rm busybox sh -c "echo ERROR test ; sleep 1"` 12 kere)
- [ ] vmalert dashboard'unda alert "firing" durumuna geçti mi
- [ ] ntfy'a notification düştü mü

---

## Phase 4 — Beszel decision gate (+30 gün)

### 9.4.1 Benchmark dökümü

- [ ] 30 gün boyunca her iki sistemden alarm sayıları + false positive sayıları kaydet
- [ ] Grafana panel'de yan yana karşılaştır
- [ ] Bir kararsızlık alarmı (örn metric 75-85 arası dalgalanma) ile her iki sistemin spam davranışı

### 9.4.2 Decision

- [ ] Beszel kalsın → vmalert kuralları kapatılır, sadece logging kalsın
- [ ] Beszel emekli → `make beszel` playbook'u no-op'a alınır, container stop edilir, role korunur

---

## Yeni Port Allocation

| Port | Servis | Phase |
|---|---|---|
| 9428 | VictoriaLogs | 1 |
| 8428 | VictoriaMetrics | 1 |
| 8429 | vmagent | 1 |
| 9100 | node_exporter | 1 |
| 8081 | cadvisor | 1 |
| 3003 | Grafana | 1 |
| 8880 | vmalert | 3 |
| – | vlagent (no host port) | 2 |

---

## Makefile Eklentileri

```makefile

victorialogs:
  cd ansible && ansible-playbook playbooks/victorialogs.yml

victoriametrics:
  cd ansible && ansible-playbook playbooks/victoriametrics.yml

node-exporter:
  cd ansible && ansible-playbook playbooks/node-exporter.yml

cadvisor:
  cd ansible && ansible-playbook playbooks/cadvisor.yml

vmagent:
  cd ansible && ansible-playbook playbooks/vmagent.yml

grafana:
  cd ansible && ansible-playbook playbooks/grafana.yml

vlagent:
  cd ansible && ansible-playbook playbooks/vlagent.yml

vmalert:
  cd ansible && ansible-playbook playbooks/vmalert.yml

# Convenience: deploy whole observability stack
observability: victorialogs victoriametrics node-exporter cadvisor vmagent grafana vlagent vmalert

```

---

## Deploy Sırası (single-pass)

```bash

# Phase 1
make victorialogs          # 1. log DB
make victoriametrics       # 2. metric TSDB
make node-exporter         # 3. host metrics
make cadvisor              # 4. container metrics
make vmagent               # 5. scraper
make grafana               # 6. UI (datasources + dashboards provisioned)
make traefik               # 7. routes
make homepage              # 8. widgets

# Phase 2 (after image pull check)
make vlagent

# Phase 3 (after 7-day soak)
make vmalert

```

---

## Verification Checklist (PRD-008 §19)

- [ ] Grafana'da `Node Exporter Full` dashboard'da 24-saat CPU/RAM/disk görünüyor
- [ ] Grafana'da `cAdvisor` dashboard'da tüm container'lar listelenmiş
- [ ] LogsQL `_msg:~"ERROR" | count()` son 7 gün sonuç dönüyor
- [ ] vmalert UI'da en az 1 firing rule var (test)
- [ ] Pi reboot sonrası `docker ps` yeni container'ları "Up" gösteriyor
- [ ] `make lint-ports` 0 duplicates
- [ ] `make validate` yeşil
- [ ] Beszel hâlâ paralel çalışıyor, ntfy'dan gelen mesajların kaynağı (Beszel mi, vmalert mi) ayırt edilebiliyor (title prefix)
