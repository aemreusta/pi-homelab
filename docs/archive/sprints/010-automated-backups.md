# Sprint 10: Automated Backups

> **Historical sprint record.** Statuses and checklists below describe the original
> work window, not today's backlog or deployment state. See
> [PROJECT_INDEX](../../../PROJECT_INDEX.md) for dated evidence.
>
> **Öncelik:** Kritik
> **Hedef:** Stateful container verilerini haftalık olarak `/srv/backups/` altına yedekle, 4 hafta retention, başarısızlıkta ntfy alert. qBittorrent 5.x migration incident'i (2026-05-13) gibi olayların kalıcı veri kaybına yol açmasını engelle.
> **PRD Referansları:** Yeni — bu sprint kendi spec'i. Future: `docs/archive/prds/009-backups.md` ileride genişletilirse.
> **Bağımlılık:** `homelab_control` (cron + ntfy zaten yerleşik), `/srv` ext4 disk.
> **İlgili incident:** 2026-05-13 qBittorrent 5.x data migration

---

## 1. Mevcut Durum & Risk

**Hiçbir otomatik yedek yok.** Tüm container config + DB dosyaları `/home/pi/docker/<role>/config/` veya benzeri konumlarda. Tek koruma: `restart: unless-stopped` + diskin fiziksel sağlığı.

| Veri | Kayıp riski neyi mahveder? | Recovery effort |
|---|---|---|
| qBittorrent `BT_backup/` (`.fastresume` + `.torrent`) | Tüm aktif torrentlerin session verisi (586 dosya) | Yüksek — yeniden ekleme + yeniden hash, downloads yeni baştan ya da resume failures |
| Sonarr / Radarr / Prowlarr `*.db` | Indexer config, kalite profilleri, custom formats, history | Orta-yüksek — quality profile + indexer setup günler sürer |
| Jellyseerr / Maintainerr `db/` | Request history, kullanıcı eşleştirmeleri | Düşük — temel config |
| AdGuard `AdGuardHome.yaml` | DNS rewrites, blocklist seçimi, custom rules | Düşük — Ansible role zaten yeniden üretir |
| Grafana `grafana.db` | Dashboard + datasource provisioning + custom panels | Düşük — provisioning Ansible'dan yeniden gelir, user-edited dashboards kaybolur |
| VictoriaLogs/Metrics `data/` | Tarihsel telemetri | İhmal edilebilir — 30d/90d zaten kayan pencere, yedekleme overhead'i değmez |

---

## 2. Hedef Sözleşmesi

1. **Haftalık otomatik backup** — Pazar 03:00 (docker-prune'un bir saat öncesi, conflict'i önlemek için)
2. **Snapshot dizini:** `/srv/backups/<service>/<YYYY-MM-DD>/` — tarihli klasörler
3. **Retention:** 4 hafta (4 snapshot) — `find -mtime +28 -delete`
4. **Atomik:** her servis için (a) container'ı durdur ya da uygun SQLite online-backup kullan, (b) tar.gz arşivi yaz, (c) container'ı yeniden başlat
5. **Notification:** ntfy on success (summary: kaç dosya, kaç MB) + ntfy URGENT on failure (priority=urgent, action=Open Backup Log)
6. **Manuel trigger:** `make backup` ve `POST /backup` (control API endpoint) — yedeklemeyi anlık çalıştırır
7. **Test path:** Bir servis için "restore'a denemek için bu adımları izle" runbook'u — başlangıç için qBittorrent kullan
8. **Lint coverage:** `make lint` + `make validate` yeşil

---

## 3. Mimari

```text
┌─ Pi 4B ───────────────────────────────────────────────────┐
│                                                            │
│  cron (Sunday 03:00)                                       │
│       │                                                    │
│       ▼                                                    │
│  /usr/local/bin/homelab-backup.sh                          │
│       │                                                    │
│       ├── docker stop/quiesce <service>                    │
│       ├── tar.gz <data dir> → /srv/backups/<svc>/<date>/   │
│       ├── SQLite online backup for live DBs                │
│       ├── docker start <service>                           │
│       ├── prune: find -mtime +28 -delete                   │
│       └── ntfy summary (per service + total)               │
│                                                            │
│  POST /backup endpoint (homelab_control)                   │
│       └── exec same script (manual trigger)                │
│                                                            │
│  /srv/backups/                                             │
│  ├── qbittorrent/                                          │
│  │   ├── 2026-05-17/{BT_backup.tar.gz,config.tar.gz}       │
│  │   └── 2026-05-24/...                                    │
│  └── *arr/...                                              │
└────────────────────────────────────────────────────────────┘
```

