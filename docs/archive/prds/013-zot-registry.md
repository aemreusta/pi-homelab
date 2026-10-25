# PRD 013 — Zot: local OCI registry for homelab-built images

> **Related:** role `ansible/roles/zot`. **Status:** Shipped 2026-06-23.

## Purpose

A LAN-local container registry + web UI so we can build homelab images on the Mac,
push them here, and have the Pi pull them — without a public registry. Any
homelab-built image uses the same path.

## Why Zot, not Harbor

The operator asked for "a Harbor-like service." Harbor itself was rejected for this
host: it's an 8+ container stack (core, portal, jobservice, registry, registryctl,
**PostgreSQL**, **Redis**, **Trivy**, nginx) with a ~1.5-2 GB working set. On this
8 GB Pi — already `mem_limit`-overcommitted (Σ 15.69 GiB) with a documented
swap-death history, and with a single media app alone wanting 4 GB at peak —
that is a swap-death risk, not a convenience.

[Zot](https://github.com/project-zot/zot) is the Harbor-like experience the Pi can
carry: a single Go binary (~50-150 MB live) giving an OCI-native registry + web UI
(repo/tag browse) + retention/GC + optional CVE scan. CNCF, vendor-neutral.

## Implementation (shipped)

| Setting | Value |
|---|---|
| Image | `ghcr.io/project-zot/zot-linux-arm64:latest` (full = UI+search) |
| Host port | **5050** (5000 is MLflow) → container 5000 |
| Runs as | `1000:1000` (host owner; bind-mounts writable) |
| Storage | `/srv/registry` (SSD) — `gc` + `dedupe` + retention keep it bounded |
| Auth | htpasswd (bcrypt), one account from vault (`vault_zot_username/password`) |
| UI (browser) | `https://registry.example` via Traefik (TLS) |
| Registry (push) | `raspberrypi.local:5050` (plain HTTP + Mac `insecure-registries`) |
| Pi pull | `localhost:5050` (insecure-exempt; Pi daemon logged in by the role) |
| Hardening | `cap_drop: ALL`, `no-new-privileges`, distroless (no shell) |
| Monitoring | vmagent job `zot` scrapes `/metrics` (basic-auth) → `ZotDown` vmalert |

### Notable details

- **`/metrics` is gated** behind `accessControl.metrics.users` in zot — even an
  authenticated repo user gets 401 until listed there. The config adds the registry
  user, and the vmagent job authenticates with the same creds.
- **No container healthcheck** — the zot image is distroless (no shell/curl), so
  liveness is the `/metrics` up-scrape + `ZotDown` (same precedent as
  portainer/beszel). `ZotDown` is `severity: warn` (a down registry only blocks
  *local* image pulls; upstream images still pull).
- **htpasswd hashed on the controller** (Mac has passlib+bcrypt<4.1, the Portainer
  precedent) and copied to the Pi, which has no passlib — `delegate_to: localhost`.
- **Retention**: keep the 10 most-recently pushed/pulled tags per repo, drop
  untagged older than 168h. Important because `/` runs ~93 % full.

## Pushing from the Mac (build host)

Docker rejects plain-HTTP registries unless `localhost` or trusted. The Pi pulls via
`localhost:5050` (exempt); the **Mac** opts in once:

```jsonc
// ~/.docker/daemon.json (Docker Desktop) or OrbStack settings
{ "insecure-registries": ["raspberrypi.local:5050"] }
```

```bash
docker login raspberrypi.local:5050        # vault_zot_username / vault_zot_password
docker push raspberrypi.local:5050/<image>:<sha>
```

Then point the consuming role's image variable at `localhost:5050/<image>:<sha>`,
disable Watchtower for that container, and redeploy the role.

## Acceptance (all met 2026-06-23)

1. `make zot` exits 0; container `Up`. ✓
2. Anonymous `/v2/_catalog` → 401; authed → 200. ✓
3. End-to-end `docker push` + `docker pull` cycle via `localhost:5050`. ✓
4. `https://registry.example` UI → 200 through Traefik TLS. ✓
5. `up{job="zot"}=1` in VictoriaMetrics; `ZotDown` rule loaded in vmalert. ✓

## Follow-ups (not blocking)

- Optional **Trivy** CVE scanning (`extensions.trivy`) — left off by default to
  keep the footprint small; enable if image-vuln visibility is wanted.
