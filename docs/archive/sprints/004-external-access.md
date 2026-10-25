# Sprint 4: External Access & CI/CD

> **Historical sprint record.** Statuses and checklists below describe the original
> work window, not today's backlog or deployment state. See
> [PROJECT_INDEX](../../../PROJECT_INDEX.md) for dated evidence.
>
> **Öncelik:** Düşük
> **Hedef:** Pi'yi güvenli şekilde internete açmak ve CI pipeline kurmak.
> **PRD Referansları:** [PRD-005](../prds/005-external-access.md)
> **Bağımlılık:** Sprint 3 (AI/ML servisleri deploy edilmiş olmalı — Zero Trust koruması için)

---

## Tasks

### 4.1 GitHub Actions Self-hosted Runner

**PRD ref:** PRD-005 Feature 1

Pi'yi GitHub Actions runner olarak kaydet — CI dakika sorununun ücretsiz çözümü.

- [ ] `ansible/roles/github_runner/` role oluştur:
  - `defaults/main.yml`:

    ```yaml
    github_runner_compose_dir: /home/pi/docker/ci/github-runner
    github_runner_repo: aemreusta/raspi-homelab
    github_runner_labels: "self-hosted,linux,arm64"
    ```

  - `templates/docker-compose.yml.j2`
  - `tasks/main.yml` → deploy + register
- [ ] `ansible/playbooks/github-runner.yml` oluştur
- [ ] `Makefile` → `github-runner` target ekle
- [ ] Vault'a `vault_github_runner_token` ekle
- [ ] `.github/workflows/` → `runs-on: self-hosted` test workflow

**Güvenlik notu:** Public repo'larda self-hosted runner risk taşır. Sadece private repo (`raspi-homelab`) için kullan.

---

### 4.2 Cloudflare Tunnel

**PRD ref:** PRD-005 Feature 2

Router'da port forwarding olmadan güvenli internet erişimi.

- [ ] `ansible/roles/cloudflare_tunnel/` role oluştur:
  - `defaults/main.yml`:

    ```yaml
    cloudflare_tunnel_compose_dir: /home/pi/docker/network/cloudflare
    ```

  - `templates/docker-compose.yml.j2`:

    ```yaml
    services:
      cloudflared:
        image: cloudflare/cloudflared:latest
        container_name: cloudflared
        command: tunnel run
        environment:
          TUNNEL_TOKEN: "{{ vault_cloudflare_tunnel_token }}"
        restart: unless-stopped
    ```

  - `templates/config.yml.j2` → ingress rules
  - `tasks/main.yml` → deploy (UFW gereksiz — outbound only)
- [ ] `ansible/playbooks/cloudflare-tunnel.yml` oluştur
- [ ] `Makefile` → `cloudflare-tunnel` target ekle
- [ ] Vault'a `vault_cloudflare_tunnel_token` ekle
- [ ] Cloudflare dashboard'da tunnel oluştur, token al

**Not:** Firewall rule gereksiz — cloudflared sadece outbound bağlantı kurar.

---

### 4.3 Cloudflare Zero Trust

**PRD ref:** PRD-005 Feature 3
**Depends:** 4.2 (Tunnel), Sprint 3 (ML servisleri)

Google SSO ile ML servislerini koru.

- [ ] Cloudflare dashboard → Access → Applications:
  - `mlflow.mydomain.com` → Email allowlist policy
  - `langfuse.mydomain.com` → Email allowlist policy
  - `aim.mydomain.com` → Email allowlist policy
  - `prefect.mydomain.com` → Email allowlist policy
- [ ] Identity provider: Google account
- [ ] Session duration: 24 saat
- [ ] Vault'a `vault_cloudflare_api_token` ekle (opsiyonel, Terraform/API kullanılacaksa)

**Not:** İlk kurulumda Cloudflare dashboard'dan manuel konfigürasyon daha pratik. Ansible otomasyonu ileride eklenebilir.

---

### 4.4 OpenClaw AI Assistant

**PRD ref:** PRD-005 Feature 4
**Depends:** 4.2 (Tunnel — webhook erişimi için)

Chat uygulamaları üzerinden Pi kontrolü.

- [ ] `ansible/roles/openclaw/` role oluştur:
  - `templates/docker-compose.yml.j2`
  - `templates/config.yml.j2` → entegrasyon ayarları
- [ ] `ansible/playbooks/openclaw.yml` oluştur
- [ ] `Makefile` → `openclaw` target ekle
- [ ] Vault'a ekle:
  - `vault_openclaw_claude_api_key` veya `vault_openclaw_openai_api_key`
  - `vault_telegram_bot_token`
- [ ] Entegrasyonları konfigüre et:
  - Homelab Control API (shutdown, restart, update, junk scan, logs)
  - Beszel (system metrics)
  - MLflow (experiment status)
  - MinIO (storage info)

---

## Deploy Sırası

```bash
make github-runner       # 1. CI runner (bağımsız)
make cloudflare-tunnel   # 2. Tunnel (internet erişimi)
# 3. Zero Trust → Cloudflare dashboard'dan manuel
make openclaw            # 4. AI assistant (tunnel hazır olduktan sonra)
```

## Yeni Kaynaklar Özeti

| Kaynak | Adet |
|--------|------|
| Ansible roles | 3-4 (github_runner, cloudflare_tunnel, openclaw; zero trust dashboard'dan) |
| Playbooks | 3-4 |
| Makefile targets | 3-4 |
| Vault secrets | 5-6 |
| Traefik routes | 0 (Cloudflare Tunnel doğrudan yönlendirir) |
