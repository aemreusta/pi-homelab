# NFS health check silently calls a missing binary — 2026-09-24

- **Symptom:** Mac `/Volumes/homelab-srv` reported `not responding`; the Mac's transcode node logged repeated file-open timeouts from 03:56 local, while the NFS daemon stayed running and emitted no remount attempt.
- **Confirmed cause:** the installed and repository daemon call `/usr/sbin/nfsstat`, absent on this Mac; the binary is `/usr/bin/nfsstat`. Exit 127 is discarded and interpreted as “not hung.” This explains failed recovery. At 09:09, re-exporting with `exportfs -ra` restored both clients without a client remount, supporting stale server export state.
- **Evidence:** a failing media path read 4 KiB successfully on the Pi, returned `ETIMEDOUT` over native Mac NFS, and exceeded 12 seconds through the Docker worker's separate NFS mount. Port 2049 and directory stat passed.
- **Disposition:** initial read-only audit followed by Ansible-mediated `exportfs -ra`; the same 4 KiB SHA-256 then matched on Pi, native Mac and Docker. New read-check code is committed; native activation awaits macOS authentication. Full workload completion and a controlled lifecycle test remain required.
- **Prevention:** validate executable availability, distinguish probe failure from healthy, test hung-but-mounted and missing-helper cases, and emit per-export health/recovery metrics.
