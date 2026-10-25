#!/bin/bash
# rebuild-runner-image.sh — rebuild + push the ephemeral GitHub runner image and
# point the supervisor at it, resetting the image-age SLO (GHRunnerImageStale).
#
# Runs on the Mac via the io.homelab.github-runner-rebuild LaunchAgent (~biweekly)
# so the runner image never drifts toward GitHub's 30-day --disableupdate cliff
# by hand. Safe to run manually too:  bash scripts/rebuild-runner-image.sh
#
# Observability (repo cron rule): writes rebuild-status.json to the NFS
# generated/gh-runner dir; the Pi emitter turns it into
# gh_runner_rebuild_* metrics and the GHRunnerRebuild{Failed,Stale} vmalert
# rules watch them — so a rebuild that RUNS but fails is caught before the
# image-age cliff. Design 039.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env"
IMAGE_REPO="raspberrypi.local:5050/homelab/github-runner"
NOW="$(date +%s)"

# Where the status JSON lands, and HOW we get it there.
#
# It has to reach the Pi's shared Mac-worker tree, because that is where the
# Pi-side gh-runner-metrics.sh reads it from. There are two ways to that tree
# from this Mac, and only one of them works:
#
#   * /Volumes/homelab-generated  — macOS-native NFS mount. Writing here from a
#     LAUNCHD job fails with EPERM. Interactive shells succeed, which is what
#     made this so slow to spot.
#   * the `nfs-generated` Docker volume — mounted inside the OrbStack Linux VM.
#     The supervisor container has written heartbeat/status.json through it
#     every minute for months without a single failure.
#
# We used to use the first. The rebuild succeeded nightly and the status write
# failed silently every time, so `rebuild-status.json` froze at 2026-09-03 and
# GHRunnerRebuildStale spent 20 days reporting "no successful runner-image
# rebuild" about an image that was being rebuilt correctly every night. A
# monitoring path that fails independently of the thing it monitors will
# eventually lie, and this one lied for three weeks.
#
# This is the same lesson the other worker containers already learned
# (runbook 044): on this Mac, go through a Docker NFS volume, not /Volumes.
STATUS_VOLUME="${GH_RUNNER_STATUS_VOLUME:-homelab-github-runner_nfs-generated}"
STATUS_SUBDIR="gh-runner"
# Escape hatch for tests: write to a plain directory instead of the volume.
STATUS_DIR="${GH_RUNNER_GENERATED_DIR:-}"

log() { echo "[$(date '+%F %T')] $*"; }

# Read/write the status file through whichever transport is configured.
status_read() {
    if [ -n "$STATUS_DIR" ]; then
        cat "$STATUS_DIR/rebuild-status.json" 2>/dev/null
    else
        docker run --rm -v "$STATUS_VOLUME:/generated" alpine:3 \
            cat "/generated/$STATUS_SUBDIR/rebuild-status.json" 2>/dev/null
    fi
}

status_write() {  # reads JSON on stdin
    if [ -n "$STATUS_DIR" ]; then
        mkdir -p "$STATUS_DIR" 2>/dev/null || return 1
        cat > "$STATUS_DIR/rebuild-status.json"
    else
        docker run --rm -i -v "$STATUS_VOLUME:/generated" alpine:3 sh -c \
            "mkdir -p /generated/$STATUS_SUBDIR && cat > /generated/$STATUS_SUBDIR/.rebuild-status.tmp && mv /generated/$STATUS_SUBDIR/.rebuild-status.tmp /generated/$STATUS_SUBDIR/rebuild-status.json"
    fi
}

prev_success="$(status_read | python3 -c 'import json,sys;print(json.load(sys.stdin).get("last_success_ts",0))' 2>/dev/null || echo 0)"
case "$prev_success" in ''|*[!0-9]*) prev_success=0 ;; esac

write_status() {  # exit_code  success_ts  version  tag
    if printf '{"last_attempt_ts": %s, "last_success_ts": %s, "last_exit_code": %s, "runner_version": "%s", "image_tag": "%s"}\n' \
        "$NOW" "$2" "$1" "$3" "$4" | status_write; then
        return 0
    fi
    # LOUD on failure. The whole point of this rewrite is that a status write
    # which fails quietly turns the alert into a 20-day lie; if we cannot
    # record the outcome, say so where a human and the log will both see it.
    log "ERROR: could not write rebuild status — the gh_runner_rebuild_* metrics"
    log "ERROR: are now STALE and GHRunnerRebuildStale will fire on a healthy image."
    return 1
}

