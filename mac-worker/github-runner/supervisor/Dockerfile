# GitHub Actions runner SUPERVISOR image (arm64).
#
# Long-running. Mints JIT configs (PAT from .env) and `docker run`s ephemeral
# runner containers against the Mac daemon (socket mounted by compose.yml). It
# does NOT run jobs. See docs/design/039-mac-ci-runner.md.
#
#   docker buildx build --platform linux/arm64 \
#     -t raspberrypi.local:5050/homelab/github-runner-supervisor:latest --push .

FROM debian:trixie-slim

# python3 + uv for the supervisor; the docker CLI to spawn/reap runner
# containers. NOTE: Debian trixie's docker.io ships only docker-init, not the
# `docker` client — copy the static CLI from the official docker:cli image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY --from=docker:28-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# uv, never pip. Debian's system Python is PEP-668 externally-managed, so we
# install into a dedicated venv and put it first on PATH.
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
RUN uv venv "$VIRTUAL_ENV"

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN uv pip install --requirement /app/requirements.txt

COPY supervisor.py /app/supervisor.py

ENTRYPOINT ["python3", "/app/supervisor.py"]
