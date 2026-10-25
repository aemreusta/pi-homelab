# 039 — Containerized ephemeral GitHub Actions runner on the Mac

<!-- CD pipeline: shipped 2026-06-26 (CI runner + Zot fix + CD controllers). -->

> **Status:** Implemented; current runner operations are in the
> [runner README](../../mac-worker/github-runner/README.md). Production CD is
> tag-gated with a never-auto list; see the [current flow](001-architecture.md#cicd-deploy-flow-gitops-design-039).
> The design history below includes superseded master-following/pilot behavior.
> **Related:** [PRD-013 — Zot registry](../archive/prds/013-zot-registry.md),
> [runbook 044 — OrbStack networking](../runbooks/044-2026-06-05-mac-worker-orbstack-networking.md).

## Context

Two gaps converge on one piece of infrastructure:

1. **Fork image → Zot is blocked.** A **public** application fork maintained by the operator must
   build an arm64 image and push it to the local Zot registry (`raspberrypi.local:5050`), which is
   **LAN-only** — a GitHub-hosted runner physically cannot reach it. The fork's build workflow exists
   but has **no runner to execute on**.
2. **Homelab CI is `workflow_dispatch`-only.** The CI workflow runs lint+test only on a
   manual trigger ("CI minutes exhausted" — private repo, GitHub-hosted runners are billed). The
   local pre-commit + pre-push gate now covers lint+test for free, but there is no automatic
   clean-room verification (fresh checkout + fresh dep install, which catches missing
   `requirements.yml` entries / "works-on-my-Mac" drift the local gate cannot see).

Self-hosted runner jobs **do not consume GitHub-hosted runner minutes** (only Actions orchestration +
account limits/policies remain relevant), so one runner solves both: the fork can reach Zot, and
homelab CI becomes free + automatic-on-push.

## Decision

Stand up a **containerized, ephemeral, single-job** GitHub Actions runner **on the always-on Mac
only**. Not native, not on the Pi, not persistent.

- **Ephemeral / single-job.** GitHub recommends ephemeral runners (one job per runner, auto-deregister
  on completion) for self-managed setups; a persistent runner accumulates workspace/cache/credential
  residue across jobs. A small **supervisor** keeps exactly one *idle* ephemeral runner alive **per
  repo**; when a job lands, that runner takes it, exits, and the supervisor respawns a fresh one.
- **JIT (just-in-time) registration.** The supervisor mints a one-time `encoded_jit_config` per
  (re)spawn via the GitHub REST API, so **no long-lived registration token is stored** on disk.
- **Containerized, not native.** Native runners are not versioned/reproducible and leak host state. We
  bake a pinned runner image (pushed to Zot) and run jobs in throwaway containers.
- **Buildx via mounted Docker socket, not DinD.** Each job container mounts `/var/run/docker.sock`, so
  `docker buildx --push` runs against the Mac's daemon and inherits its
  `insecure-registries: ["raspberrypi.local:5050"]`. No Docker-in-Docker.
- **Repo-level, two repos.** Start with repo-scoped runners (not org) to keep blast radius to the two
  repos. Revisit org-level only if the repo count grows.

### Why not the alternatives

Native runner — rejected (not reproducible, leaks host state). Pi runner — rejected (the Pi is
production; it must not run CI). Persistent runner — rejected (cross-job residue is a security and
flakiness liability). Org-level — deferred (repo-level is the smaller blast radius for two repos).
DinD — avoided via the socket mount.

### The Pi's role does NOT change

Pi stays: Zot registry, observability stack, deploy target, production services. The Pi is **not** a
CI worker, build host, or test runner. All CI compute is on the Mac.

## Architecture

```text
GitHub repo (job queued)
  │
  ▼
Mac: github-runner-supervisor  (long-running, 1 per Mac, restart:unless-stopped)
  │  • holds a fine-grained PAT (2 repos, Administration:write) — supervisor env ONLY
  │  • maintains 1 IDLE ephemeral runner container per repo (IDLE_RUNNERS_PER_REPO=1)
  │  • lets up to TWO jobs run at a time across both repos (MAX_CONCURRENT_GLOBAL=2)
  │  • on runner exit (job done) → cleanup workspace → respawn fresh idle runner
  │  • reconciles GitHub runner list; reaps offline registrations >15m
  │  • writes heartbeat + status JSON to NFS /generated/gh-runner/
  ▼
Mac: github-runner-job-<repo>  (ephemeral; --jitconfig + implicit --ephemeral; dies after ONE job)
  │  • mounts /var/run/docker.sock → buildx talks to the Mac daemon
  │  • receives ONLY the encoded_jit_config — never the PAT
  ▼
docker buildx --push  →  raspberrypi.local:5050 (Zot, insecure-registries on the Mac daemon)
  ▼
Pi: docker pull localhost:5050/<image>:<tag>  → make <role>   (operator-run, unchanged)
```

**Labels (uniform across both repos):** `self-hosted, linux, ARM64, homelab-build`
(GitHub default casing — lowercase `linux`, uppercase `ARM64`; the runner container is Linux, so the
old `macOS` label is dropped). The custom `homelab-build` label is what each workflow's `runs-on`
targets.

### Concurrency model

- **`IDLE_RUNNERS_PER_REPO=1`** — one idle ephemeral runner registered per repo, so either repo's job
  is picked up promptly.
- **`MAX_CONCURRENT_GLOBAL=2`** (raised from `1` on 2026-07-03) — up to two jobs run at a time *across
  both repos*, so a slow fork image build and a fast raspi-homelab test no longer head-of-line
  block each other on the Mac Docker daemon / buildx cache. Jobs beyond the 2 slots stay **queued** on
  GitHub (queued ≠ failed; GitHub holds it up to ~24 h). That silent hold is exactly why
  `GHRunnerQueuedTooLong` exists (below). **2 is the ceiling** — two heavy arm64 image builds at once
  thrash the Mac; reduce the queue at the source (workflow `concurrency: cancel-in-progress` on the
  fork's CI) rather than raising the cap further.

## Security boundary (threat model)

**Trust line:** *"the Mac CI host may be compromised; the Pi and the Ansible vault may not."* Everything
below follows from drawing the line there.

- **The Mac CI host is an UNTRUSTED boundary.** It must NOT hold:
  - a `~/.ssh` private key that grants Pi/vault access,
  - the Ansible vault password (`.vault-password`) or a `~/.ansible` tree with secrets,
  - a Tailscale admin/API token,
  - a broad GitHub or Zot token in the Docker/OrbStack credential store.
- **The only credential is a narrow fine-grained PAT** (2 repos, Repository→Administration: read+write
  — the minimum to mint repo-level JIT configs), living **only** in the supervisor container's env.
- **PAT isolation.** The supervisor mints the JIT config and passes **only** `encoded_jit_config` to a
  job container. The job container **never** receives the PAT. (Acceptance: env-dump test inside a job
  container shows no PAT.)
- **Docker socket mount is a privilege grant.** A job can control the Mac daemon (root-equivalent on
  that host). Acceptable *because* the Mac is the dedicated, secret-free CI box and the trust line is
  drawn above it.
- **No CD on the runner.** Deploys need the vault password + SSH-to-Pi, which are barred from the Mac.
  Deploys stay operator-run from the local checkout (as today). A protected `environment: production`
  deploy job is a **non-goal** for now.
- **Public fork mitigations** (operator chose to keep the fork public). GitHub warns
  self-hosted runners should "almost never" be used on public repos — an outside PR could otherwise
  execute on the runner. The accepted mitigation set:
  - **no `pull_request` / `pull_request_target` trigger** — a fork PR never executes on the Mac or
    reads secrets;
  - **owner/event guard** on the job:
    `if: github.repository == '<owner>/<fork>' && (github.event_name == 'workflow_dispatch' || startsWith(github.ref, 'refs/tags/pi-v'))`;
  - **repo settings:** Actions → General → "Require approval for all outside collaborators", workflow
    permissions "Read repository contents only", and restrict `pi-v*` tag-push to the owner;
  - ephemeral one-job containers (no cross-job residue) + the narrow `homelab-build` label;
  - **all third-party actions SHA-pinned** (full 40-char commit SHA, not a moving tag).
- **PAT rotation.** Mandatory expiry (90–180 d), a rotation runbook, a supervisor-startup permission
  smoke-test, and distinct failure metrics (`gh_runner_github_api_auth_failures_total` for 401/403,
  `gh_runner_jit_config_failures_total{status}` for 409/422) so an expired PAT alerts instead of
  masquerading as "runner offline". **Upgrade path:** GitHub App installation token > fine-grained PAT
  > classic PAT.

## Observability (NFS heartbeat → Pi cron → textfile)

The repo's real Mac→Pi metric path is **NFS heartbeat JSON → Pi cron → node_exporter textfile**, *not*
a Mac `/metrics` scrape (earlier Mac workloads wrote heartbeat + status JSON to an NFS share and a
Pi cron read them and emitted a `.prom`). This runner follows the same pattern — no
new scrape target, no firewall change:

- The supervisor writes to the **existing** `nfs-generated` export under a dedicated
  **`gh-runner/`** subdir: `/generated/gh-runner/heartbeat` (epoch, every ~60 s) +
  `/generated/gh-runner/status.json`. The status JSON carries **both** Mac-local and GitHub-visible
  state (the supervisor already calls the API, so it reconciles each tick):
  - *Mac-local:* `idle_count`, `last_job_status`, `last_job_exit_code`, `jobs_total`,
    `orphan_containers`, `image_build_timestamp`, `runner_version`.
  - *GitHub-visible* (supervisor "I started a container" ≠ "GitHub sees it online"):
    per-repo `registered`/`online`/`busy`/`offline`/`queued_jobs`, `jit_last_success_ts`,
    `github_api_rate_remaining`, `github_api_auth_failures_total`, `jit_config_failures_total{status}`.
- A **Pi-side emitter** `gh-runner-metrics.sh` (cron `*/5`, in the new `github_runner_metrics` role)
  reads those NFS files and writes
  `/var/lib/node_exporter/textfile/gh_runner.prom` via the **canonical atomic emit pattern**
  (`{ … } > "$TMP" && mv "$TMP" "$TF" || rm -f "$TMP"`). It emits `gh_runner_*` gauges/counters plus a
  `gh_runner_metrics_last_run_unix_seconds` self-heartbeat (satisfying the repo's "every cron emits a
  metric" rule — it *is* the emitter). On a stale/absent NFS heartbeat it emits an explicit
  `gh_runner_heartbeat_stale 1` gauge — **state, not silence**.

### Metric inventory (`gh_runner_*`)

| Metric | Type | Meaning |
|---|---|---|
| `gh_runner_supervisor_heartbeat_timestamp_seconds` | gauge | epoch the supervisor last wrote NFS heartbeat |
| `gh_runner_metrics_last_run_unix_seconds` | gauge | epoch the Pi emitter last ran (self-heartbeat) |
| `gh_runner_heartbeat_stale` | gauge | 1 when the NFS heartbeat is missing/old, else 0 |
| `gh_runner_idle_count` | gauge | idle ephemeral runner containers the supervisor believes are up |
| `gh_runner_registered_count{repo}` | gauge | runners GitHub lists for the repo |
| `gh_runner_online_count{repo}` | gauge | …online |
| `gh_runner_busy_count{repo}` | gauge | …busy (running a job) |
| `gh_runner_offline_count{repo}` | gauge | …offline (orphans pending reap) |
| `gh_runner_queued_jobs{repo}` | gauge | queued `homelab-build` jobs awaiting a runner |
| `gh_runner_jobs_total` | counter | jobs completed since supervisor start |
| `gh_runner_last_job_exit_code` | gauge | exit code of the most recent job container |
| `gh_runner_orphan_containers` | gauge | exited job containers awaiting cleanup |
| `gh_runner_github_api_auth_failures_total` | counter | 401/403 from the GitHub API (PAT problem) |
| `gh_runner_jit_config_failures_total{status}` | counter | 409/422 minting a JIT config |
| `gh_runner_github_api_rate_remaining` | gauge | REST rate-limit headroom |
| `gh_runner_image_build_timestamp_seconds` | gauge | when the runner image was built (age → staleness) |
| `gh_runner_version_info{version}` | gauge | constant 1, labels the pinned runner version |

### vmalert rules (`gh-runner.yml.j2`, group `gh-runner`)

Thresholds hoisted to `vmalert/defaults/main.yml`; annotations follow
[`vmalert-rule-quoting.md`](../conventions/vmalert-rule-quoting.md) (single-outer / escaped-inner for
any `$value`/`printf`).

| Alert | Fires when | Why it exists |
|---|---|---|
| `GHRunnerSupervisorDown` | supervisor heartbeat stale | the whole runner system is down |
| `GHRunnerMetricsStale` | `gh_runner.prom` mtime stale | the Pi emitter died pre-write |
| `GHRunnerNoIdle` | `gh_runner_idle_count == 0` sustained | no runner to pick up jobs |
| `GHRunnerQueuedTooLong` | a `homelab-build` job queued > 15 m | catches a dead supervisor `NoIdle` alone can't (queued ≠ failed, GitHub holds ~24 h) |
| `GHRunnerGitHubAuthFailure` | `…auth_failures_total` increasing | a revoked/expired PAT otherwise just looks "offline" |
| `GHRunnerImageStale` | image age > 21 d (warn) / > 28 d (crit) | beat GitHub's 30-day `--disableupdate` cliff |
| `GHRunnerJobFailuresBurst` | repeated non-zero job exits | CI broken / runner environment broken |

## Runner image versioning (30-day SLO)

The runner version is pinned **in the image** and `config.sh`/`run.sh` run with `--disableupdate`
(GitHub auto-update would otherwise re-run on every container start). **Operational requirement, not
optional maintenance:** with auto-update off, GitHub stops queueing jobs to a runner more than ~30 days
behind the latest release. So the image carries `gh_runner_image_build_timestamp_seconds` +
`gh_runner_version_info{version}`, and `GHRunnerImageStale` warns at 21 d / crits at 28 d — rebuild and
re-push before the cliff. `.NET 10` / Bun are **not** baked; the workflows install them per-job via
`setup-dotnet`/`setup-bun`. *(Optional later: a repo-specific build image with .NET+Bun
baked, only if per-job install time becomes annoying.)*

## Setup (operator one-time)

1. **Fine-grained PAT** → both repos, Repository permissions → Administration: read+write. Put it in
   `mac-worker/github-runner/.env` (gitignored) only.
2. **Mac Docker/OrbStack** → confirm `~/.docker/daemon.json` has
   `{"insecure-registries":["raspberrypi.local:5050"]}` and OrbStack `network_bridge false` (runbook 044).
3. **Build + push the runner image once** (bootstrap): on the Mac, from
   `mac-worker/github-runner/runner-image/`,
   `docker buildx build --platform linux/arm64 -t raspberrypi.local:5050/homelab/github-runner:<date> --push .`
   (and likewise the supervisor image). The supervisor pulls them thereafter.
4. **Repo Actions secrets** (both repos): `ZOT_USERNAME` / `ZOT_PASSWORD` =
   `vault_zot_username` / `vault_zot_password`. raspi-homelab needs none for lint/test.
5. **Repo settings hardening** for the public fork (see Security boundary).
6. **Start the supervisor:** `bash mac-worker/github-runner/install.sh` (installs the LaunchAgent →
   `docker compose up -d` at login).
7. **Deploy the Pi observability:** `make github-runner-metrics` + redeploy vmalert.

## Verified

*(Fill on first green run.)*

- [ ] Runner shows `Idle` in each repo's Settings → Actions → Runners with the four labels.
- [ ] raspi-homelab push → CI green on the Mac, 0 hosted minutes, uv (not pip) in logs, lint-ports ran.
- [ ] Job finishes → runner removed from the GitHub list; fresh idle runner respawned within ~60 s.
- [ ] fork dispatch → `raspberrypi.local:5050/<image>:<tag>` appears; Pi can `docker pull localhost:5050/<image>:<tag>`.
- [ ] `gh_runner.prom` present; killing the supervisor fires `GHRunnerSupervisorDown`.

## Caveats / non-goals

- **Mac must be online** for CI/builds; jobs queue until it's up. The local pre-commit/pre-push gate
  blocks bad commits regardless — self-hosted CI is a clean-room backstop, not the primary gate.
- **No CD from the runner** (vault/SSH barred from the Mac).
- **Public fork on a self-hosted runner** is against GitHub's general guidance; accepted here only with
  the full mitigation set above.
- **Runner image age is an SLO**, not a nicety — rebuild before the 30-day cliff.

## CD pilot rollout (2026-07-03) — retiring manual `make`

CI (this doc) validates; the **Pi-side `homelab_autodeploy` controller** is the
CD half. It was live but *ungated* — a green commit auto-deployed every changed
role, the highest-blast-radius shape the role's own defaults flagged. The manual
`make <service>` deploys are now being retired onto it **gradually, not
big-bang**, via a pilot allowlist:

- `homelab_autodeploy_target_allowlist` (role defaults) — only these targets
  auto-deploy on a green commit. First cohort (low-risk: stateless, self-healing,
  quick redeploy, no user data, no host provisioning): `homepage`, `grafana`,
  `vmagent`, `node-exporter`, `cadvisor`, `fluentbit`, `homelab-control`.
- **Everything else is HELD** for a manual `make`, surfaced via ntfy
  (`homelab_autodeploy.py plan-skipped`) — a held role is never silently treated
  as "no changes" (silent-cron discipline). `base` is deliberately excluded:
  host/security/storage/docker changes stay manual until the pilot proves out.
- `homelab_autodeploy_enabled` flipped `false→true` in defaults to match the
  already-running cron (fixes a committed-vs-live drift); safe now that the blast
  radius is the allowlist, not "all infra".
- The `make <service>` targets are **not deleted** — they remain the off-LAN /
  emergency escape hatch. `make help` tags pilot ones `[CD pilot]`.
- **Widen one cohort per sprint.** An empty allowlist would mean "no gate,
  auto-deploy everything" — the full-rollout end state, reached only after each
  tier earns trust.

`plan_targets`/`plan_skipped` are pure and unit-tested; `make cd-drill` covers
the bash orchestrator end-to-end. See the
[CI/CD deploy flow diagram](001-architecture.md#cicd-deploy-flow-gitops-design-039).

## Remaining

- P1: PAT rotation runbook; GitHub App migration; optional baked fork build image.
- ~~Raise `MAX_CONCURRENT_GLOBAL` once determinism is proven.~~ Done 2026-07-03 (1→2; ceiling reached — see Concurrency model).
- CD pilot: widen `homelab_autodeploy_target_allowlist` one cohort per sprint as each tier proves out.
