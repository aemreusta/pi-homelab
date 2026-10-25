#!/bin/bash
# One long-running PID supervised by io.tdarr.nfsmount. Install with the sibling
# install-nfs-mount.sh; recovery decisions and bounded probes are tested Python.
set -euo pipefail
exec /usr/bin/python3 /usr/local/libexec/homelab/nfs_mount.py "$@"