---

## 4. Per-Service Backup Recipe

| Service | Method | Quiesce | Files |
|---|---|---|---|
| qBittorrent | `docker stop` + tar | Stop required (SQLite WAL not safe to copy live) | `BT_backup/`, `data/BT_backup/`, `qBittorrent.conf`, `config/qBittorrent.conf` |
| Sonarr/Radarr/Prowlarr | SQLite `.backup` via `docker exec sqlite3` | Live OK | `*.db`, `config.xml` |
| Jellyseerr | `docker stop` + tar (db is small) | Stop OK (~3s) | `config/db/`, `settings.json` |
| Maintainerr | `docker stop` + tar | Stop OK | `data/` (whole dir) |
| AdGuard | tar (read-only file copy) | No quiesce | `conf/AdGuardHome.yaml` |
| Grafana | SQLite `.backup` via `docker exec` | Live OK | `grafana.db` |

**VL / VM / Beszel:** explicitly NOT backed up — kayan pencere telemetri, retention zaten görevini yapıyor.

---

## 5. Role Spec

### `ansible/roles/backup/`

```text
backup/
├── defaults/main.yml          # backup_dir, retention_days, ntfy_url, service list
├── tasks/
│   ├── main.yml               # include deploy.yml
│   └── deploy.yml             # template + chmod + cron
├── templates/
│   ├── homelab-backup.sh.j2   # main orchestrator
│   ├── backup-qbittorrent.sh.j2
│   ├── backup-sqlite.sh.j2    # generic SQLite online backup
│   └── backup-tar.sh.j2       # generic stop+tar pattern
├── handlers/main.yml          # logrotate trigger
└── meta/main.yml              # depends: docker, homelab_control (for ntfy_url var)
```

### Variables (`defaults/main.yml`)

```yaml
backup_root: /srv/backups
backup_retention_days: 28
backup_schedule: "0 3 * * 0"  # Sunday 03:00
backup_log: /var/log/homelab-backup.log
backup_ntfy_url: "{{ homelab_control_ntfy_url }}"

# Per-service config (each gets a generated /usr/local/bin/backup-<service>.sh)
backup_services:
  - { name: qbittorrent, method: stop_tar, paths: [config/qbittorrent] }
  - { name: sonarr,      method: sqlite,  paths: [config/sonarr/sonarr.db] }
  - { name: radarr,      method: sqlite,  paths: [config/radarr/radarr.db] }
  - { name: prowlarr,    method: sqlite,  paths: [config/prowlarr/prowlarr.db] }
  - { name: jellyseerr,  method: stop_tar, paths: [jellyseerr/config/db] }
  - { name: maintainerr, method: stop_tar, paths: [maintainerr/data] }
  - { name: adguard,     method: tar,     paths: [adguard/conf/AdGuardHome.yaml] }
  - { name: grafana,     method: sqlite,  paths: [observability/grafana/data/grafana.db] }
```

---

## 6. Homelab Control API endpoint

`POST /backup` → trigger `homelab-backup.sh` in background daemon thread, return 202.
`GET /backup/last` → parse latest `homelab-backup.log` entries, return JSON summary.

Implement in `roles/homelab_control/templates/control-api.py.j2`. Token-gated like other POST endpoints.

---

## 7. Tasks Checklist

### 10.1 backup role skeleton

- [ ] `ansible/roles/backup/` (defaults, tasks/main+deploy, templates, handlers, meta)
- [ ] `ansible/playbooks/backup.yml`
- [ ] `Makefile` target: `backup` (deploys role) + `backup-run` (trigger immediate via SSH)

### 10.2 Orchestrator script

- [ ] `homelab-backup.sh.j2` — iterate `backup_services`, log to `backup_log`, invoke per-method helper, summarize for ntfy
- [ ] On failure: ntfy `priority=urgent`, tag `rotating_light`, action button → `http://<lan>:9099/?token=...`

### 10.3 Per-method scripts

- [ ] `backup-tar.sh.j2` — `docker stop <svc> ; tar czf … ; docker start <svc>` with rc check
- [ ] `backup-sqlite.sh.j2` — `docker exec <svc> sqlite3 <db> ".backup /tmp/<svc>.bak" ; docker cp <svc>:/tmp/<svc>.bak …` (no service stop)
- [ ] (qBittorrent special case is just stop_tar — covered by backup-tar.sh)

