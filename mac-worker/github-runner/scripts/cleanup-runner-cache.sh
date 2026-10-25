#!/usr/bin/env bash
# cleanup-runner-cache.sh — weekly disk hygiene for the CI build cache on the
# Mac. Conservative on purpose: it prunes dangling images and build cache older
# than the retention window, but NEVER touches running containers, named
# volumes, or tagged images (the Zot-pushed runner/supervisor images stay).
#
# Suggested: a weekly user cron / launchd timer on the Mac.
set -euo pipefail

RETENTION="${RETENTION:-168h}"  # 7 days

echo "==> Pruning dangling images (untagged) older than ${RETENTION}"
docker image prune -f --filter "until=${RETENTION}" --filter "dangling=true"

echo "==> Pruning build cache older than ${RETENTION}"
docker builder prune -f --filter "until=${RETENTION}"

echo "==> Disk usage after prune:"
docker system df

echo "==> Done."
