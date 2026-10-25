#!/bin/bash
# ensure-compose.sh <project_dir> — re-assert a Mac compose project.
#
# Shared by every Mac compose LaunchAgent (e.g. io.homelab.github-runner for
# the CI supervisor). launchd runs
# it at login AND on a StartInterval, so it is the recovery path for any way a
# project can end up down: a container crash while the engine is healthy is
# covered by restart:unless-stopped, but OrbStack restarting, being stopped, or
# the Mac waking from sleep/hibernation with the Pi's NFS not yet reachable is
# not. The last case kept the runner down 19h on 2026-09-28: its NFS volume
# timed out once at login and the old one-shot agent never tried again.
# See docs/incidents/2026-09-28-mac-sleep-runner-one-shot-agent.md.
#
# Orchestration only — no decisions beyond "is the engine up yet".
#   1. `orb start` first: compose re-asserts containers, never the engine
#      (2026-09-07 incident). Idempotent; a missing binary must not block.
#   2. Bounded docker wait: launchd will not start a second instance of a
#      running job, so an unbounded wait would swallow every later trigger.
#   3. `docker compose up -d` is idempotent — a no-op when everything runs.
#   4. The exit code is the real result so `launchctl print` shows failures
#      (the inline one-liners this replaces ended in an unconditional exit 0).

set -uo pipefail

dir="${1:-}"
if [ -z "$dir" ] || [ ! -d "$dir" ]; then
    echo "usage: $0 <compose-project-dir>" >&2
    exit 2
fi

attempts="${ENSURE_DOCKER_WAIT_ATTEMPTS:-60}"
pause="${ENSURE_DOCKER_WAIT_SECONDS:-5}"
stamp() { date -Iseconds; }

orb start >/dev/null 2>&1 || true

n=0
until docker info >/dev/null 2>&1; do
    n=$((n + 1))
    if [ "$n" -ge "$attempts" ]; then
        echo "$(stamp) $dir: docker not up after $((attempts * pause))s — giving up this cycle"
        exit 3
    fi
    sleep "$pause"
done

cd "$dir" || exit 2
if ! out=$(docker compose up -d 2>&1); then
    echo "$(stamp) $dir: compose up failed — next StartInterval retries"
    printf '%s\n' "$out"
    exit 1
fi
