# PRD-004: AI/ML Altyapısı

> **Öncelik:** Orta (temel altyapı) / Düşük (ML araçları)
> **İlgili Sprint:** [Sprint 3 — AI/ML Foundation](../sprints/003-aiml-foundation.md)
>
> **Implementation status (2026-06-07):**
>
> - ✅ **Faz 1:** PostgreSQL (role `postgresql`) + MinIO (role `minio`) — shipped
> - ✅ **Faz 2:** MLflow (role `mlflow`, custom Dockerfile) + Label Studio (role `label_studio`) — shipped
> - ⬜ Deferred: Langfuse, DVC, Prefect, Aim, Ray, LLM gateway (Bifrost)

---

## Bağlam

Raspberry Pi 4B üzerinde self-hosted ML experiment tracking ve pipeline orchestration altyapısı. Tüm servisler Docker container olarak çalışacak.

**Donanım kısıtları:**

- RAM: 8 GB toplam, ~1.5 GB kullanımda, ~6.2 GB available
- Disk: 615 GB boş (ext4 SSD)
- CPU: ARM64 (Cortex-A72) — bazı ML image'ları ARM64 desteği gerektirir
- Swap: 2 GB (852 MB kullanımda)

---

## Dependency Graph

```text
PostgreSQL ──┬── MLflow ←── MinIO (artifact store)
             ├── Langfuse
             └── Prefect

MinIO ───────┬── MLflow artifacts (s3://mlflow-artifacts)
             ├── DVC remote (s3://dvc-remote)
             └── Datasets (s3://datasets)

Aim ─────────── bağımsız (dosya bazlı)
Ray ─────────── bağımsız (tek node)
```

**Deploy sırası:** PostgreSQL → MinIO → (MLflow, Langfuse, Prefect, DVC) → (Aim, Ray)

---

## Temel Altyapı (Faz 1)

### PostgreSQL

Ortak veritabanı — MLflow, Langfuse ve Prefect için paylaşılan backend.

| Özellik | Değer |
|---------|-------|
| Image | `postgres:16-alpine` |
| Port | 5432 (**localhost only** — dışarıya expose edilmeyecek) |
| Volume | `/srv/data/postgres` |
| RAM tahmini | 100-200 MB |
| Restart policy | `unless-stopped` |

**Veritabanları:**

| DB | Kullanıcı | Servis |
|----|-----------|--------|
| `mlflow` | `mlflow` | MLflow backend store |
| `langfuse` | `langfuse` | Langfuse event store |
| `prefect` | `prefect` | Prefect orchestration |

**Vault secrets:**

- `vault_postgres_password` — superuser password

**Ansible role:**

```text
ansible/roles/postgresql/
├── defaults/main.yml
│   postgresql_port: 5432
│   postgresql_compose_dir: /home/pi/docker/data/postgresql
│   postgresql_data_dir: /srv/data/postgres
│   postgresql_databases:
│     - { name: mlflow, user: mlflow }
│     - { name: langfuse, user: langfuse }
│     - { name: prefect, user: prefect }
├── tasks/
│   ├── main.yml
│   ├── deploy.yml
│   └── configure.yml          # DB ve user oluşturma (idempotent)
├── templates/
│   └── docker-compose.yml.j2
├── handlers/main.yml
└── meta/main.yml
```

**docker-compose.yml.j2 notu:**

- `ports: ["127.0.0.1:5432:5432"]` — sadece localhost'tan erişim
- UFW rule gerekmiyor (localhost-only)
- `POSTGRES_PASSWORD: {{ vault_postgres_password }}`

### MinIO

S3-uyumlu nesne depolama — MLflow artifact store, DVC remote, dataset storage.

| Özellik | Değer |
|---------|-------|
| Image | `minio/minio:latest` |
| API Port | **9010** (9000 Portainer ile çakışır!) |
| UI Port | **9011** (9001 Portainer HTTPS ile çakışır!) |
| Volume | `/srv/data/minio` |
| RAM tahmini | 200-300 MB |
| Restart policy | `unless-stopped` |

**Port çakışma çözümü:**

| Planlanan | Mevcut Çakışma | Yeni Port |
|-----------|----------------|-----------|
| MinIO API 9000 | Portainer HTTP 9000 | **9010** |
| MinIO UI 9001 | — | **9011** |

