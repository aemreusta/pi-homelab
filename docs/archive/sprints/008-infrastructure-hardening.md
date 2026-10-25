# Sprint 8: Infrastructure Hardening — Tamamlandi (2026-03-27)

> **Historical sprint record.** Statuses and checklists below describe the original
> work window, not today's backlog or deployment state. See
> [PROJECT_INDEX](../../../PROJECT_INDEX.md) for dated evidence.
>
> **Oncelik:** Kritik + Yuksek
> **Hedef:** Mevcut 20 container altyapisini saglam hale getirmek: hardcoded degerleri temizle, log/health/CI eksiklerini kapat, Traefik'i guvenli yap.
> **Analiz Referansi:** Sprint 6 Kod Analizi ve Best Practices (tamamlanıp silindi; Git geçmişinde)
> **Bagimliliklari:** Yok (bagimsiz calisabilir)
> **Durum:** Tamamlandi (2026-03-27)

---

## Motivasyon

Sprint 6 analizi (2026-03-24) genel skor: **6.5/10**. Sistem calisiyorsa da, sessiz hatalara karsi koruma yok:

| Problem | Etki | Sayisi | Durum |
|---------|------|--------|-------|
| Hardcoded `192.0.2.0/24` | Farkli subnet'te deploy kirilir | 5 dosya | Cozuldu |
| Container log rotation yok | Disk dolabilir | 17/20 container | Cozuldu |
| Healthcheck yok | Beszel/Portainer container durumunu bilmiyor | 19/20 container | 5 kritik servis eklendi |
| CI/CD yok | Lint/test sadece lokal, push'ta kontrol yok | 0 workflow | Cozuldu |
| Traefik dashboard acik | Auth olmadan erisim, TLS redirect yok | 1 servis | Cozuldu |
| Undefined variable (`tailscale_dns_ip`) | Deploy fail eder | 1 dosya | Cozuldu |

**Prensip:** Yeni servis eklemeden once mevcut altyapiyi saglamlastir.

---

## Faz 1: Degisken Temizligi (Kritik) — Tamamlandi

### 8.1 LAN Subnet Degiskeni — Hardcoded CIDR Temizligi

**Guncellenecek dosyalar:**

- [x] `ansible/inventory/group_vars/all/main.yml` — `lan_subnet` ve `tailscale_subnet` degiskenleri eklendi
- [x] `ansible/roles/nfs_server/defaults/main.yml` — NFS export options `{{ lan_subnet }}` kullanir
- [x] `ansible/roles/homelab_control/tasks/ufw.yml` — `from_ip: "{{ lan_subnet }}"`
- [x] `ansible/roles/tailscale/defaults/main.yml` — `tailscale_advertise_routes: "{{ lan_subnet }}"`

---

### 8.3 Eksik/Hatali Degiskenler

- [x] `ansible/roles/tailscale_dns/defaults/main.yml` — `tailscale_dns_ip` eklendi (default: `100.64.0.10`)
- [x] `ansible/roles/filebrowser/templates/docker-compose.yml.j2` — `filebrowser_puid/filebrowser_pgid` kullanir
- [x] `ansible/roles/nfs_server/defaults/main.yml` — `{{ docker_user }}` referansi

---

## Faz 2: Container Sagligi — Tamamlandi

### 8.4 Logging Standardizasyonu

Tum compose template'lerine `logging: json-file, max-size: 10m, max-file: 3` eklendi:

- [x] `adguard/templates/docker-compose.yml.j2`
- [x] `portainer/templates/docker-compose.yml.j2`
- [x] `homepage/templates/docker-compose.yml.j2`
- [x] `filebrowser/templates/docker-compose.yml.j2`
- [x] `watchtower/templates/docker-compose.yml.j2`
- [x] `traefik/templates/docker-compose.yml.j2`
- [x] `media/templates/docker-compose.yml.j2` (qbittorrent, sonarr, radarr, prowlarr, byparr, jellyfin)
- [x] `jellyseerr/templates/docker-compose.yml.j2`
- [x] `maintainerr/templates/docker-compose.yml.j2`
- [x] `beszel/templates/docker-compose.yml.j2` (hub + agent)
- [x] `ntfy/templates/docker-compose.yml.j2`
- [x] `tailscale_dns/templates/docker-compose.yml.j2`

**Not:** `homelab_control` systemd servisi (Docker degil), `tailscale` ve `nfs_server` compose template'i yok.

---

### 8.5 Health Check — Kritik Servisler

| Servis | Healthcheck | Durum |
|--------|-------------|-------|
| adguard | `wget --spider -q http://localhost:80` | Eklendi |
| traefik | `wget --spider -q http://localhost:8180/api/overview` | Eklendi |
| portainer | `wget --spider -q http://localhost:9000` | Eklendi |
| jellyfin | `curl -f http://localhost:8096/health` | Eklendi |
| beszel-hub | `wget --spider -q http://localhost:8090` | Eklendi |
| ntfy | Zaten vardi | Mevcut |