### 10.4 Retention prune

- [ ] In orchestrator: `find {{ backup_root }} -maxdepth 2 -type d -mtime +{{ backup_retention_days }} -exec rm -rf {} +`
- [ ] Log how many snapshots pruned per service

### 10.5 Cron + state

- [ ] `ansible.builtin.cron` task: `0 3 * * 0 /usr/local/bin/homelab-backup.sh`
- [ ] logrotate entry for `backup_log`
- [ ] `/var/lib/homelab/backup.state` for last-run timestamp (used by daily-report)

### 10.6 Control API integration

- [ ] `POST /backup` → background thread runs script, ntfy on completion
- [ ] `GET /backup/last` → parse log → JSON
- [ ] Web panel: "Run Backup Now" button + last-run timestamp + per-service status

### 10.7 Test & restore runbook

- [ ] Run `make backup-run` once; verify `/srv/backups/<svc>/<date>/` populated
- [ ] qBittorrent restore drill: stop container, blow away config, restore from latest snapshot, start, verify torrents reload
- [ ] Write `docs/runbooks/backup-restore.md` documenting the recovery procedure

### 10.8 Verification

- [ ] `make lint` + `make validate` green
- [ ] First Sunday 03:00 cron fires; ntfy summary received
- [ ] Disk usage sane: `du -sh /srv/backups` < 5 GB (qBit + app DBs are small)

---

## 8. Out of Scope

- **Off-site backup** (rsync to Mac / S3 / Backblaze) — Sprint 11 candidate. This sprint only handles local snapshots.
- **VL / VM / Beszel data** — kayan retention zaten görev tanımını yapıyor.
- **Application-level data** (`/srv/media` gibi büyük media) — bu sprint config dosyalarına odaklanır, media için rclone/rsync ayrı bir konu.
- **Hot/incremental backup** — full snapshot her hafta yeterli. Borgbackup/Restic ileride.

---

## 9. Success Criteria

- [ ] 4 ardışık Pazar gecesi başarılı backup ntfy mesajı alındı
- [ ] Bir servis için restore drill yapıldı, eski snapshot'tan başarıyla geri dönüldü
- [ ] Disk `/srv/backups/` 4-snapshot retention'la stabil (sürekli büyümüyor)
- [ ] Backup script bir hata fırlatırsa ntfy urgent alert tetikliyor
- [ ] Control API endpoint çalışıyor (manual trigger + last-run query)

---

## 11. Close-out (2026-05-19, post-PM review)

Sprint shipped on 2026-05-18 as three commits: `cb07b13` (unrelated
ride-along), `8590a0d` (role + tests + Control API endpoints),
`325935e` (atomic-rename fix + restore runbook with verified drill
output). 83 tests green; first ad-hoc run produced 11/11 services
landing, 720 MB total in 3.5 min.

PM review caught 8 items. Status (W1-W8) recorded here so a future
reader has the honest close-out, not a polished one:

### W1 — Restore drill scope

✓ Resolved. The runbook (`docs/runbooks/028-backup-restore.md`)
documents **four** verified drills, not one, with verbatim output,
including:

| Service | Snapshot count | Live count | Result |
|---|---|---|---|
| Grafana | 1 user, 1 org | 1 user, 1 org | match |
| qBittorrent | 1 078 `.fastresume` files | 1 078 (recursive) | match |

All four drills surfaced one real bug (the `gunzip -c x.gz | sqlite3
:memory:` pattern doesn't work — sqlite3 needs a file path, not piped
bytes) which the runbook now documents as a "wrong pattern that bit
me." The qBit drill specifically replayed the 2026-05-13 incident
shape and is the strongest validation of the sprint's premise.

### W2 — Cron path unverified until 2026-05-24

✓ Acknowledged, scheduled. Three runs to date were ad-hoc; the first
`0 3 * * 0 /usr/local/bin/homelab-backup.sh` cron entry will not fire
until **2026-05-24 03:00 local (00:00 UTC)**. Verification check
landed in [Sprint 015](015-quality-debt-payback.md) §"Sprint 10
follow-ups" as a deferred queue item: on 2026-05-25, confirm
`/var/log/homelab-backup.log` has a `{"event":"summary",...}` entry
near 03:03 Sunday and ntfy fired the success summary.

### W3 — Disk-alert was already in fired-state

