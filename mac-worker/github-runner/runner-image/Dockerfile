# Ephemeral GitHub Actions runner image (arm64) for the homelab Mac.
#
# Built once on the Mac and pushed to Zot; the supervisor `docker run`s it once
# per job. The actions/runner binary is PINNED via RUNNER_VERSION. JIT mode has
# no --disableupdate flag, so a runner whose image is behind self-updates at
# startup (into the throwaway container only). Rebuild + re-push periodically to
# keep those per-job self-updates rare — the GHRunnerImageStale alert is the
# reminder. See docs/design/039-mac-ci-runner.md "Runner image versioning".
#
#   docker buildx build --platform linux/arm64 \
#     --build-arg RUNNER_VERSION=<current> \
#     -t raspberrypi.local:5050/homelab/github-runner:$(date +%Y-%m-%d) --push .
#
# .NET 10 / Bun are NOT baked — workflows that need them install them per-job via
# setup-dotnet / setup-bun. This stays a generic runner image.
#
# Chromium IS baked: a served repo's UI automation suite drives the real
# UI through headless Chrome and SKIPS itself (a ::notice, not a failure) if no
# browser is on the runner. Google Chrome publishes no arm64 Linux build, so
# Chromium is the only option on this arch; chromium-driver provides a matching
# chromedriver so Selenium Manager need not fetch one over the network on arm64.

FROM debian:trixie-slim

# Bump to the current actions/runner release at each rebuild (the 30-day SLO).
ARG RUNNER_VERSION=2.336.0
ARG TARGETARCH=arm64

# Stamp build provenance as LABELS so the SUPERVISOR (a separate container) can
# read them with `docker image inspect` and surface image age + the pinned
# runner version as metrics (gh_runner_image_build_timestamp_seconds /
# gh_runner_version_info → GHRunnerImageStale, the 30-day SLO). BUILD_EPOCH is
# passed at build time:  --build-arg BUILD_EPOCH=$(date +%s)
ARG BUILD_EPOCH=0
ENV RUNNER_VERSION=${RUNNER_VERSION}
LABEL org.homelab.build-epoch="${BUILD_EPOCH}" \
      org.homelab.runner-version="${RUNNER_VERSION}"

# Toolchain: git/curl/jq + python3/uv + shellcheck + C/Make for Go race tests.
# Jobs run unprivileged: install OS packages here, never with sudo in a workflow.
# The Docker CLI + buildx plugin come from the official docker:cli image — the
# daemon itself is the Mac's, reached via the mounted socket. (Debian trixie's
# docker.io ships only docker-init, not the `docker` client or buildx.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git jq bash sudo unzip \
        python3 python3-venv build-essential \
        shellcheck \
        libicu76 liblttng-ust1 libkrb5-3 libssl3 zlib1g \
        chromium chromium-driver fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Selenium/the automation harness honors CHROME_BIN; Debian installs the binary
# as `chromium` (the workflow's browser probe also checks this name).
ENV CHROME_BIN=/usr/bin/chromium

# Headless Chromium can't initialize its sandbox in this container (Debian trixie
# restricts unprivileged user namespaces via AppArmor and OrbStack ships no SUID
# chrome-sandbox), so it exits with "No usable sandbox!" and every automation
# session fails SessionNotCreated. The fork's test harness deliberately does NOT
# pass --no-sandbox (a host-trust decision belongs to the runner, not the app), so
# inject it via Debian chromium's own flags hook: /usr/bin/chromium is a wrapper
# that sources /etc/chromium.d/* and chromedriver launches that wrapper, so the
# flag applies to every session WITHOUT editing fork test code. Safe here — the
# runner is an ephemeral, secret-free CI container (docs/design/039-mac-ci-runner.md boundary).
# (--disable-dev-shm-usage is already handled by the stock /etc/chromium.d/dev-shm
# hook when /dev/shm is small.)
RUN printf 'export CHROMIUM_FLAGS="$CHROMIUM_FLAGS --no-sandbox"\n' > /etc/chromium.d/00-homelab-ci

COPY --from=docker:28-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker:28-cli /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx

# uv (never pip) — used by jobs that need an isolated Python env.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Non-root runner user (GitHub's runner refuses to configure as root by default).
# Create the docker group (the docker.io pkg that used to provide it is gone) so
# the runner can be granted socket access; the supervisor also passes --group-add
# for the live socket GID at spawn time.
RUN groupadd -f docker && useradd -m -s /bin/bash runner && usermod -aG docker runner

# Toolchain installers (setup-dotnet, etc.) target system dirs that are
# writable on GitHub-hosted runners. setup-dotnet installs to /usr/share/dotnet
# and does NOT use sudo, so pre-create it owned by the non-root runner.
RUN mkdir -p /usr/share/dotnet && chown runner:runner /usr/share/dotnet

USER runner
WORKDIR /home/runner

COPY --chown=runner:runner install-tools.sh /home/runner/install-tools.sh
RUN bash /home/runner/install-tools.sh

COPY --chown=runner:runner entrypoint.sh /home/runner/entrypoint.sh
ENTRYPOINT ["/home/runner/entrypoint.sh"]