**Not:** `homelab_control` systemd servisi — Docker healthcheck degil, `/health` endpoint mevcut.

---

### 8.6 Eksik Ansible Tag'leri

- [x] `ansible/roles/nfs_server/tasks/main.yml` — `tags: [nfs_server]` ve `tags: [nfs_server, firewall]`

---

## Faz 3: Guvenlik — Tamamlandi

### 8.7 Traefik Hardening

- [x] Security headers middleware: `frameDeny`, `browserXssFilter`, `contentTypeNosniff`, `forceSTSHeader` (HSTS 1yr)
- [x] Dashboard BasicAuth: `traefik_dashboard_htpasswd` degiskeni (vault uzerinden, varsayilan: devre disi)
- [x] Ping endpoint: `ping: {}` eklendi (healthcheck icin)
- [x] Traefik handler: `become: true` eklendi
- [x] Security headers tum router'lara (LAN + Tailscale) uygulanir
- [ ] HTTP→HTTPS redirect — LAN servislerin cogu HTTP kullanir, ertelendi

---

## Faz 4: CI/CD & Otomasyon — Tamamlandi

### 8.8 GitHub Actions — Lint + Test Pipeline

- [x] `.github/workflows/ci.yml` olusturuldu
  - **Lint job:** `ansible-lint --offline` + Ansible collections
  - **Test job:** `pytest` + Jinja2 template rendering
  - Trigger: push/PR to master (ansible/tests/scripts paths)
- [x] `ansible/requirements.yml` mevcut ve guncel

---

### 8.9 Makefile — `test` ve `validate` Target'lari

- [x] `make test` — `python3 -m pytest tests/ -v` (43 test, 1 skipped)
- [x] `make validate` — lint + syntax-check + test

---

## Basari Kriterleri

### Zorunlu (Must Have)

- [x] Hardcoded `192.0.2.0/24` sifir — tumu `{{ lan_subnet }}` kullanir
- [x] Tum compose template'lerde `logging:` blogu var
- [x] 5 kritik serviste healthcheck var (adguard, traefik, portainer, jellyfin, beszel)
- [x] GitHub Actions CI, push'ta lint + test calistiriyor
- [x] `tailscale_dns_ip` undefined hatasi cikmaz
- [x] `make validate` calisiyor

### Olmali (Should Have)

- [x] Traefik dashboard BasicAuth destegi (vault ile aktive edilir)
- [x] Security headers middleware aktif
- [x] `nfs_server` tag'leri eklendi

### Olabilir (Could Have)

- [ ] HTTP→HTTPS redirect (LAN servisler icin opsiyonel) — ertelendi
- [x] Container `no-new-privileges` — tum 20 container'a eklendi
- [ ] Molecule test framework (Sprint 6.8'den) — ertelendi (single-host icin dusuk ROI)

---

## Commit Gecmisi

| Commit | Faz | Degisiklik |
|--------|-----|-----------|
| `73540f7` | 1 | refactor: extract hardcoded LAN subnet and params into variables |
| `8ce3202` | 2 | feat: add logging and healthchecks to all containers, fix missing tags |
| `327fdd5` | 3 | fix: harden Traefik with security headers, dashboard auth, ping endpoint |
| `45c21c9` | 4 | ci: add GitHub Actions pipeline and Makefile test/validate targets |

---

## Sprint 6 ile Iliski

Bu sprint, Sprint 6 analiz raporundan turetlen **en yuksek etkili** maddeleri icerir:

| Sprint 6 Maddesi | Sprint 8 Karsiligi | Durum |
|-------------------|---------------------|-------|
| 6.1 Global Variables | 8.1 LAN Subnet + 8.3 Fixes | Tamamlandi |
| 6.3 Container Security | no-new-privileges tum container'lara | Tamamlandi |
| 6.4 Health Checks | 8.5 Healthchecks | Tamamlandi (5 kritik servis) |
| 6.5 Logging | 8.4 Logging | Tamamlandi |
| 6.6 Validation | validate.yml pre-flight checks | Tamamlandi |
| 6.7 CI/CD | 8.8 GitHub Actions | Tamamlandi |
| 6.9 Standards | docs/guides/ansible-standards.md | Tamamlandi |
| 6.10 Diagrams | docs/design/001-architecture.md | Tamamlandi |
| 6.2 Compose Dir | — | Atlanildi (redeployment riski) |
| 6.8 Molecule | — | Atlanildi (single-host dusuk ROI) |

Sprint 6 planlama dokumanlari (006-code-analysis-report.md, 006-infrastructure-best-practices.md) tamamen uygulanip silindi.

---

## Ilgili Belgeler

- [Ansible Standards](../../guides/ansible-standards.md) — Variable naming, compose template kurallari
- [Architecture Diagrams](../../design/001-architecture.md) — Network, servis, storage, boot diyagramlari
- [Sprint 1 Stability](001-stability-security.md) — Tamamlanan stabilite sprint'i

---

*Tamamlanma: 2026-03-27*
*Kaynak: Repo robustness analizi + Sprint 6 code analysis report*
