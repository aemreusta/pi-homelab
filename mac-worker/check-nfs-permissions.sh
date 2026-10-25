#!/bin/bash
# check-nfs-permissions.sh — did the Full Disk Access grant actually take?
#
# Run this BEFORE and AFTER granting Full Disk Access to the NFS supervisor.
# It prints a PASS/FAIL per check and exits non-zero if the supervisor is still
# blind, so it is also usable from a cron or another script.
#
# WHY THIS EXISTS
# ---------------
# `io.tdarr.nfsmount` runs as a LaunchDaemon. LaunchDaemons do NOT inherit the
# Full Disk Access grant that a Terminal-launched process has, so the daemon
# cannot read /Volumes/homelab-* even though the mounts are healthy and your
# shell reads them fine. That asymmetry is the entire bug and it is why "it
# works when I test it by hand" is not evidence here — check 3 below exists
# specifically to make the asymmetry visible instead of confusing.
#
# Consequences while it is denied:
#   * every read probe returns read_denied -> the supervisor is blind
#   * the shared status cannot be written -> the Pi fires MacNfsStatusMissing
#   * (fixed 2026-09-26) it used to remount a healthy share on a timed-out
#     probe, producing the macOS "Server connections interrupted" dialog
#
# Fix, then re-run this: System Settings -> Privacy & Security ->
# Full Disk Access -> "+" -> Cmd-Shift-G -> /bin/bash -> enable the toggle ->
#   sudo launchctl kickstart -k system/io.tdarr.nfsmount
# then wait ~90s for the first supervise cycle.
#
# See docs/runbooks/053-2026-09-26-mac-nfs-supervisor-permissions.md

set -uo pipefail

STATUS_FILE="/var/run/homelab-nfs.json"
SHARED_STATUS="/Volumes/homelab-generated/mac-nfs-status.json"
PROBE_SRV="/Volumes/homelab-srv/.homelab-nfs-probe"
PROBE_GEN="/Volumes/homelab-generated/.homelab-nfs-probe"

fails=0
pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fails=$((fails + 1)); }
info() { printf '        %s\n' "$1"; }

echo "NFS supervisor permission check"
echo

# ── 1. Are the mounts even present? ─────────────────────────────────────────
# Checked first so a genuine mount problem is never misreported as a
# permission problem. `mount` output is unaffected by Full Disk Access.
echo "1. Mounts present"
for m in /Volumes/homelab-srv /Volumes/homelab-generated; do
    if /sbin/mount | grep -q " ${m} "; then
        pass "$m is mounted"
    else
        fail "$m is NOT mounted — fix the mount first, this is not a permissions issue"
    fi
done
echo

# ── 2. Can THIS shell read the probe files? ─────────────────────────────────
# NOT `timeout` — macOS does not ship it and "command not found" exits
# non-zero, which has twice produced a false "hung mount" diagnosis in this
# repo. perl's alarm is the portable bounded wait.
echo "2. Readable from this shell (a grant here proves nothing about the daemon)"
for p in "$PROBE_SRV" "$PROBE_GEN"; do
    if perl -e 'alarm 12; exec @ARGV' /bin/cat "$p" >/dev/null 2>&1; then
        pass "$(basename "$(dirname "$p")")/$(basename "$p") readable"
    else
        fail "$p unreadable even from a shell — mount or probe file problem"
    fi
done
echo

# ── 3. What does the DAEMON see? ────────────────────────────────────────────
# The actual question. If check 2 passed and this fails, Full Disk Access is
# missing — that is the whole diagnosis in one comparison.
echo "3. Daemon's own last verdict  <-- the one that matters"
if [ -r "$STATUS_FILE" ]; then
    /usr/bin/python3 - "$STATUS_FILE" <<'PY'
import json, sys, time

data = json.load(open(sys.argv[1]))
age = int(time.time()) - int(data.get("checked_at", 0))
print("        status written %ds ago" % age)
bad = 0
for name, export in sorted(data.get("exports", {}).items()):
    state, reason = export.get("state"), export.get("reason")
    ever = int(export.get("last_read_ok") or 0)
    if state == "healthy" and ever:
        print("  \033[32mPASS\033[0m  %-10s state=%s (has read successfully)" % (name, state))
    elif reason == "read_denied":
        print("  \033[31mFAIL\033[0m  %-10s read_denied — Full Disk Access NOT granted to the daemon" % name)
        bad += 1
    else:
        print("  \033[31mFAIL\033[0m  %-10s state=%s reason=%s" % (name, state, reason))
        bad += 1
sys.exit(min(bad, 120))
PY
    rc=$?
    [ "$rc" -eq 0 ] || fails=$((fails + rc))
else
    fail "$STATUS_FILE missing — the daemon has not completed a cycle"
    info "start it: sudo launchctl kickstart -k system/io.tdarr.nfsmount"
fi
echo

# ── 4. Does the status reach the Pi? ────────────────────────────────────────
# Written through /Volumes, so it fails with EPERM under the same missing
# grant. This is the file whose absence makes MacNfsStatusMissing fire.
echo "4. Shared status published (this is what the Pi reads)"
if [ -r "$SHARED_STATUS" ]; then
    age=$(( $(date +%s) - $(stat -f %m "$SHARED_STATUS" 2>/dev/null || echo 0) ))
    if [ "$age" -lt 900 ]; then
        pass "published, ${age}s old"
    else
        fail "published but STALE (${age}s old) — the daemon stopped updating it"
    fi
else
    fail "$SHARED_STATUS missing — Pi cannot see Mac NFS health (MacNfsStatusMissing fires)"
fi
echo

# ── 5. Is the daemon loaded at all? ─────────────────────────────────────────
echo "5. Daemon loaded"
if launchctl print system/io.tdarr.nfsmount >/dev/null 2>&1; then
    pass "io.tdarr.nfsmount is loaded"
else
    fail "io.tdarr.nfsmount is NOT loaded"
    info "load it: sudo launchctl bootstrap system /Library/LaunchDaemons/io.tdarr.nfsmount.plist"
fi
echo

# ── verdict ─────────────────────────────────────────────────────────────────
if [ "$fails" -eq 0 ]; then
    printf '\033[32mALL CHECKS PASSED\033[0m — the supervisor can see the shares.\n'
    exit 0
fi

printf '\033[31m%d CHECK(S) FAILED\033[0m\n\n' "$fails"
cat <<'EOF'
If checks 1 and 2 passed but 3 or 4 failed, the mounts are healthy and the
DAEMON is the thing that cannot see them — grant Full Disk Access:

  1. System Settings -> Privacy & Security -> Full Disk Access
  2. "+" -> press Cmd-Shift-G -> type /bin/bash -> Open
     (/bin/bash is the daemon's ProgramArguments[0]; granting the wrapper is
      what the running process actually needs)
  3. Make sure its toggle is ON
  4. sudo launchctl kickstart -k system/io.tdarr.nfsmount
  5. wait ~90s, then re-run this script

Runbook: docs/runbooks/053-2026-09-26-mac-nfs-supervisor-permissions.md
EOF
exit 1