**Bucket'lar:**

| Bucket | Kullanım |
|--------|----------|
| `mlflow-artifacts` | MLflow experiment artifacts |
| `dvc-remote` | DVC dataset versioning |
| `datasets` | Raw/processed datasets |

**Vault secrets:**

- `vault_minio_root_user`
- `vault_minio_root_password`

**Traefik routes:**

- `minio.example` → `127.0.0.1:9011` (UI)
- `minio-api.example` → `127.0.0.1:9010` (S3 API)

**Ansible role:**

```text
ansible/roles/minio/
├── defaults/main.yml
│   minio_api_port: 9010
│   minio_ui_port: 9011
│   minio_compose_dir: /home/pi/docker/data/minio
│   minio_data_dir: /srv/data/minio
│   minio_buckets:
│     - mlflow-artifacts
│     - dvc-remote
│     - datasets
├── tasks/
│   ├── main.yml
│   ├── deploy.yml
│   ├── ufw.yml
│   └── configure.yml          # mc client ile bucket oluşturma
├── templates/
│   └── docker-compose.yml.j2
├── handlers/main.yml
└── meta/main.yml
```

---

## ML Araçları (Faz 2)

### MLflow

Experiment tracking + model registry. Tüm ML deney parametreleri, metrikleri ve artifact'ları burada.

| Özellik | Değer |
|---------|-------|
| Image | `ghcr.io/mlflow/mlflow:2-latest` (pin; verify arm64 tag) |
| Port | 5000 |
| Traefik route | `mlflow.example` |
| Backend store | PostgreSQL (`mlflow` DB) |
| Artifact store | MinIO (`s3://mlflow-artifacts`) via `--serve-artifacts` proxy |
| RAM tahmini | 200-300 MB |
| **Bağımlılık** | PostgreSQL + MinIO (both on mlstack_net) |
| **Gerekli sürücüler** | psycopg2-binary + boto3 — use `-full` image or uv-built image |

**⚠️ Corrected environment (three bugs in original — see analysis):**

```yaml
# Service-name resolution via mlstack_net — NOT the LAN IP (which breaks on
# WiFi↔Ethernet change and is unreachable from inside a bridge container anyway).
MLFLOW_BACKEND_STORE_URI: postgresql://mlflow:{{ vault_mlflow_db_password }}@postgresql:5432/mlflow
MLFLOW_S3_ENDPOINT_URL: http://minio:9000
# Scoped MinIO service-account keys (not root creds — see B4 in the review).
AWS_ACCESS_KEY_ID: "{{ vault_minio_mlflow_access_key }}"
AWS_SECRET_ACCESS_KEY: "{{ vault_minio_mlflow_secret_key }}"
# Serve-artifacts mode: clients fetch through MLflow, not directly to MinIO.
# mlflow server --serve-artifacts --artifacts-destination s3://mlflow-artifacts
```

### Langfuse

LLM observability — trace, prompt versiyonlama, cost tracking. Claude/OpenAI API çağrılarını izlemek için.

| Özellik | Değer |
|---------|-------|
| Image | `langfuse/langfuse:latest` |
| Port | **3100** (3000 AdGuard ile çakışır!) |
| Traefik route | `langfuse.example` |
| Backend | PostgreSQL (`langfuse` DB) |
| RAM tahmini | 200-300 MB |
| **Bağımlılık** | PostgreSQL |

**Port çakışma çözümü:**

| Planlanan | Mevcut Çakışma | Yeni Port |
|-----------|----------------|-----------|
| Langfuse 3000 | AdGuard setup 3000 | **3100** |

**Vault secrets:**

- `vault_langfuse_secret_key` — encryption key
- `vault_langfuse_next_auth_secret` — NextAuth session secret

### DVC + MinIO Remote

Veri seti versiyonlama. Git-like workflow for datasets.

