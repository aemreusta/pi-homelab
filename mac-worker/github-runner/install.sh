#!/bin/bash
# install.sh — install + start the ephemeral GitHub Actions runner supervisor on
# the Mac. Run as the logged-in user (NOT root — this is a user LaunchAgent and
# needs the user's Docker):
#
#   bash mac-worker/github-runner/install.sh
#
# Hardened in the same shape as mac-worker/install-nfs-mount.sh:
#   1. set -euo pipefail — any failure aborts BEFORE we touch the running agent
#   2. Preconditions checked first (.env present + complete, Docker up, helper
#      scripts pass lint, registry reachable)
#   3. Install the plist before (re)bootstrapping, so a broken install leaves
#      the running agent untouched
#   4. Verify with a poll loop (the supervisor + first image pull take a moment)
#
# Re-runnable / idempotent.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_SRC="$REPO_DIR/io.homelab.github-runner.plist"
PLIST_DST="$HOME/Library/LaunchAgents/io.homelab.github-runner.plist"
AGENT_LABEL="gui/$(id -u)/io.homelab.github-runner"
# Biweekly runner-image rebuild agent (keeps the image under the 30-day cliff).
REBUILD_PLIST_SRC="$REPO_DIR/io.homelab.github-runner-rebuild.plist"
REBUILD_PLIST_DST="$HOME/Library/LaunchAgents/io.homelab.github-runner-rebuild.plist"
REBUILD_AGENT_LABEL="gui/$(id -u)/io.homelab.github-runner-rebuild"
ENV_FILE="$REPO_DIR/.env"
CONTAINER="homelab-github-runner-supervisor"

if [ "$EUID" -eq 0 ]; then
    echo "ERROR: do NOT run as root — this is a user LaunchAgent (bash $0)" >&2
    exit 1
fi

# ---------- 1. Preconditions -------------------------------------------------
echo "==> 1/6  Preconditions"

[ -f "$PLIST_SRC" ] || { echo "ERROR: missing $PLIST_SRC" >&2; exit 2; }
[ -f "$REBUILD_PLIST_SRC" ] || { echo "ERROR: missing $REBUILD_PLIST_SRC" >&2; exit 2; }

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: missing $ENV_FILE — copy .env.example to .env and fill it in" >&2
    exit 2
fi

# Required keys must be present AND not left at their CHANGE_ME placeholders.
missing=0
for key in GH_OWNER GH_REPOS GITHUB_PAT RUNNER_IMAGE; do
    val="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2-)"
    if [ -z "$val" ] || printf '%s' "$val" | grep -q "CHANGE_ME"; then
        echo "ERROR: $key is unset or still a CHANGE_ME placeholder in .env" >&2
        missing=1
    fi
done
[ "$missing" -eq 0 ] || exit 2

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker is not running — start Docker/OrbStack first" >&2
    exit 3
fi
echo "    docker: up"

# Shellcheck the helper scripts before we wire anything that runs them.
if command -v shellcheck >/dev/null 2>&1; then
    if ! shellcheck "$REPO_DIR"/scripts/*.sh "$0"; then
        echo "ERROR: shellcheck failed — refusing to install" >&2
        exit 3
    fi
    echo "    shellcheck: clean"
else
    echo "    shellcheck: not installed (skipping — brew install shellcheck)"
fi

# Registry reachability is a warning, not a hard fail (the supervisor pulls the
# runner image lazily; a transient blip shouldn't block install).
REGISTRY="$(grep -E '^RUNNER_IMAGE=' "$ENV_FILE" | head -1 | cut -d= -f2- | cut -d/ -f1)"
# Zot answers /v2/ with 401 (auth required) when it's up — that's "reachable".
# Only a connection failure (curl exit != 0, empty code) means it's actually down.
if [ -n "$REGISTRY" ]; then
    reg_code="$(curl -sS -m 5 -o /dev/null -w '%{http_code}' "http://$REGISTRY/v2/" 2>/dev/null || true)"
    case "$reg_code" in
        2??|401|403) : ;;  # up (answered)
        *) echo "    WARN: registry $REGISTRY not reachable (code='${reg_code:-none}') — build+push the runner image (039 step 3)" ;;
    esac
fi

# ---------- 2. Local dirs ----------------------------------------------------
echo "==> 2/6  Ensure logs dir"
mkdir -p "$REPO_DIR/logs"

# ---------- 3. Install the LaunchAgent plist ---------------------------------
echo "==> 3/6  Install LaunchAgent plists"
mkdir -p "$HOME/Library/LaunchAgents"
install -m 0644 "$PLIST_SRC" "$PLIST_DST"
install -m 0644 "$REBUILD_PLIST_SRC" "$REBUILD_PLIST_DST"

# ---------- 4. (Re)bootstrap the agents --------------------------------------
echo "==> 4/6  (Re)bootstrap agents (supervisor + biweekly rebuild)"
launchctl bootout "$AGENT_LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
launchctl bootout "$REBUILD_AGENT_LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$REBUILD_PLIST_DST"

# ---------- 5. Bring the supervisor up now -----------------------------------
echo "==> 5/6  docker compose up -d"
( cd "$REPO_DIR" && docker compose up -d )

# ---------- 6. Verify (poll up to 60s) ---------------------------------------
echo "==> 6/6  Verify (polling for supervisor, max 60s)"
deadline=$((SECONDS + 60))
state=""
while [ $SECONDS -lt $deadline ]; do
    state="$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || true)"
    [ "$state" = "running" ] && break
    sleep 3
done

echo
if [ "$state" = "running" ]; then
    echo "OK: $CONTAINER is running."
    echo "    Logs:   docker compose -f $REPO_DIR/compose.yml logs -f"
    echo "    Status: docker compose -f $REPO_DIR/compose.yml ps"
else
    echo "WARN: $CONTAINER not running yet (state: ${state:-none})."
    echo "      It may still be pulling the runner image. Check:"
    echo "      docker compose -f $REPO_DIR/compose.yml logs"
    exit 5
fi
