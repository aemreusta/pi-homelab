#!/bin/bash
# Install only after the Pi's nfs_server role provisions its read sentinels.
# Healthy mounts survive the upgrade. Only the daemon recovers confirmed hangs.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
WRAPPER_DST=/usr/local/bin/tdarr-nfs-mount.sh
HELPER_DST=/usr/local/libexec/homelab/nfs_mount.py
PLIST_DST=/Library/LaunchDaemons/io.tdarr.nfsmount.plist
DAEMON_LABEL=system/io.tdarr.nfsmount

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: run sudo bash $0" >&2
    exit 1
fi

for src in tdarr-nfs-mount.sh nfs_mount.py io.tdarr.nfsmount.plist; do
    [ -f "$REPO_DIR/$src" ] || { echo "ERROR: missing $src" >&2; exit 2; }
done
for bin in /usr/bin/python3 /usr/bin/nfsstat /sbin/mount /sbin/mount_nfs /sbin/umount /usr/bin/nc /usr/sbin/diskutil /bin/launchctl /bin/mkdir; do
    [ -x "$bin" ] || { echo "ERROR: missing executable $bin" >&2; exit 2; }
done
/bin/bash -n "$REPO_DIR/tdarr-nfs-mount.sh"
/usr/bin/python3 -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' "$REPO_DIR/nfs_mount.py"
/usr/bin/plutil -lint "$REPO_DIR/io.tdarr.nfsmount.plist"
if command -v shellcheck >/dev/null 2>&1; then
    shellcheck "$REPO_DIR/tdarr-nfs-mount.sh" "$REPO_DIR/install-nfs-mount.sh"
fi

backup="$(mktemp -d /private/tmp/homelab-nfs-install.XXXXXX)"
trap 'rm -rf "$backup"' EXIT
was_loaded=0
launchctl print "$DAEMON_LABEL" >/dev/null 2>&1 && was_loaded=1
for dst in "$WRAPPER_DST" "$HELPER_DST" "$PLIST_DST"; do
    [ ! -f "$dst" ] || cp -p "$dst" "$backup/$(basename "$dst")"
done

rollback() {
    echo "Activation failed; restoring previous daemon files." >&2
    launchctl bootout "$DAEMON_LABEL" 2>/dev/null || true
    for dst in "$WRAPPER_DST" "$HELPER_DST" "$PLIST_DST"; do
        if [ -f "$backup/$(basename "$dst")" ]; then
            cp -p "$backup/$(basename "$dst")" "$dst"
        else
            rm -f "$dst"
        fi
    done
    if [ "$was_loaded" -eq 1 ]; then
        launchctl bootstrap system "$PLIST_DST" || echo "ERROR: previous daemon could not restart" >&2
    fi
}

# Complete validation and staging before stopping the old job. Never pkill by
# substring: it can match the installer itself and unrelated shell commands.
install -d -m 0755 -o root -g wheel /usr/local/bin /usr/local/libexec/homelab
if ! install -m 0755 -o root -g wheel "$REPO_DIR/tdarr-nfs-mount.sh" "$WRAPPER_DST" ||
   ! install -m 0644 -o root -g wheel "$REPO_DIR/nfs_mount.py" "$HELPER_DST" ||
   ! install -m 0644 -o root -g wheel "$REPO_DIR/io.tdarr.nfsmount.plist" "$PLIST_DST"; then
    rollback
    exit 3
fi
if [ "$was_loaded" -eq 1 ] && ! launchctl bootout "$DAEMON_LABEL"; then
    rollback
    exit 4
fi
if ! launchctl bootstrap system "$PLIST_DST"; then
    rollback
    exit 4
fi

echo "Verifying BOTH shares with bounded file reads (up to 120s plus one check)..."
deadline=$((SECONDS + 120))
verified=0
while [ "$SECONDS" -lt "$deadline" ]; do
    if /usr/bin/python3 "$HELPER_DST" --check > "$backup/check.json"; then
        verified=1
        break
    fi
    sleep 3
done
cat "$backup/check.json"
if [ "$verified" -ne 1 ]; then
    echo "ERROR: NFS verification failed; daemon remains active to retry. See /var/run/homelab-nfs.json and /var/log/tdarr-nfs-mount.log." >&2
    exit 5
fi
echo "OK: both native exports passed bounded reads. Docker volumes need separate verification."