fail() {
    log "REBUILD FAILED: $*"
    write_status 1 "$prev_success" "${RUNNER_VERSION:-unknown}" ""
    exit 1
}

docker info >/dev/null 2>&1 || fail "docker not running"
[ -f "$ENV_FILE" ] || fail "missing $ENV_FILE"

# ── Age guard ───────────────────────────────────────────────────────────
# The LaunchAgent used to fire twice a month (Day 1 + Day 15, 04:00). On a
# laptop that is a bad trigger: it missed BOTH 2026-08-15 and 2026-09-01,
# and the image aged to 33 days — past GitHub's 30-day --disableupdate
# cliff, at which point jobs stop being queued to the runner at all. Two
# missed triggers is all it takes, because the schedule has no slack.
#
# So the trigger is now DAILY and this guard supplies the cadence instead:
# many cheap chances to catch the Mac awake, but an actual ~1 GB build+push
# only once the image is genuinely old. Keep MIN_AGE_DAYS comfortably below
# both the 21d GHRunnerImageStale warn and the 16d GHRunnerRebuildStale
# warn, so a normal cadence never trips either.
MIN_AGE_DAYS="${REBUILD_MIN_AGE_DAYS:-14}"
if [ "${FORCE:-0}" != "1" ] && [ "$prev_success" -gt 0 ] 2>/dev/null; then
    age_days=$(( (NOW - prev_success) / 86400 ))
    if [ "$age_days" -lt "$MIN_AGE_DAYS" ]; then
        log "SKIP: last successful rebuild was ${age_days}d ago (< ${MIN_AGE_DAYS}d). FORCE=1 to override."
        # Record the attempt (proves the automation is alive) but carry the
        # previous success forward — a skip is not a rebuild, and must not
        # reset the GHRunnerRebuildStale clock.
        write_status 0 "$prev_success" "${RUNNER_VERSION:-unknown}" ""
        exit 0
    fi
fi

# Resolve the latest actions/runner release (gh CLI, then plain REST fallback).
RUNNER_VERSION="$(gh api repos/actions/runner/releases/latest --jq '.tag_name' 2>/dev/null | sed 's/^v//')"
if [ -z "$RUNNER_VERSION" ]; then
    RUNNER_VERSION="$(curl -fsS -m 10 https://api.github.com/repos/actions/runner/releases/latest \
        | python3 -c 'import json,sys;print(json.load(sys.stdin)["tag_name"].lstrip("v"))' 2>/dev/null || true)"
fi
[ -n "$RUNNER_VERSION" ] || fail "could not resolve latest actions/runner version"

TAG="$(date +%F)"
log "building $IMAGE_REPO:$TAG (runner $RUNNER_VERSION, epoch $NOW)"
if ! ( cd "$REPO_DIR/runner-image" && docker buildx build --platform linux/arm64 \
        --build-arg RUNNER_VERSION="$RUNNER_VERSION" --build-arg BUILD_EPOCH="$NOW" \
        -t "$IMAGE_REPO:$TAG" --push . ); then
    fail "buildx build/push failed (check Zot login + registry reachability)"
fi

# Point the supervisor at the fresh tag and recreate it so it re-reads the
# build-epoch label (compose restart does NOT re-read .env).
if grep -qE '^RUNNER_IMAGE=' "$ENV_FILE"; then
    sed -i '' "s#^RUNNER_IMAGE=.*#RUNNER_IMAGE=$IMAGE_REPO:$TAG#" "$ENV_FILE"
else
    echo "RUNNER_IMAGE=$IMAGE_REPO:$TAG" >> "$ENV_FILE"
fi
( cd "$REPO_DIR" && docker compose up -d ) || fail "compose up -d failed (image pushed but supervisor not recreated)"

log "REBUILD OK: $IMAGE_REPO:$TAG runner=$RUNNER_VERSION"
write_status 0 "$NOW" "$RUNNER_VERSION" "$TAG"
