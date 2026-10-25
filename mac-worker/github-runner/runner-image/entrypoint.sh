#!/usr/bin/env bash
# entrypoint.sh — run exactly ONE job, then exit. The supervisor passes a
# one-time JIT config in ENCODED_JIT_CONFIG (minted from the PAT, which this
# container never sees). `--jitconfig` runs an implicitly-ephemeral runner: it
# registers, takes a single job, auto-deregisters from GitHub, and exits.
#
# NOTE: there is no `--disableupdate` flag on run.sh (it belongs to config.sh,
# which JIT mode skips). A JIT runner may self-update at startup if the image is
# behind; the update lands in this throwaway container only. We keep the image
# version current (the GHRunnerImageStale SLO) to keep those per-job updates rare
# — see design 039.
set -euo pipefail

: "${ENCODED_JIT_CONFIG:?ENCODED_JIT_CONFIG must be provided by the supervisor}"

cd /home/runner

# One job, ephemeral. The exit code propagates to the supervisor
# (→ gh_runner_last_job_exit_code).
exec ./run.sh --jitconfig "$ENCODED_JIT_CONFIG"
