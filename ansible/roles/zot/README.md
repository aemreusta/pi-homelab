# zot — local OCI registry + UI

[Zot](https://github.com/project-zot/zot) is a single-binary, OCI-native container
registry with a web UI. It hosts **locally-built homelab images** (e.g. forks of
upstream apps built on the Mac CI runner) so we don't depend on a public registry
for in-house builds.

## Why Zot, not Harbor

Harbor is an 8+ container stack (core, portal, jobservice, registry, registryctl,
PostgreSQL, Redis, Trivy, nginx), ~1.5-2 GB working set. On this 8 GB Pi — already
`mem_limit`-overcommitted with a documented swap-death history — that is unsafe.
Zot delivers the same push/pull + browse-UI experience in ~50-150 MB.

## Endpoints

| Use | URL |
|---|---|
| Registry API + UI (LAN) | `http://raspberrypi.local:5050` |
| Registry API + UI (browser, TLS) | `https://registry.example` (via Traefik) |
| Pi-local pull (insecure-exempt) | `localhost:5050` |

Auth is htpasswd (bcrypt); one account from vault (`vault_zot_username` /
`vault_zot_password`). Port **5050** because host 5000 is MLflow.

## Pushing from the Mac (build host)

Docker rejects plain-HTTP registries unless they're `localhost` or explicitly
trusted. The Pi pulls via `localhost:5050` (exempt). The **Mac** must opt in once:

```jsonc
// ~/.docker/daemon.json  (Docker Desktop) — or OrbStack settings
{ "insecure-registries": ["raspberrypi.local:5050"] }
```

Then:

```bash
docker login raspberrypi.local:5050           # vault_zot_username / vault_zot_password
docker tag  homelab/myapp:<sha> raspberrypi.local:5050/myapp:<sha>
docker push raspberrypi.local:5050/myapp:<sha>
```

The Pi then pulls `localhost:5050/myapp:<sha>` — pin the consuming role's
image variable to that tag and redeploy the role.

## Storage hygiene

`gc` + `dedupe` + a retention policy (keep the 10 most-recently pushed/pulled tags
per repo, drop untagged older than 168h) keep the registry bounded — important
because `/` runs ~93 % full.

## Monitoring

The image is distroless (no shell) so there is no container healthcheck; liveness
is the Prometheus `/metrics` scrape (vmagent job `zot`) + the `ZotDown` vmalert
rule — same precedent as the scratch-based portainer/beszel images.
