# PRD-002: Control API İyileştirmeleri

> **Öncelik:** Yüksek (güvenlik) / Orta (storage) / Düşük-Orta (cron)
> **İlgili Sprint:** [Sprint 1 — Stability & Security](../sprints/001-stability-security.md)
> **Mevcut Kod:** `ansible/roles/homelab_control/templates/control-api.py.j2`
> **Test:** `tests/test_control_api.py`

---

## Mevcut Durum

Homelab Control API (port 9099) systemd servisi olarak çalışıyor. Python `http.server` tabanlı, Bearer token auth.

### Mevcut Endpoints

| Method | Path | Auth | Açıklama |
|--------|------|------|----------|
| GET | `/health` | Hayır | Health check |
| GET | `/?token=<t>` | Query param | Web panel HTML |
| GET | `/junk` | Bearer | Junk dosya listesi |
| POST | `/junk` | Bearer | Junk dosya silme |
| GET | `/logs?source=system\|container&lines=N` | Bearer | Log viewer |
| POST | `/shutdown` | Bearer | Sistem kapatma |
| POST | `/restart` | Bearer | Sistem yeniden başlatma |
| POST | `/update` | Bearer | Git pull + Ansible |
| POST | `/clean` | Bearer | Docker prune |

### Konfigürasyon

```yaml
# ansible/roles/homelab_control/defaults/main.yml
homelab_control_port: 9099
homelab_control_junk_scan_dirs:
  - /srv/downloads
  - /srv/media
```

---

## Feature 1: Junk Cleaner Güvenlik Hardening

**Öncelik: Yüksek**

### 1.1 Symlink Saldırı Koruması

**Problem:** `_junk_clean()` metodu dosya silerken path doğrulaması yapmıyor. Bir symlink (`._test -> /etc/passwd`) ile `/srv` dışındaki dosyalar silinebilir.

**Çözüm:** Delete öncesi `os.path.realpath()` kontrolü:

```python
# control-api.py.j2 → _junk_clean()
real = os.path.realpath(f["path"])
if not real.startswith("/srv/"):
    errors.append({"path": f["path"], "error": "path escapes /srv, skipped"})
    continue
```

**Koşullar:**

- Path resolution sonrası `/srv/` prefix'i olmalı
- Escape eden path'ler `errors` listesine eklenmeli, silinmemeli
- Log'a yazılmalı (audit log ile birlikte)

### 1.2 Silme Audit Log

**Problem:** Hangi dosyaların ne zaman silindiği kaydedilmiyor.

**Çözüm:** `POST /junk` sonrası `/var/log/homelab-clean.log` dosyasına yaz:

```text
2026-03-16 14:32 [JUNK DELETE] deleted=26 freed=110592 errors=0
  /srv/downloads/._foo.mp4 (4096 bytes)
  /srv/downloads/._bar.mkv (4096 bytes)
  ...
```

**Implementasyon:**

- `_junk_clean()` içinde başarılı silmeler `logging` modülü ile loglanacak
- `FileHandler` ile `/var/log/homelab-clean.log`'a yazılacak
- Cron scan sonuçları da aynı log dosyasına eklenecek

### 1.3 Logrotate

Yeni template: `ansible/roles/homelab_control/templates/logrotate-homelab.j2`

```text
/var/log/homelab-clean.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
}
```

Deploy: `tasks/deploy.yml` → `/etc/logrotate.d/homelab-clean` olarak kopyala

### 1.4 Race Condition Fix (Minor)

**Problem:** `freed_bytes` hesabı scan anındaki dosya boyutunu kullanıyor. Scan ile delete arasında dosya değişmiş/silinmiş olabilir.

**Çözüm:** Delete anında `os.path.getsize()` ile kontrol et, `OSError` durumunda counter artırma.

---

## Feature 2: Storage Observability — `GET /storage`

**Öncelik: Orta**

### API Endpoint

```http
GET /storage
Authorization: Bearer <token>
```

**Response:**

```json
{
  "total_bytes": 1800000000000,
  "used_bytes": 1100000000000,
  "free_bytes": 615000000000,
  "use_percent": 65,
  "dirs": [
    {"path": "/srv/docker", "bytes": 50000000, "human": "48 KB"},
    {"path": "/srv/downloads", "bytes": 750000, "human": "748 KB"},
    {"path": "/srv/media", "bytes": 12000, "human": "12 KB"},
    {"path": "/srv/backups", "bytes": 4000, "human": "4 KB"}
  ]
}
```

**Implementasyon:**