✗ Confirmed. State file `/var/lib/homelab/disk-alert-srv.state`
contents (2026-05-19): `1779117302 76` → last fired
2026-05-18 15:15 UTC at 76 % (before the backup deploy bumped us to
77 % → 78 %). Disk-alert hasn't re-fired because:

- 24 h cooldown (set in `common_disk_alert_cooldown_hours: 24`)
- +5 pp escape isn't tripped (78 - 76 = 2 pp)

So Sprint 014's "alerts are quiet" observation was partially wrong —
silent due to cooldown, not all-clear. The script worked as designed;
the operator just didn't know it had already fired.

**Action items folded into Sprint 015:**

1. Add a hard cap `backup_disk_quota_gb: 50` to the role defaults
   plus a pre-flight check in `homelab-backup.sh`: abort with urgent
   ntfy if `du -sh /srv/backups` exceeds the cap.
2. Decide whether disk-alert should re-fire on +2 pp or wait for +5 pp
   (current behavior). 78 % at the current trajectory hits 81 % in
   weeks, so +5 pp is fine — but the policy should be **explicit** in
   `docs/design/024-alert-discipline.md`.
3. The actual current `/srv/backups` footprint is 720 MiB; the 50 GB
   cap is 70× headroom for now.

### W4 — Media-app SQLite backup vs 2-3 GB estimate

✓ Resolved. Cross-checked live: one media app's ≈451 MiB live SQLite DB
backed up to ≈448 MiB uncompressed / ≈432 MiB compressed (gzip ratio 3.6 %).
The 3 MB drift between live and backup uncompressed is the WAL state
at the moment of `.backup` (whatever was buffered but not yet
checkpointed). The compression ratio is extremely low because that app
stores image blobs directly in the SQLite DB — they're already compressed
media bytes, gzip can't help. So the backup is correct; the 2-3 GB plan
estimate was just pessimistic.

**Memory-worthy fact:** a BLOB-heavy SQLite DB is nearly incompressible.
When sizing future backup quotas, treat it as `~live_size × 0.96` — assume
*no* gzip savings.

### W5 — Stale unstaged working-tree files

✓ Acknowledged. `.claude/settings.local.json` (Claude Code permission
additions) and `.pre-commit-config.yaml` (ruff bump v0.15.10 → v0.15.13)
have ridden across multiple sprints. Folded into Sprint 015 §"Workspace
hygiene" as a 2-minute chore commit.

### W6 — Sprint 015 backlog needed a home

✓ Resolved. [Sprint 015](015-quality-debt-payback.md) skeleton landed
alongside this close-out, capturing: silent-failure ADR (PM I1),
M4 cleared notifications, 6-task idempotency drift, PRD-008 refresh,
byparr healthcheck fix (PM W8), quarterly audit cadence (PM W7), and
this sprint's W2/W3/W5 follow-ups.

### W7 — Quarterly noise-floor audit (2026-08-16)

✓ Resolved. Folded into Sprint 015. Mechanism: a `homelab-audit-due`
cron stub that fires an `ntfy` reminder at `0 9 1 8 *` (2026-08-01
09:00) so the operator has two weeks to schedule the audit before the
quarterly target date. Implementation deferred to Sprint 015.

### W8 — byparr healthcheck still misleading

✗ Outstanding. The `/docs` healthcheck returns 200 OK while a real
bypass returns 500 — documented as the "healthcheck blind spot" in
`memory/byparr_leak_observation.md` but never fixed. Fold into
Sprint 015 as part of the **silent-failure ADR**: byparr is the
canonical case study for that ADR ("a container reports healthy while
doing nothing useful").

### Other measured close-out facts

- **Stale partial file:** none observed after the 3rd backup run
  (a sampled `/srv/backups/<service>/2026-05-18/` contains only the final `.gz`,
  no `.partial.<pid>` leftovers). Atomic-rename pattern working as
  designed.
- **Cron line:** Ansible-managed, verified present:
  `0 3 * * 0 /usr/local/bin/homelab-backup.sh >> /var/log/homelab-backup.log 2>&1`
- **Pi network sample:** RTT to 8.8.8.8 = 10 ms (TurkNet ISP). Pi has
  good baseline connectivity — actual upstream throughput must be
  measured (`iperf3` or `speedtest-cli`) before Sprint 11 decides
  bandwidth pacing.

### Verdict

Sprint 10 closed end-to-end live. Deferred items are tracked, not
forgotten. Sprint 11 (offsite) inherits a clean local-backup chain
plus the explicit gaps surfaced by this close-out.
