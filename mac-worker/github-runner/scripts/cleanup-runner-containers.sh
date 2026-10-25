#!/usr/bin/env bash
# cleanup-runner-containers.sh — safety-net reaper for ephemeral runner
# containers. The supervisor already reaps exited containers each tick; this is
# a manual/cron backstop for the case where the supervisor was down when jobs
# finished. Removes ONLY containers carrying our gh-runner-job label.
set -euo pipefail

JOB_LABEL="gh-runner-job"

echo "==> Exited gh-runner-job containers:"
mapfile -t exited < <(docker ps -a --filter "label=${JOB_LABEL}" --filter status=exited --format '{{.ID}}')
if [ "${#exited[@]}" -eq 0 ]; then
    echo "    none"
else
    for cid in "${exited[@]}"; do
        code="$(docker inspect -f '{{.State.ExitCode}}' "$cid" 2>/dev/null || echo '?')"
        echo "    removing $cid (exit=$code)"
        docker rm -f "$cid" >/dev/null
    done
fi

echo "==> Done."
