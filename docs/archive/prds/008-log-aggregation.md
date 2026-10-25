# PRD-008: Log Aggregation & Time-Series Stack (Victoria + Grafana)

> **Öncelik:** Yüksek
> **İlgili Sprint:** [Sprint 9](../sprints/009-victoria-stack.md)
> **Bağımlılık:** Sprint 1 (homelab-control), Beszel deployment (coexist edecek)

---

## 1. Mevcut Durum

| Boyut | Şu an | Eksik |
|---|---|---|
| Container logs | Docker `json-file` driver, 10 MB × 3 file rotation per container | Aggregation yok, retention ~30 MB / container, eski log saatler içinde rotate olup gidiyor |
| Host logs | journald | Hiçbir şey ntfy'e bağlı değil |
| Metrik | Beszel (PocketBase backend, 24-saatlik UI grafiği) | Geçmiş veri queryable değil → eşikler "tahmini" |
| Alarm | Beszel metric thresholds + 4 cron script (disk/memory/docker-prune/daily-report) | Container içi hatalar (`ERROR`, `panic`, `unhealthy`) hiçbir alarm tetiklemiyor |
| Watchtower update history | `/updates` endpoint (manuel sorgu) + ntfy on-update (Sprint 8 sonrası) | Tarihsel arama yok |

**Joined gap:** Bir container restart loop'una girerse, traefik cert renewal başarısız olursa, Sonarr/Radarr import zinciri kırılırsa — log'lar yerel rotate edilip kayboluyor, ntfy hiçbir şey göndermiyor, kullanıcı sadece "broken behavior" görünce farkına varıyor.

---

## 2. Hedef

1. **30 günlük log retention** — tüm container'lar + host journald, etiketlerle filtrelenebilir.
2. **30+ günlük metrik retention** — Beszel'in 24h camera roll'u yerine gerçek tarihsel veri.
3. **Otomatik error alerting** — `ERROR|FATAL|panic|unhealthy` pattern'leri LogsQL kuralı tetikleyince ntfy'e push.
4. **Tek ekran correlation** — Grafana'da log + metrik aynı dashboard'da: bir spike olduğunda sebebi anında görülebilsin.
5. **Beszel'i emekliliğe doğru benchmark et** — vmalert'i aynı kurallarla kur, 30 gün kıyasla, sonra karar ver.

