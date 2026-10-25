# Sprint 3: AI/ML Foundation

> **Historical sprint record.** Statuses and checklists below describe the original
> work window, not today's backlog or deployment state. See
> [PROJECT_INDEX](../../../PROJECT_INDEX.md) for dated evidence.
>
> **Öncelik:** Orta-Düşük
> **Hedef:** ML experiment tracking ve pipeline orchestration altyapısını kurmak.
> **PRD Referansları:** [PRD-004](../prds/004-aiml-infrastructure.md)
> **Bağımlılık:** Sprint 1-2 tamamlanmış olmalı (stabil sistem üzerine ML stack)
>
> **Completion status:** Faz 1 shipped 2026-06-07 (PostgreSQL + MinIO). Faz 2 shipped 2026-06-07 (MLflow + Label Studio). Remaining items (Langfuse, DVC, Prefect, LLM gateway) are deferred.

---

## Faz 1: Temel Altyapı

> PostgreSQL ve MinIO önce deploy edilmeli — diğer tüm ML araçları bunlara bağımlı.

### 3.1 PostgreSQL Deploy

**PRD ref:** PRD-004 — PostgreSQL

- [ ] `ansible/roles/postgresql/` role oluştur:
  - `defaults/main.yml`:

    ```yaml
    postgresql_port: 5432
    postgresql_compose_dir: /home/pi/docker/data/postgresql
    postgresql_data_dir: /srv/data/postgres
    postgresql_databases:
      - { name: mlflow, user: mlflow }
      - { name: langfuse, user: langfuse }
      - { name: prefect, user: prefect }
    ```

  - `tasks/main.yml` → include `deploy.yml` (UFW gereksiz — localhost only)
  - `tasks/deploy.yml` → template compose, create dirs, start container
  - `tasks/configure.yml` → DB ve user oluşturma (idempotent, `docker exec` ile)
  - `templates/docker-compose.yml.j2`:

    ```yaml
    services:
      postgresql:
        image: postgres:16-alpine
        container_name: postgresql
        environment:
          POSTGRES_PASSWORD: "{{ vault_postgres_password }}"
        ports:
          - "127.0.0.1:{{ postgresql_port }}:5432"
        volumes:
          - "{{ postgresql_data_dir }}:/var/lib/postgresql/data"
        restart: unless-stopped
    ```

  - `handlers/main.yml`, `meta/main.yml`
- [ ] `ansible/playbooks/postgresql.yml` oluştur
- [ ] `Makefile` → `postgresql` target ekle
- [ ] Vault'a `vault_postgres_password` ekle

**Not:** `ports: ["127.0.0.1:5432:5432"]` — sadece localhost, UFW rule gereksiz.

---

### 3.2 MinIO Deploy

**PRD ref:** PRD-004 — MinIO

- [ ] `ansible/roles/minio/` role oluştur:
  - `defaults/main.yml`:

    ```yaml
    minio_api_port: 9010    # 9000 Portainer ile çakışır!
    minio_ui_port: 9011
    minio_compose_dir: /home/pi/docker/data/minio
    minio_data_dir: /srv/data/minio
    minio_buckets:
      - mlflow-artifacts
      - dvc-remote
      - datasets
    ```

  - `tasks/main.yml` → include `deploy.yml`, `ufw.yml`
  - `tasks/deploy.yml` → template compose, create dirs, start container
  - `tasks/configure.yml` → `mc` client ile bucket oluşturma
  - `tasks/ufw.yml` → port 9010, 9011 allow LAN
  - `templates/docker-compose.yml.j2`:

    ```yaml
    services:
      minio:
        image: minio/minio:latest
        container_name: minio
        command: server /data --console-address ":9001"
        environment:
          MINIO_ROOT_USER: "{{ vault_minio_root_user }}"
          MINIO_ROOT_PASSWORD: "{{ vault_minio_root_password }}"
        ports:
          - "{{ minio_api_port }}:9000"
          - "{{ minio_ui_port }}:9001"
        volumes:
          - "{{ minio_data_dir }}:/data"
        restart: unless-stopped
    ```

  - `handlers/main.yml`, `meta/main.yml`
- [ ] `ansible/playbooks/minio.yml` oluştur
- [ ] `Makefile` → `minio` target ekle
- [ ] Vault'a `vault_minio_root_user`, `vault_minio_root_password` ekle
- [ ] Traefik routes ekle: `minio.example` (UI:9011), `minio-api.example` (API:9010)

