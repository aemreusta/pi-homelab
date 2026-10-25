# PRD-005: Dış Erişim ve CI/CD

> **Öncelik:** Düşük (tümü)
> **İlgili Sprint:** [Sprint 4 — External Access](../sprints/004-external-access.md)
> **Bağımlılıklar:** PRD-004 (AI/ML servisleri deploy edilmiş olmalı — Zero Trust için)

---

## Bağlam

Pi şu an sadece LAN ve Tailscale üzerinden erişilebilir. Bu PRD, güvenli internet erişimi (Cloudflare Tunnel), SSO koruması (Zero Trust), CI/CD (GitHub Actions runner) ve chat-bazlı yönetim (OpenClaw) özelliklerini kapsar.

---

## Feature 1: GitHub Actions Self-hosted Runner

Pi'yi GitHub Actions runner olarak kaydet — CI dakika sorununun ücretsiz çözümü.

### Teknik Detay

| Özellik | Değer |
|---------|-------|
| Image | `myoung34/github-runner:latest` (veya official) |
| Çalışma modu | Docker container, systemd service olarak da olabilir |
| Labels | `self-hosted`, `linux`, `arm64` |
| Repo | `raspi-homelab` (veya org-level) |

### Konfigürasyon

```yaml
# ansible/roles/github_runner/defaults/main.yml
github_runner_compose_dir: /home/pi/docker/ci/github-runner
github_runner_repo: aemreusta/raspi-homelab
github_runner_labels: "self-hosted,linux,arm64"
```

### Vault

- `vault_github_runner_token` — runner registration token

### Workflow Kullanımı

```yaml
# .github/workflows/lint.yml
jobs:
  lint:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v4
      - run: make lint
```

### Güvenlik Notları

- Runner token kısa ömürlü — Ansible her deploy'da yeni token almalı (veya PAT kullanmalı)
- Runner container'ı Docker socket erişimi gerektirebilir (dikkatli)
- Public repo'larda self-hosted runner güvenlik riski taşır — sadece private repo için

---

## Feature 2: Cloudflare Tunnel

Pi'yi internete açmanın en güvenli yolu — router'da port forwarding yok, DDoS koruması dahil.

### Teknik Detay

| Özellik | Değer |
|---------|-------|
| Image | `cloudflare/cloudflared:latest` |
| Mod | `tunnel run` |
| Config | `/etc/cloudflared/config.yml` (volume mount) |

### Mimari

```text
Internet → Cloudflare Edge → cloudflared container → Traefik (127.0.0.1:80)
                                                   → veya direkt servis port'ları
```

### Konfigürasyon Örneği

```yaml
# cloudflared config.yml
tunnel: <TUNNEL_ID>
credentials-file: /etc/cloudflared/credentials.json

ingress:
  - hostname: mlflow.mydomain.com
    service: http://127.0.0.1:5000
  - hostname: langfuse.mydomain.com
    service: http://127.0.0.1:3100
  - service: http_status:404
```

### Vault

- `vault_cloudflare_tunnel_token` — tunnel credentials

### Ansible Role

```text
ansible/roles/cloudflare_tunnel/
├── defaults/main.yml
│   cloudflare_tunnel_compose_dir: /home/pi/docker/network/cloudflare
├── tasks/
│   ├── main.yml
│   └── deploy.yml             # UFW gereksiz — outbound only
├── templates/
│   ├── docker-compose.yml.j2
│   └── config.yml.j2
├── handlers/main.yml
└── meta/main.yml
```

**Not:** UFW rule gerekmez — cloudflared sadece outbound bağlantı kurar (port 7844 HTTPS, Cloudflare'e).

---

## Feature 3: Cloudflare Zero Trust / Access

Cloudflare Tunnel üzerine SSO — Google login ile servis koruması.

### Korunacak Servisler

| Servis | Hostname | Neden |
|--------|----------|-------|
| MLflow | `mlflow.mydomain.com` | Experiment verileri |
| Langfuse | `langfuse.mydomain.com` | LLM trace verileri |
| Aim | `aim.mydomain.com` | Experiment comparison |
| Prefect | `prefect.mydomain.com` | Pipeline orchestration |

### Konfigürasyon

- **Identity provider:** Google Workspace veya personal Google account
- **Access policy:** Email allowlist (sadece belirli email'ler)
- **Session duration:** 24 saat (configurable)

### Bağımlılıklar

1. Cloudflare Tunnel çalışıyor olmalı (Feature 2)
2. AI/ML servisleri deploy edilmiş olmalı (PRD-004)
3. Cloudflare hesabında domain kayıtlı olmalı

### Vault

- `vault_cloudflare_api_token` — Cloudflare API token (Access policy yönetimi için)

### Not

Zero Trust konfigürasyonu Cloudflare dashboard'dan yapılır. Ansible ile otomasyon mümkün (`cloudflare` Terraform provider veya API) ama ilk kurulumda manuel daha pratik.

---

## Feature 4: OpenClaw AI Assistant

Açık kaynak personal AI assistant — chat uygulamaları üzerinden Pi'yi yönet.

### Teknik Detay

| Özellik | Değer |
|---------|-------|
| Tip | Node.js uygulaması (Docker container) |
| Desteklenen kanallar | Telegram, Discord, WhatsApp |
| AI backend | Claude API / OpenAI API / local model |
| Ref | <https://openclaw.ai/> |

### Entegrasyonlar

| Entegrasyon | Açıklama |
|-------------|----------|
| Homelab Control API | Shutdown, restart, update, junk scan, logs |
| MLflow | Experiment status sorguları |
| MinIO | Bucket/dataset bilgisi |
| Beszel | System metrics (CPU, RAM, disk, temp) |

### Örnek Kullanım

```text
Kullanıcı: "Pi'nin CPU sıcaklığı kaç?"
OpenClaw:  "Beszel'den son veri: CPU 35°C, RAM %19, Disk %65"

Kullanıcı: "Junk dosyaları temizle"
OpenClaw:  "47 junk dosya bulundu (192 KB). Silmek ister misin?"
Kullanıcı: "Evet"
OpenClaw:  "47 dosya silindi, 192 KB yer açıldı."
```

### Bağımlılıklar

1. Cloudflare Tunnel (webhook alabilmek için dış erişim gerekli — Telegram/Discord bot'ları)
2. Homelab Control API (mevcut, PRD-002 iyileştirmeleri ile)

### Vault

- `vault_openclaw_claude_api_key` veya `vault_openclaw_openai_api_key`
- `vault_telegram_bot_token`
- `vault_discord_bot_token` (opsiyonel)

### Ansible Role

```text
ansible/roles/openclaw/
├── defaults/main.yml
├── tasks/
│   ├── main.yml
│   ├── deploy.yml
│   └── ufw.yml
├── templates/
│   ├── docker-compose.yml.j2
│   └── config.yml.j2
├── handlers/main.yml
└── meta/main.yml
```

---

## Özet: Yeni Kaynaklar

| Kaynak | Adet |
|--------|------|
| Ansible roles | 4 (github_runner, cloudflare_tunnel, cloudflare_access, openclaw) |
| Playbooks | 4 |
| Makefile targets | 4 |
| Vault secrets | 5-6 yeni |
| Traefik routes | 0 (Cloudflare Tunnel doğrudan servislere yönlendirir) |