**Out of scope (bu PRD'de değil):**

- Trace toplama (Jaeger / Tempo / VictoriaTraces) — gelecek sprint
- SSO / OIDC — Grafana admin user + Traefik basicauth + UFW LAN-only ile yetinilecek (sonraki sprint'te değerlendir)
- Multi-tenancy / mTLS

---

## 3. Mimari

```text

┌──────────────────────────────── Pi 4B ────────────────────────────────┐
│                                                                        │
│   ┌─ Hosts & containers ─┐    ┌── Collectors ──┐    ┌─ Storage ──┐     │
│   │ /var/log/journal     │ ─► │   vlagent      │ ─► │ VictoriaLogs│    │
│   │ /var/lib/docker/.../ │    │  (file source) │    │   :9428    │     │
│   │ container json logs  │    └────────────────┘    └────┬───────┘     │
│   │                      │                               │             │
│   │ node_exporter :9100  │ ◄┐                            │             │
│   │ cadvisor :8080       │  ├─ vmagent ─┐                │             │
│   │ traefik /metrics     │ ◄┘ (scrape)  └─► VictoriaMetrics :8428      │
│   │ ntfy /metrics        │                       │                     │
│   └──────────────────────┘                       │                     │
│                                                  ▼                     │
│                                         ┌── Alerting ──┐               │
│                                         │   vmalert    │ ─► ntfy       │
│                                         │ (PromQL +    │   (existing   │
│                                         │  LogsQL)     │   topic)      │
│                                         └──────────────┘               │
│                                                                        │
│                            ┌─ Visualization ─┐                         │
│                            │     Grafana     │  ◄── kullanıcı          │
│                            │     :3003       │      (Traefik:          │
│                            └─────────────────┘       grafana.example)  │
│                                                                        │
│   ┌─ Coexisting ─┐                                                     │
│   │   Beszel     │  (30-gün benchmark window, sonra karar)             │
│   └──────────────┘                                                     │
└────────────────────────────────────────────────────────────────────────┘

```

---

## 4. Component Inventory

| Bileşen | Image | Port (host) | RAM tahmin | Disk tahmin | Rol |
|---|---|---|---|---|---|
| VictoriaLogs | `victoriametrics/victoria-logs:v1.x` | 9428 | ~400 MB | ~15 GB / 30d | Log DB |
| VictoriaMetrics | `victoriametrics/victoria-metrics:v1.x` | 8428 | ~200 MB | ~5 GB / 30d | TSDB |
| Fluent Bit | `fluent/fluent-bit:latest` | – (host net, no published port) | ~50 MB | <100 MB tail.db + buffer | Log collector (file→VL) |
| vmagent | `victoriametrics/vmagent:v1.x` | 8429 (web UI) | ~100 MB | <500 MB buffer | Metric scraper |
| node_exporter | `prom/node-exporter:v1.x` | 9100 | ~30 MB | – | Host metrik exporter |
| cadvisor | `gcr.io/cadvisor/cadvisor:v0.x` | 8080 (Pi'de çakışıyor — 8081'e map) | ~100 MB | – | Container metrik exporter |
| vmalert | `victoriametrics/vmalert:v1.x` | 8880 | ~80 MB | – | Alert rule evaluator |
| Alertmanager | `prom/alertmanager:v0.x` | 9093 | ~50 MB | <50 MB | Alert dedupe/grouping → ntfy webhook |
| Grafana | `grafana/grafana-oss:11.x` | 3003 | ~256 MB | ~500 MB | Dashboards & explore UI |

**Toplam ek RAM:** ~1.3 GB. Pi 8 GB / şu an ~1.5 GB kullanımda → bol headroom.

---

## 5. Feature 1: VictoriaLogs

**Görev:** LogsQL-queryable log database, container + host journald log akışını saklar.

| Detay | Değer |
|---|---|
| Image | `victoriametrics/victoria-logs:latest` (multi-arch) |
| Storage | `/home/pi/docker/observability/victorialogs/data` (ext4 SSD) |
| Retention | `--retentionPeriod=30d` |
| Port | 9428 (HTTP API + minimal UI at `/select/vmui`) |
| Traefik route | `logs.example` |
| Memory limit | 768 MB (2× tahmin headroom) |
| Healthcheck | `GET /health` |
| Stream fields | `host`, `container_name`, `compose_project`, `app_name` (vlagent enrich edecek) |

---

## 6. Feature 2: VictoriaMetrics

**Görev:** Prometheus-compatible TSDB, metric retention.

| Detay | Değer |
|---|---|
| Image | `victoriametrics/victoria-metrics:latest` |
| Storage | `/home/pi/docker/observability/victoriametrics/data` |
| Retention | `--retentionPeriod=90d` (metrikler küçük, daha uzun tutulabilir) |
| Port | 8428 |
| Traefik route | `metrics.example` |
| Memory limit | 384 MB |
| Healthcheck | `GET /health` |

---

## 7. Feature 3: Fluent Bit (log collector)

**Görev:** Container json logs + journald → enrich → VictoriaLogs.

**Karar — neden vlagent değil Fluent Bit:** vlagent v1.50.0 ARM64 image'i mevcut, ancak input desteği SADECE syslog/journald/HTTP receiver'lardan oluşuyor — Docker'ın `/var/lib/docker/containers/*/*-json.log` dosyalarını tail edecek bir `file` input'u yok. vlagent bir log RELAY'idir, COLLECTOR değil. Fluent Bit zaten hafif (~50 MB), olgun, multi-arch, ve VL'in `/insert/jsonline` endpoint'ine native HTTP output'la basıyor.

**vlagent'ı ileride tekrar değerlendirme noktası:** Eğer VictoriaMetrics ekibi vlagent'a `file` source eklerse, Phase 2.5'te swap edilir.

| Detay | Değer |
|---|---|
| Image | `fluent/fluent-bit:latest` (multi-arch) |
| Sources | `/var/lib/docker/containers/*/*-json.log` (Docker daemon writes) — journald şu an opsiyonel, ek input olarak Phase 2.5'te eklenir |
| Target | `http://127.0.0.1:9428/insert/jsonline?_stream_fields=host,container_id&_msg_field=log&_time_field=time` |
| Enrichment | `host=homelab`, `container_id` (filename'den parse) |
| Buffer | Disk-based (`/data/fluentbit-buffer`), 64 MB mem buf + tail.db |
| Memory limit | 128 MB |
| Volumes | `/var/lib/docker/containers:ro`, `/data/fluentbit-buffer` |
| Network | `host` (Pi'in DOCKER-USER chain'i bridge→host trafiğini engelliyor; vmagent ile aynı çözüm) |

---

## 8. Feature 4: vmagent (metric scraper)

**Görev:** Tüm `/metrics` endpoint'lerini scrape edip VictoriaMetrics'e remote_write.

| Detay | Değer |
|---|---|
| Image | `victoriametrics/vmagent:latest` |
| Scrape targets | `node_exporter:9100`, `cadvisor:8080`, `traefik:8180/metrics`, `ntfy:2586/metrics`, `victorialogs:9428/metrics`, `victoriametrics:8428/metrics`, `vmagent:8429/metrics` (self-scrape) |
| Scrape interval | 30s |
| Buffer | Disk-based remote_write queue, 1 GB cap |
| Memory limit | 256 MB |
| Volumes | `/data/vmagent-buffer` |

---

## 9. Feature 5: Exporters (node_exporter + cadvisor)

| Exporter | Image | Port | Görev |
|---|---|---|---|
| node_exporter | `prom/node-exporter:latest` | 9100 | Host CPU, RAM, disk, network, temperature, systemd unit health |
| cadvisor | `gcr.io/cadvisor/cadvisor:latest` | 8081 (Pi'de 8080 cAdvisor için boş ama Tdarr 8080 kullanıyor — port çakışmasını ufw'de doğrula) | Container CPU, RAM, IO, network |

**Note:** cAdvisor varsayılan portu 8080 — Tdarr UI port 8265 kullanıyor, ama 8080'i bilmiyorum. Sprint kapsamında lint port-uniqueness kontrolüyle netleşecek; çakışma varsa 8081'e map'lenir.

---

## 10. Feature 6: vmalert + Alertmanager (alarm motoru)

**Görev:** PromQL (metrikler) + LogsQL (loglar) kuralları evaluate eden vmalert, fire eden alarmları Alertmanager'a forward eder. Alertmanager dedupe + grouping + cooldown yapıp ntfy webhook'una bas.

**Neden Alertmanager:** vmalert native olarak Alertmanager protokolü konuşur — custom adapter yazmadan, dedupe/inhibit/silencing/grouping native gelir. Pi 4B üstünde ek 50 MB RAM bütçesi — kabul edilebilir.

**Alarm payload yolu:** vmalert → POST `/api/v2/alerts` → Alertmanager → webhook receiver → ntfy URL (mevcut topic).

### vmalert

| Detay | Değer |
|---|---|
| Image | `victoriametrics/vmalert:latest` |
| Datasource | `http://victoriametrics:8428` |
| Remote write | `http://victoriametrics:8428/api/v1/write` (alarm event'lerini metrik olarak kaydet) |
| Notifier | `http://alertmanager:9093` (Alertmanager v2 API) |
| Rule files | `/etc/vmalert/rules.d/*.yml` (Ansible ile template'lenecek) |
| Port | 8880 |
| Memory limit | 128 MB |

### Alertmanager

| Detay | Değer |
|---|---|
| Image | `prom/alertmanager:latest` |
| Storage | `/home/pi/docker/observability/alertmanager/data` |
| Port | 9093 |
| Memory limit | 64 MB |
| Receivers | Tek receiver: `ntfy-webhook` (POST → ntfy `http://ntfy:2586/homelab`) |
| Routing | Tüm alarmlar default route → ntfy |
| Group by | `[alertname, severity]` |
| Group wait | 30s (ilk alarm için bekleme) |
| Group interval | 5m (aynı grup için yeni alarm bekleme) |
| Repeat interval | 4h (devam eden alarm için tekrar bildirim) |
| ntfy mapping | `severity=critical` → priority `urgent`, `warning` → `high`, `info` → `default`. Title = `{{ .CommonAnnotations.summary }}`, Tags = `{{ .CommonLabels.alertname \| lower }}` |

**İlk iteration alarm kuralları:**

| Kural | LogsQL / PromQL | Tetikleme |
|---|---|---|
| Container error spike | `_stream_fields:container_name _msg:~"ERROR\|FATAL\|panic\|exception"` count > 10 / 5m | high priority ntfy |
| Container unhealthy | `cadvisor: container_last_seen{} == 0` for 2m | high priority ntfy |
| Container restart loop | `increase(container_start_time_seconds[10m]) > 3` | urgent ntfy |
| Watchtower update failure | `_stream_fields:container_name="watchtower" _msg:~"Error\|failed"` | high priority ntfy |
| Traefik backend down | `traefik_service_server_up == 0` for 2m | high priority ntfy |
| Beszel mirror — disk > 90% | `node_filesystem_avail_bytes / node_filesystem_size_bytes < 0.1` | urgent ntfy |
| Beszel mirror — temp > 75°C | `node_hwmon_temp_celsius > 75` | high priority ntfy |

---

## 11. Feature 7: Grafana (dashboard, day 1)

**Neden day 1:** VictoriaLogs henüz olgun bir built-in UI sağlamıyor. Curl + LogsQL ile arama yapmak MTTR'yi uzatır. Logs ↔ metrics correlation için Grafana zorunlu.

| Detay | Değer |
|---|---|
| Image | `grafana/grafana-oss:latest` |
| Port | 3003 (3000 AdGuard, 3001 Homepage, 3002 Uptime Kuma planlı; 3003 boş) |
| Storage | `/home/pi/docker/observability/grafana/data` |
| Traefik route | `grafana.example` |
| Auth | Admin user pi, password vault'tan |
| Memory limit | 384 MB |
| Provisioning | Datasources + dashboards Ansible ile YAML template |

**Provisioned datasources:**

- VictoriaMetrics (Prometheus-compatible)
- VictoriaLogs (Loki-compatible plugin)

**Provisioned dashboards (resmi VM dashboards):**

| Dashboard | ID | Amaç |
|---|---|---|
| VictoriaLogs - Single Node | 22084 | İngest hızı, disk, query perf |
| VictoriaMetrics - Single Node | 10229 | TSDB sağlık |
| Node Exporter Full | 1860 | Host hardware |
| Docker Container Monitoring (cAdvisor) | 193 | Container kaynak kullanımı |

---

## 12. Beszel Coexistence Strategy

30-gün benchmark window:

1. Beszel **çalışmaya devam eder** — `make beszel` deploy'u değişmez
2. VictoriaMetrics paralel olarak aynı host metriklerini toplar
3. vmalert'te **Beszel ile birebir aynı thresholds** ile aynalanmış alarm kuralları kurulur (CPU >85%, Mem >80%, Temp >70°C, Disk handled by cron)
4. 30 gün boyunca her iki sistemin alarm doğruluğu/spam oranı kıyaslanır
5. Karar gate: VictoriaMetrics tarafı eşit ya da daha iyi → Beszel emekli edilir, role tutulur (geri dönüş için)

**Çıkarım — neden hemen kaldırmıyoruz:** Beszel'in mobile UI'si pratik, ntfy webhook'u zaten çalışıyor, tahta deploy. Önce yenisinin güvenilirliğini görelim.

---

## 13. Resource Budget (Pi 4B 8GB / 1.8TB SSD)

| Bileşen | RAM | Disk |
|---|---|---|
| VictoriaLogs | 768 MB | 15 GB / 30d |
| VictoriaMetrics | 384 MB | 5 GB / 90d |
| vlagent | 128 MB | 500 MB buffer |
| vmagent | 256 MB | 1 GB buffer |
| node_exporter | 64 MB | – |
| cadvisor | 128 MB | – |
| vmalert | 128 MB | – |
| Grafana | 384 MB | 500 MB |
| **Toplam ek** | **~2.2 GB peak** | **~22 GB initial** |

**Pi durumu:** 8 GB RAM, şu an ~1.5 GB kullanımda. Yeni stack tam yüklü 3.7 GB → 4.3 GB free. Disk 1.8 TB / şu an 65% kullanımda → 600 GB free. Rahatlıkla sığar.

---

## 14. Yeni Port Allocation

| Port | Servis | Çakışma kontrol |
|---|---|---|
| 9428 | VictoriaLogs | Boş ✅ |
| 8428 | VictoriaMetrics | Boş ✅ |
| 9429 | vlagent web UI | Boş ✅ |
| 8429 | vmagent web UI | Boş ✅ |
| 9100 | node_exporter | Boş ✅ |
| 8081 | cadvisor (8080 → 8081 host map) | Tdarr 8265 farklı, çakışma yok ✅ |
| 8880 | vmalert | Boş ✅ |
| 3003 | Grafana | 3000 AdGuard, 3001 Homepage, 3002 Uptime Kuma planlı → 3003 boş ✅ |

`make lint-ports` final sprint sonunda doğrulayacak.

---

## 15. Yeni Traefik Routes

```yaml

- name: logs       # VictoriaLogs vmui

  port: 9428

- name: metrics    # VictoriaMetrics vmui

  port: 8428

- name: vmagent

  port: 8429

- name: alerts     # vmalert UI

  port: 8880

- name: grafana

  port: 3003

```

`cadvisor` ve `node_exporter` Traefik'e expose edilmez — sadece vmagent scrape'ler.

---

## 16. Phased Rollout Plan

**Phase 1 (Sprint 9.1 — VL/VM core):**

- victorialogs + victoriametrics + vmagent + node_exporter + cadvisor + grafana role'leri
- Grafana'da official dashboard'lar provision
- Traefik route'ları + homepage widget'ları
- **Goal:** "Görüyorum ama henüz alarm yok" — gözlem + tarihsel veri başlasın

**Phase 2 (Sprint 9.2 — Logging):**

- vlagent role
- Container + journald log akışı VL'ye gidiyor
- **Goal:** Grafana'da log search çalışıyor, 7-gün veri birikti

**Phase 3 (Sprint 9.3 — Alerting):**

- vmalert role
- LogsQL + PromQL kural setleri ntfy'e bağlı
- Beszel ile alarm-paralel benchmark başlar
- **Goal:** Container error'ları artık ntfy'e düşüyor

**Phase 4 (Sprint 9.4 — Beszel decision, +30 gün sonra):**

- Benchmark sonuçlarını değerlendir
- Karar: Beszel kalsın / kalksın

---

## 17. Out of Scope

- Trace ingestion (VictoriaTraces / Jaeger) → ayrı PRD
- Authentication / OIDC for Grafana → Traefik basicauth + LAN-only yeterli
- Alertmanager → vmalert direkt ntfy'e basacak
- Loki backwards compat → user'lar mevcut bir Loki dashboard'u import etmek istiyorsa, VL'nin Loki-compatible push API'si zaten var (vlagent'e gerek yok bu use case için)

---

## 18. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| vlagent ARM64 image instability (yeni ürün) | Phase 2 başında image pull testi yap; sorun varsa `fluentbit` rolüne pivot |
| VictoriaLogs disk büyümesi tahminden büyük | `--retentionPeriod` düşürülebilir; vmalert kuralı disk > %85 → urgent ntfy |
| Grafana auth bypass riski | Traefik basicauth + UFW LAN-only |
| Beszel + vmalert çift bildirim spam'i | Phase 3'te Beszel'in ntfy webhook'u **geçici olarak kapatılabilir**, sonuçlar log'a kaydedilir, 30-gün sonunda decision gate |
| Pi reboot sonrası container start order | Compose `depends_on` + healthcheck; tüm role'ler `restart: unless-stopped` |

---

## 19. Success Criteria

- [ ] Grafana'da bir container'ın 24-saatlik CPU/RAM/disk grafiği görüntülenebiliyor
- [ ] LogsQL ile `_msg:~"ERROR"` aramada son 7 gün sonuçları geliyor
- [ ] vmalert bir test kuralında tetiklenip ntfy'a notification gönderdi
- [ ] Pi reboot sonrası tüm 8 yeni container otomatik kalkıyor
- [ ] Beszel + Victoria 7 günlük benchmark window'unda her iki tarafta da aynı CPU usage'ı içe yakın değerlerde gösteriyor
- [ ] Toplam container RAM kullanımı 4 GB altında
