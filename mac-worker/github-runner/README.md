# mac-worker/github-runner — ephemeral GitHub Actions runner

A containerized, **ephemeral, single-job** GitHub Actions runner that runs on
this Mac and serves the homelab repos. A long-running **supervisor** keeps one
*idle* runner alive per repo, mints a one-time JIT config per spawn, and respawns
a fresh runner after each job. Jobs run in throwaway containers; the runner
auto-deregisters from GitHub when its one job finishes.

Full design + threat model: [`docs/design/039-mac-ci-runner.md`](../../docs/design/039-mac-ci-runner.md).

## What it gives you

- **Image builds → Zot:** an image-build workflow can reach the
  LAN-only registry `raspberrypi.local:5050` (a GitHub-hosted runner cannot).
- **Free homelab CI:** `raspi-homelab` lint+test runs on every push to master,
  on the Mac, consuming **no GitHub-hosted runner minutes**.

## One-time setup

1. **Fine-grained PAT** — both repos, Repository permissions → *Administration:
   read+write*. This is the minimum to mint repo-level JIT configs.
2. `cp .env.example .env` and fill in `GITHUB_PAT` + `RUNNER_IMAGE`. **`.env` is
   gitignored — never commit a real PAT.**
3. **Docker/OrbStack:** `~/.docker/daemon.json` →
   `{"insecure-registries":["raspberrypi.local:5050"]}`; OrbStack `network_bridge false`
   (runbook 044). Restart Docker.
4. **Build + push the images once** (bootstrap — the supervisor pulls them after):

   ```bash
   cd runner-image
   docker buildx build --platform linux/arm64 \
     --build-arg RUNNER_VERSION=<current actions/runner release> \
     --build-arg BUILD_EPOCH=$(date +%s) \
     -t raspberrypi.local:5050/homelab/github-runner:$(date +%Y-%m-%d) --push .
   cd ../supervisor
   docker buildx build --platform linux/arm64 \
     -t raspberrypi.local:5050/homelab/github-runner-supervisor:latest --push .
   ```

   Set `RUNNER_IMAGE` in `.env` to the tag you just pushed.
5. **Repo Actions secrets** (both repos, for the fork build): `ZOT_USERNAME` /
   `ZOT_PASSWORD` = `vault_zot_username` / `vault_zot_password`.
6. **Start it:** `bash install.sh` (installs the `io.homelab.github-runner`
   LaunchAgent → `docker compose up -d` at login; re-runnable/idempotent).

## Operating it

```bash
docker compose ps                 # supervisor up?
docker compose logs -f            # supervisor activity (spawns, reaps, API)
docker compose restart            # after editing .env
launchctl bootout  gui/$(id -u)/io.homelab.github-runner   # stop at-login start
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.homelab.github-runner.plist
```

In each repo: **Settings → Actions → Runners** — you should see an `Idle` runner
with labels `self-hosted, linux, ARM64, homelab-build`.

## Keeping the runner image fresh (automated)

Auto-update is pinned off, so GitHub stops queueing jobs to a runner >30 days
behind the latest `actions/runner` release. `install.sh` also installs a second
LaunchAgent — **`io.homelab.github-runner-rebuild`** — that runs
[`scripts/rebuild-runner-image.sh`](scripts/rebuild-runner-image.sh) on the **1st
and 15th at 04:00** (max image age ~15 days, under the 21d `GHRunnerImageStale`
warn). Each run resolves the latest `actions/runner` release, rebuilds+pushes
with a fresh `BUILD_EPOCH`, repoints `RUNNER_IMAGE` in `.env`, and recreates the
supervisor.

It is **self-observing**: the script writes `rebuild-status.json` into the NFS
`generated/gh-runner/` dir; the Pi emitter turns it into `gh_runner_rebuild_*`
metrics and the **`GHRunnerRebuildFailed`** / **`GHRunnerRebuildStale`** vmalert
rules fire if a scheduled rebuild runs but fails to build/push — caught *before*
the 21d image-age cliff, not after. A dead scheduler still trips the existing
`GHRunnerImageStale` backstop.

```bash
launchctl list | grep github-runner-rebuild          # scheduled?
bash scripts/rebuild-runner-image.sh                 # run one now (manual)
tail -f logs/rebuild.log                             # watch it
```

## Troubleshooting

- **Runner never appears / "offline":** check `docker compose logs` for PAT
  auth errors (401/403). A revoked/expired PAT fires `GHRunnerGitHubAuthFailure`
  Pi-side. Confirm the PAT has *Administration: read+write* on both repos.
- **Job queued forever:** with `MAX_CONCURRENT_GLOBAL=2` jobs beyond the 2-slot
  limit wait; a push burst on one repo (e.g. several image builds) drains a
  couple at a time. `GHRunnerQueuedTooLong` fires after 15 min if nothing is
  picking up — check the runner shows `busy=true` (draining) vs a real stall.
- **Build can't reach Zot:** the daemon needs `insecure-registries` for
  `raspberrypi.local:5050`, and OrbStack must be in NAT mode (`network_bridge false`).
- **Image age alert (`GHRunnerImageStale`):** rebuild + re-push the runner image
  before it is ~30 days behind the latest `actions/runner` release (auto-update
  is pinned off — see 039). Bump `RUNNER_VERSION`.

## Layout

| Path | Role |
|---|---|
| `compose.yml` | the supervisor service (mounts the Docker socket + NFS `generated`) |
| `.env.example` | config template (PAT, repos, image, concurrency) |
| `install.sh` | phased, idempotent installer (LaunchAgent + verify) |
| `io.homelab.github-runner.plist` | at-login + every-15-min LaunchAgent (runs `../ensure-compose.sh`) |
| `runner-image/` | pinned `actions/runner` job image (`--disableupdate`) |
| `supervisor/` | `supervisor.py` — JIT mint, spawn/reap, NFS status emit |
| `scripts/` | manual cleanup backstops (containers, build cache) |
