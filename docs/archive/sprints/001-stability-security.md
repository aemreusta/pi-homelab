# Sprint 1: Stability & Security

> **Historical sprint record.** Statuses and checklists below describe the original
> work window, not today's backlog or deployment state. See
> [PROJECT_INDEX](../../../PROJECT_INDEX.md) for dated evidence.
>
> **Öncelik:** Kritik + Yüksek
> **Hedef:** Sistemi 30 gün dokunmadan çalışır hale getirmek. Security gap'leri kapatmak.
> **PRD Referansları:** [PRD-002](../prds/002-control-api-enhancements.md), [PRD-003](../prds/008-log-aggregation.md)

---

## Sistem Durumu (2026-03-16 SSH)

| Metrik | Değer |
|--------|-------|
| Uptime | 7 gün, 18 saat |
| Container | 20 çalışıyor, 0 restart |
| Disk | 1.1 TB / 1.8 TB (65%) |
| RAM | 1.5 GB / 7.6 GB (6.2 GB available) |
| CPU Temp | 35.5°C |
| Failed systemd | 0 |
| API Health | Beszel ✅, ntfy ✅, Control ✅ |

---

## Tasks

### 1.1 Homepage Performance Baseline

**PRD ref:** PRD-003 Feature 3

Optimizasyon yapmadan önce mevcut durumu ölç.

- [ ] `bash scripts/perf-baseline.sh` çalıştır (Mac'ten, LAN üzerinden)
- [ ] Sonuçları kaydet: DNS latency, Traefik TTFB, direct TTFB
- [ ] Decision gate değerlendir:
  - `direct > 2s` → homepage widget timeout → log incele
  - `traefik - direct > 500ms` → Traefik middleware → config kontrol
  - `DNS > 100ms` → AdGuard latency → config kontrol
- [ ] Sonuçları `docs/` altına not olarak ekle

**Dosyalar:** `scripts/perf-baseline.sh` (mevcut, değişiklik yok)

---

### 1.2-1.4 Junk Cleaner Hardening — ✅ Tamamlandı (2026-03-24)

**PRD ref:** PRD-002 Feature 1.1, 1.2, 1.3

- [x] Symlink saldırı koruması: `os.path.realpath()` ile `/srv/` dışına çıkan path'ler reddedilir
- [x] Audit log: `RotatingFileHandler` ile `/var/log/homelab-clean.log` (10MB, 4 backup)
- [x] Logrotate config: `logrotate-homelab.j2` → `/etc/logrotate.d/homelab-clean`
- [x] Log dosya izinleri: Ansible ile `pi:pi 0640` ön-oluşturma (servis root değil pi olarak çalışıyor)
- [x] `homelab-junk.log` cron log dosyası da ön-oluşturma + logrotate'e eklendi (pre-existing fix)
- [x] Test: `TestSymlinkProtection` class — symlink bypass ve legitimate delete testleri
- [x] Mevcut 37 test regression geçti

---

### 1.5 Storage Observability — GET /storage — ✅ Tamamlandı (2026-03-24)

**PRD ref:** PRD-002 Feature 2

- [x] `get_storage_stats()` fonksiyonu: `shutil.disk_usage` + recursive `os.walk` per directory
- [x] `GET /storage` route (Bearer auth required)
- [x] Web panel: progress bar (blue/yellow/red color-coded) + directory size table
- [x] Test: auth enforcement + response structure validation

---

### 1.6 Last Scan Result — GET /junk/last

**PRD ref:** PRD-002 Feature 3.1

Son cron scan sonucunu API'den erişilebilir yap.

- [ ] `junk-scan.sh.j2` → script sonuna JSON output ekle:

  ```bash
  echo '{"scanned_at":"'$(date -Iseconds)'","total_count":'$COUNT',"total_size":'$SIZE'}' \
    > /opt/homelab/control/last-junk-scan.json
  ```

- [ ] `control-api.py.j2` → `do_GET` handler'a `/junk/last` route ekle:
  - Dosya varsa JSON oku ve serve et
  - Dosya yoksa `{"error": "no scan results yet"}` döndür
- [ ] Web panelde: "Last scan: Sunday 03:00 — 47 files found" satırı

**Dosyalar:**

- `ansible/roles/homelab_control/templates/junk-scan.sh.j2`
- `ansible/roles/homelab_control/templates/control-api.py.j2`

---

### 1.7 Watchtower Updates Endpoint — `GET /updates`

**PRD ref:** PRD-002 Feature 5 — ✅ Tamamlandı

- [x] `control-api.py.j2` → `parse_watchtower_updates()` fonksiyonu eklendi
- [x] `GET /updates` route → Bearer auth, parsed session JSON
- [x] Web panel: Watchtower Updates tablosu (auto-fetch on load)
- [x] `watchtower` + `ntfy` DOCKER_CONTAINER_WHITELIST'e eklendi
- [x] 3 yeni test case (auth, parsed sessions, empty log)

---

## Çıktılar

- ✅ Hardened junk cleaner (symlink-safe + audit logged + logrotated)
- ✅ Storage visibility via API + web panel
- ✅ Son cron scan sonucu API'den erişilebilir
- ✅ Performance baseline data
- ✅ Watchtower update history endpoint + UI
- ✅ 7+ yeni test case

## Etkilenen Dosyalar (özet)

| Dosya | Değişiklik Tipi |
|-------|----------------|
| `ansible/roles/homelab_control/templates/control-api.py.j2` | Güncelleme (realpath, audit log, storage, junk/last) |
| `ansible/roles/homelab_control/templates/junk-scan.sh.j2` | Güncelleme (JSON output) |
| `ansible/roles/homelab_control/templates/logrotate-homelab.j2` | **Yeni dosya** |
| `ansible/roles/homelab_control/tasks/deploy.yml` | Güncelleme (logrotate task) |
| `tests/test_control_api.py` | Güncelleme (yeni test case'ler) |

## Deploy

```bash
make homelab-control    # Pi'ye deploy
python3 -m pytest tests/test_control_api.py -v  # Testleri çalıştır
```
