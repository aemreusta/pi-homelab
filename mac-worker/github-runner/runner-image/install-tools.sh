#!/usr/bin/env bash
# install-tools.sh — fetch + unpack the PINNED actions/runner tarball at image
# build time. Version comes from the RUNNER_VERSION build arg (see Dockerfile).
# Pinning here (not auto-update at runtime) is the whole point — see 039.
set -euo pipefail

: "${RUNNER_VERSION:?RUNNER_VERSION must be set (Dockerfile ARG)}"

# Map Docker's TARGETARCH to the runner's arch token. This image is arm64.
arch="arm64"
url="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${arch}-${RUNNER_VERSION}.tar.gz"

echo "==> Fetching actions-runner ${RUNNER_VERSION} (${arch})"
curl -fsSL -o /tmp/runner.tar.gz "$url"
tar xzf /tmp/runner.tar.gz -C /home/runner
rm -f /tmp/runner.tar.gz

# The runner ships a dependency installer; deps are already in the image, but
# run it so any missing shared lib surfaces at build time, not first job.
if [ -x /home/runner/bin/installdependencies.sh ]; then
    sudo /home/runner/bin/installdependencies.sh || true
fi

echo "==> actions-runner ${RUNNER_VERSION} installed"
