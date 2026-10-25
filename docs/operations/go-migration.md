# Owned runtime migration to Go

**Decision: accepted, 2026-09-27.** Go is the default for repository-owned
services, workers and automation. Ansible/dev tools and third-party applications
remain. Current URLs, schedules, metric names, state contracts, notification
limits and recovery behavior are migration requirements.

## First implementation

| Component | Code / verification | Production status |
| --- | --- | --- |
| Shared `runtime/` module | Standard library only; Linux/Darwin ARM64 builds; race/vet checks | Bootstrapped and executed on Pi, 27 September |
| Runner collector | `homelab-agent runner-metrics`; differential Python fixtures and three Pi benchmark sizes | Go active; installed Python fallback also exercised with isolated output paths; seven-day observation open |
| Backup log observer | `homelab-agent backup-observe`; shell failure contracts and real rclone local-backend smoke | Deployed; full production cloud retry remains open |
| Pipeline statistics | `homelab-agent log-count`; actual HTTP/shell fixtures, strict count contract | Live: natural cron publishes success and reads canonical counts; 28 September |
| Control storage scan | One background refresh, cached response, completion-based TTL | Deployed; ten cold concurrent requests returned in 6–21 ms; completed cache contained 14 directories, no scan error; still Python |
| Indexer functional observation | `homelab-agent indexer-health`; real HTTP, backoff-time, malformed-body, redirect, timeout, secret-publication and rendered-wrapper fixtures; new owned logic with no Python predecessor | Active after `.2`; canonical ownership correction `.3` completed 11 October 10:08:45 TRT. Natural 10:09 run and five inactive rules verified. Isolated Pi median 1,393 ms / 0.01 s CPU, peak 6,896 KiB RSS; browser resource cost excluded. Media probes the installed command, CD builds runtime first. [Contract and rollback](indexer-health.md) |

These are the first cutovers, not a completed whole-repository migration.

Container watchdog Go cutover is active after `.4` and the deliberate common
monitoring-tag apply, 11 October. Seven legacy-shell differential fixtures preserve
decisions/state/actions, with failed-command urgent cooldown the intentional fix.
Interleaved Pi medians legacy/Go: 0.75/0.69 s wall, 0.16/0.13 s CPU; maximum
GNU-time command/child RSS 28,180/28,296 KiB. Natural 11:30:02 cron published
40 containers without actions; TSDB and four inactive evaluator rules were
verified at 11:32. The shell engine stays installed for rollback; no actual
production failure/restart injection is claimed. [Contract](watchdog.md).

[First-wave measurements](../research/2026-09-27-go-wave1.md) and
[raw evidence](../research/evidence/2026-09-27-go-wave1.json) record the exact
binary, commits and validation scope. Typical input: Python 143.3 ms / 10,664 KiB
peak RSS, Go 16.7 ms / 3,148 KiB; all three workloads produced equivalent series.
Large-input measurement found and removed repeated label-encoder allocation.

The first runtime/consumer bootstrap and backup/storage recovery used Ansible
syntax + live deep-check + apply from the local checkout. This is distinct from
a CI-green deploy-tag release. `deploy-v2026.09.27.1` bundles the first and
second waves for release reconciliation; inspect CI and Pi state for promotion. Python fallback and both initial Go
artifacts remain; the isolated fallback exercise does not claim a production
rollback deployment drill.

The CD controller was deliberately bootstrapped at `5b06db1`; live source
mapping recognizes `runtime/`. The new backup progress rule is loaded among
161 metric and 10 log rules with no reported evaluation errors at 18:48 TRT.

The first tag completed rollout at 00:24 TRT on 28 September. The running
runtime identity is `6d92ae015eaa006744d493957cd333d0743b46e4abe2809a3b187d706ac7e0d9`;
00:44 TRT checks found 164 metric and 10 log rules without evaluation errors.
Thirty Pi runs measured medians of 7.1 ms / 1724 KiB for `log-count`. These are
command measurements, not aggregate host savings. Storage requests under concurrent deployment took
1.05–1.27 seconds; the later background scan completed 14 directories without errors.

## Build and deployment

- `make go-test`: race tests and vet; `make go-build`: two ARM64 binaries under
  ignored `build/`. `make validate` and CI include the Go tests.
- `homelab_runtime` builds from the exact checkout on the Pi. The Go 1.27.1 Linux
  ARM64 compiler archive is checksum-pinned from [official release metadata](https://go.dev/dl/?mode=json)
  (~64 MiB, only downloaded at bootstrap/version change). Compilation is nice=15,
  `-p=1`, `GOMAXPROCS=2`, `CGO_ENABLED=0`, `GOTOOLCHAIN=local`, `GOPROXY=off`.
  Production jobs need only the binary. The compiler cache reduces later build cost.
- A SHA256 identity covers source paths/contents and compiler version. Sources
  and binaries live under `/opt/homelab/runtime/releases/<identity>/`.
  Removed source files cannot survive into the next build. Verify the staged
  binary before atomic activation; retain prior artifacts and backup copies.
- `runtime/` and `scripts/go-source-id.py` map to `homelab-runtime` in CD.
  Bootstrap the changed deny-listed controller deliberately; normal future
  releases retain CI + deploy-tag gates.
- Check mode validates inputs and predicts source changes; it does not download,
  compile or execute a nonexistent prospective binary. Actual apply verifies it.

## Rollback

For the runner, deploy `github_runner_metrics_engine=python` through Ansible;
its module remains installed, and cron/metric paths stay the same. Do not silently
fall back on a Go error: preserve the last textfile so stale-collector alerts work.

To select a retained binary, deploy `homelab-runtime.yml` with
`homelab_runtime_rollback_id=<64-character identity>`. An absent artifact fails
before activation. Binary rollback must be coordinated with consumer contracts:
only select a release that contains every command used by the deployed scripts.
Before Go existed, backup rollback means restoring the prior Ansible template.
Keep first-wave rollback artifacts for at least 14 days; do not prune during soak.

## Remaining sequence and exit criteria

1. Observe the runner through at least 4 October, including stale/malformed input and missing
   files; preserve HELP/TYPE/sample semantics. Record latency, CPU and RSS on the
   actual Pi. Sort series for comparison; map ordering is not a contract. Keep
   rollback artifacts through at least 11 October; do not remove the fallback
   automatically when either date arrives.
2. Read-only monitoring helpers: DNS, kernel I/O, coverage and health collectors.
3. Control API routes, clients and storage inventory; retain authorization and
   command allowlists. Frontend choice is independent of collector migration.
4. Media decisions and Mac workers. Do not assume a language change accelerates
   FFmpeg or network waits.
5. Deployment controllers last, with identity, last-good and rollback drills intact.

For each component record: old/new contracts, fixtures, CPU/RSS/I/O/subprocess
count/latency on small/typical/large workloads, shadow differences, release SHA,
cutover time and rollback evidence. A repeatable >10% regression blocks that
cutover. Measure aggregate system savings separately from microbenchmarks.

## Load fixes accompanying the first wave

Rclone JSONL is streamed to `/var/log/homelab-gdrive-rclone.log`; detailed errors
survive the run. The observer publishes current bytes/errors/rate and a running
heartbeat; the shell preserves rclone and observer exit codes independently.
Last-success state survives failure. Existing empty-source and retention semantics
remain. The failed 27 September sync is not retroactively repaired by new logging.

The storage API returns capacity immediately on cold start and last-known directory
sizes while refreshing. A single worker pool (at most two scans) prevents concurrent
requests multiplying filesystem I/O; failures retain the previous sample and back off.
Persistent inventory and cloud/local workload isolation remain subsequent work.