---

## Faz 2: ML Araçları

> Faz 1 (PostgreSQL + MinIO) tamamlandıktan sonra başla.

### 3.3 MLflow Deploy

**PRD ref:** PRD-004 — MLflow
**Depends:** 3.1 (PostgreSQL), 3.2 (MinIO)

- [ ] `ansible/roles/mlflow/` role oluştur:
  - `defaults/main.yml`:

    ```yaml
    mlflow_port: 5000
    mlflow_compose_dir: /home/pi/docker/ml/mlflow
    ```

  - `templates/docker-compose.yml.j2` → PG backend + MinIO artifact store env vars
- [ ] Playbook + Makefile target
- [ ] Traefik route: `mlflow.example` → 5000

---

### 3.4 Langfuse Deploy

**PRD ref:** PRD-004 — Langfuse
**Depends:** 3.1 (PostgreSQL)

- [ ] `ansible/roles/langfuse/` role oluştur:
  - `defaults/main.yml`:

    ```yaml
    langfuse_port: 3100    # 3000 AdGuard ile çakışır!
    langfuse_compose_dir: /home/pi/docker/ml/langfuse
    ```

  - `templates/docker-compose.yml.j2` → PG backend + NextAuth config
- [ ] Playbook + Makefile target
- [ ] Vault'a `vault_langfuse_secret_key`, `vault_langfuse_next_auth_secret` ekle
- [ ] Traefik route: `langfuse.example` → 3100

---

### 3.5 DVC Remote Config

**PRD ref:** PRD-004 — DVC
**Depends:** 3.2 (MinIO)

- [ ] MinIO'da `dvc-remote` bucket oluşturulmuş olmalı (3.2 configure.yml'da)
- [ ] Proje bazlı `.dvc/config` template hazırla:

  ```ini
  [core]
      remote = minio
  ['remote "minio"']
      url = s3://dvc-remote
      endpointurl = http://minio-api.example:9010
  ```

- [ ] Kullanım dokümanı yaz

---

### 3.6 Aim Deploy

**PRD ref:** PRD-004 — Aim
**Depends:** Yok (bağımsız)

- [ ] `ansible/roles/aim/` role oluştur:
  - Port: 43800, dosya bazlı storage
- [ ] Playbook + Makefile target
- [ ] Traefik route: `aim.example` → 43800

---

### 3.7 Ray Deploy

**PRD ref:** PRD-004 — Ray
**Depends:** Yok (bağımsız)

- [ ] `ansible/roles/ray/` role oluştur:
  - Port: 8265 (dashboard)
  - Memory limit: `--memory 2G` (8GB Pi'de max 2-3GB)
- [ ] Playbook + Makefile target
- [ ] Traefik route: `ray.example` → 8265

**Dikkat:** Ray memory-intensive. Deploy sonrası `docker stats` ile RAM kullanımını kontrol et.

---

### 3.8 Prefect Deploy

**PRD ref:** PRD-004 — Prefect
**Depends:** 3.1 (PostgreSQL)

- [ ] `ansible/roles/prefect/` role oluştur:
  - Port: 4200, PG backend
- [ ] Playbook + Makefile target
- [ ] Traefik route: `prefect.example` → 4200

---

## Deploy Sırası

```bash
# Faz 1 — Temel altyapı (sırayla)
make postgresql         # 1. Veritabanı
make minio              # 2. Object storage

# Faz 2 — ML araçları (PostgreSQL/MinIO hazır olduktan sonra)
make mlflow             # 3. Experiment tracking
make langfuse           # 4. LLM observability
make aim                # 5. Experiment comparison (bağımsız)
make ray                # 6. Hyperparameter tuning (bağımsız)
make prefect            # 7. Pipeline orchestration

# Route güncellemeleri
make traefik            # 8. Tüm yeni route'lar
make homepage           # 9. Dashboard widget'lar
```

## RAM Kontrolü (deploy sonrası)

```bash
ssh pi@raspberrypi.local "free -h && docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}'"
```

Beklenen: ~4-5 GB kullanım, ~3 GB available. Swap 2 GB ek buffer sağlar.

## Yeni Kaynaklar Özeti

| Kaynak | Adet |
|--------|------|
| Ansible roles | 8 |
| Playbooks | 8 |
| Makefile targets | 8 |
| Traefik routes | 7 |
| Vault secrets | 5 |
| Toplam yeni port | 8 |