```python
import shutil

def get_storage():
    usage = shutil.disk_usage("/srv")
    dirs = []
    for entry in os.scandir("/srv"):
        if entry.is_dir():
            total = sum(
                f.stat().st_size
                for f in pathlib.Path(entry.path).rglob("*")
                if f.is_file()
            )
            dirs.append({"path": entry.path, "bytes": total})
    dirs.sort(key=lambda d: d["bytes"], reverse=True)
    return {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "use_percent": round(usage.used / usage.total * 100),
        "dirs": dirs
    }
```

**Not:** `os.walk` yerine `pathlib.rglob` — sadece 1 level deep scan yapılacak (subdirectory'lere inmez, sadece `/srv/X` level). Büyük dizinlerde (TB ölçeğinde) `du` yerine Python — daha kontrollü timeout handling.

### Web Panel UI

- Disk usage progress bar (used/total, yüzde ile)
- Klasör listesi (boyuta göre sıralı, human-readable)
- Mevcut web panel HTML'ine yeni section olarak eklenecek

---

## Feature 3: Cron İyileştirmeleri

### 3.1 Son Scan Sonucu — `GET /junk/last`

**Öncelik: Düşük-Orta**

`junk-scan.sh.j2` çıktıyı JSON dosyasına yazar:

```bash
# junk-scan.sh.j2 sonuna ekle
echo '{"scanned_at":"'$(date -Iseconds)'","total_count":'$COUNT',"total_size":'$SIZE'}' \
  > /opt/homelab/control/last-junk-scan.json
```

API endpoint:

```http
GET /junk/last → Authorization: Bearer <token>
```

```json
{
  "scanned_at": "2026-03-16T03:00:00+03:00",
  "total_count": 47,
  "total_size": 192512
}
```

Web panelde: "Last scan: Sunday 03:00 — 47 files found" satırı.

### 3.2 Schedule Randomize

**Öncelik: Düşük**

```yaml
# Ansible cron task
minute: "{{ 59 | random(seed=inventory_hostname) }}"
```

Host bazlı deterministik rastgele dakika — backup/logrotate çakışmasını önler.

---

## Feature 4: `/logs` Edge Cases

**Öncelik: Düşük** (mevcut çalışıyor, güvenli)

| Edge Case | Mevcut | İyileştirme |
|-----------|--------|-------------|
| stderr/stdout karışması | `result.stdout or result.stderr` | `result.stdout + result.stderr` |
| journalctl locale | `-o short-iso` | `LANG=C` prefix ekle |
| Concurrent calls | Tek-thread, sorun yok | Semaphore (opsiyonel) |
| İleri gelecek | — | WebSocket log streaming |

---

## Feature 5: Watchtower Update History — `GET /updates`

**Öncelik: Orta** — ✅ Implementasyon tamamlandı

Yeni endpoint: Watchtower loglarını parse ederek structured update session'ları döner.

### API

```http
GET /updates
Authorization: Bearer <token>
```

Response:

```json
{
  "sessions": [
    {
      "timestamp": "2026-03-15T16:45:04+03:00",
      "updates": [
        {"image": "ghcr.io/gethomepage/homepage:latest", "action": "found"},
        {"container": "homepage", "action": "recreated"}
      ],
      "summary": {"failed": 0, "scanned": 20, "updated": 2}
    }
  ],
  "total": 1
}
```

### Web Panel UI

- Watchtower Updates tablosu: tarih, güncellenen containerlar, scanned/failed
- Sayfa yüklendiğinde otomatik fetch
- Updated=0 olan session'lar gri renkte

### Whitelist

`watchtower` ve `ntfy` container'ları DOCKER_CONTAINER_WHITELIST'e eklendi — `/logs` endpoint'inden de erişilebilir.

---

## Etkilenen Dosyalar

| Dosya | Değişiklik |
|-------|------------|
| `ansible/roles/homelab_control/templates/control-api.py.j2` | realpath, audit log, storage endpoint, junk/last, race fix |
| `ansible/roles/homelab_control/templates/junk-scan.sh.j2` | JSON çıktı ekle |
| `ansible/roles/homelab_control/templates/logrotate-homelab.j2` | **Yeni dosya** |
| `ansible/roles/homelab_control/defaults/main.yml` | Yeni variable'lar (opsiyonel) |
| `ansible/roles/homelab_control/tasks/deploy.yml` | Logrotate deploy task |
| `tests/test_control_api.py` | Yeni test case'ler |

## Test Stratejisi

- **Symlink bypass:** `os.symlink` mock ile `/srv` dışına escape testi
- **Storage endpoint:** Response format + field validation
- **Junk/last:** JSON parse + missing file handling
- **Audit log:** Log dosyası yazıldığını verify
- Mevcut testler: regression geçmeli (`python3 -m pytest tests/test_control_api.py -v`)