| Özellik | Değer |
|---------|-------|
| Tip | Client-side tool (Pi'de container yok) |
| Remote | MinIO `s3://dvc-remote` |
| **Bağımlılık** | MinIO |

**Proje bazlı `.dvc/config` template:**

```ini
[core]
    remote = minio
['remote "minio"']
    url = s3://dvc-remote
    endpointurl = http://minio-api.example:9010
    access_key_id = {{ vault_minio_root_user }}
    secret_access_key = {{ vault_minio_root_password }}
```

### Aim

W&B self-hosted alternatifi — experiment karşılaştırma UI. MLflow'dan farklı olarak interaktif comparison dashboard sunar.

| Özellik | Değer |
|---------|-------|
| Image | `aimstack/aim:latest` |
| Port | 43800 |
| Traefik route | `aim.example` |
| Storage | Dosya bazlı (`.aim/` dizini) |
| RAM tahmini | 100-200 MB |
| **Bağımlılık** | Yok (bağımsız) |

### Ray (Single Node)

Paralel hyperparameter tuning ve preprocessing. Kubernetes gerektirmez — tek node üzerinde çalışır.

| Özellik | Değer |
|---------|-------|
| Image | `rayproject/ray:latest` |
| Dashboard | 8265 |
| Traefik route | `ray.example` |
| RAM tahmini | 200-500 MB (iş yüküne göre değişir) |
| **Bağımlılık** | Yok (bağımsız) |

**Dikkat:** Ray memory-intensive olabilir. `--memory` flag ile limit set edilmeli. 8GB Pi'de max 2-3GB Ray'a ayrılabilir.

### Prefect

ML pipeline orkestrasyon — Airflow'dan hafif. DAG-based workflow engine.

| Özellik | Değer |
|---------|-------|
| Image | `prefecthq/prefect:2-python3.11` |
| Port | 4200 |
| Traefik route | `prefect.example` |
| Backend | PostgreSQL (`prefect` DB) |
| RAM tahmini | 150-300 MB |
| **Bağımlılık** | PostgreSQL |

---

## RAM Budget

| Servis | Min | Max | Not |
|--------|-----|-----|-----|
| **Mevcut kullanım** | 1.5 GB | 1.5 GB | 20 container |
| PostgreSQL | 100 MB | 200 MB | `shared_buffers=128MB` |
| MinIO | 200 MB | 300 MB | Idle state |
| MLflow | 100 MB | 200 MB | Web UI + tracking server |
| Langfuse | 200 MB | 300 MB | Next.js app |
| Aim | 100 MB | 200 MB | Idle state |
| Ray | 200 MB | 500 MB | Aktif job yokken düşük |
| Prefect | 150 MB | 300 MB | Server + agent |
| **Toplam** | **2.6 GB** | **3.5 GB** | |
| **Mevcut + yeni** | **4.1 GB** | **5.0 GB** | |
| **Kalan** | **3.5 GB** | **2.6 GB** | 8 GB toplam |

Swap (2 GB) ile birlikte toplam 10 GB adreslenebilir — yeterli, ama Ray aktif job çalıştırırken dikkatli olunmalı.

---

## Port Allocation (Tam Liste)

| Port | Servis | Durum |
|------|--------|-------|
| 5432 | PostgreSQL | **Yeni** (localhost only) |
| 9010 | MinIO API | **Yeni** (Portainer çakışması çözüldü) |
| 9011 | MinIO UI | **Yeni** |
| 5000 | MLflow | **Yeni** |
| 3100 | Langfuse | **Yeni** (AdGuard çakışması çözüldü) |
| 43800 | Aim | **Yeni** |
| 8265 | Ray | **Yeni** |
| 4200 | Prefect | **Yeni** |

## Yeni Vault Secrets

| Secret | Servis |
|--------|--------|
| `vault_postgres_password` | PostgreSQL superuser |
| `vault_minio_root_user` | MinIO admin user |
| `vault_minio_root_password` | MinIO admin password |
| `vault_langfuse_secret_key` | Langfuse encryption |
| `vault_langfuse_next_auth_secret` | Langfuse session |

## Yeni Makefile Targets

```makefile
postgresql, minio, mlflow, langfuse, aim, ray, prefect
```

## Yeni Traefik Routes

```yaml
- { name: mlflow, port: 5000 }
- { name: langfuse, port: 3100 }
- { name: minio, port: 9011 }      # UI
- { name: minio-api, port: 9010 }  # S3 API
- { name: aim, port: 43800 }
- { name: ray, port: 8265 }
- { name: prefect, port: 4200 }
```
