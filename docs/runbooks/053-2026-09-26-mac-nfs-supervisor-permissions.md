# Runbook 053 — the Mac NFS supervisor, Full Disk Access, and split-DNS drift

**Applies when:** `MacNfsStatusMissing` or `MacNfsReadFailing` fires, macOS shows
"Server connections interrupted: homelab-srv", or `TailscaleSplitDnsDrift` fires.

Both of these are **control-plane state Ansible cannot reach**. They need a human once,
and then they stay fixed. This runbook exists so that "once" is five minutes, not an
afternoon of re-diagnosis.

## First: is the mount actually broken?

Almost always, no. Check before doing anything:

```bash
mount | grep homelab                                  # both exports listed?
perl -e 'alarm 12; exec @ARGV' ls /Volumes/homelab-srv   # NOT `timeout` — macOS has none
log show --predicate 'process == "kernel"' --last 24h | grep -ci "nfs server.*not responding"
```

A count of **0** from that last command means the kernel never lost the server. If the
mounts list and read fine while the supervisor reports failure, **the supervisor is the
thing that is broken**, and nothing should be unmounted.

> **Do not use `timeout` on macOS.** It does not ship it, `command not found` exits
> non-zero, and every check that uses it reports a false failure. This has produced a
> wrong diagnosis twice — the deploy deep-check and again on 2026-09-24.

## `MacNfsStatusMissing` — the supervisor is blind

`io.tdarr.nfsmount` runs as a **LaunchDaemon**, and LaunchDaemons do not inherit the Full
Disk Access grant that a Terminal-launched process has. Symptoms:

```bash
cat /var/run/homelab-nfs.json          # state=unknown reason=read_denied, last_read_ok: 0
tail /var/log/tdarr-nfs-mount.log      # PermissionError ... /Volumes/homelab-generated/...
```

The tell is that the *same* probe succeeds from your shell:

```bash
/usr/bin/python3 /usr/local/libexec/homelab/nfs_mount.py \
  --read-probe /Volumes/homelab-srv/.homelab-nfs-probe ; echo "rc=$?"   # rc=0
```

**Verify first, and again afterwards** — one command tells you whether the grant took:

```bash
./mac-worker/check-nfs-permissions.sh
```

It checks five things and exits non-zero while the supervisor is blind. The diagnosis is
the *comparison* between checks 2 and 3: if this shell can read the probe files but the
daemon reports `read_denied`, the mounts are fine and Full Disk Access is the problem.
That asymmetry is why "it works when I test it by hand" is not evidence here.

**Fix (GUI, once):** System Settings → Privacy & Security → **Full Disk Access** → `+` →
press `⌘⇧G`, enter `/bin/bash`, add it, ensure the toggle is on. Then:

```bash
sudo launchctl kickstart -k system/io.tdarr.nfsmount
sleep 90
./mac-worker/check-nfs-permissions.sh          # expect ALL CHECKS PASSED
```

The daemon's `ProgramArguments` is `/bin/bash /usr/local/bin/tdarr-nfs-mount.sh`, which is
why `/bin/bash` is the binary that needs the grant. If you prefer not to grant it to a
shell, change the plist to exec `/usr/bin/python3` directly and grant that instead.

Until this is granted the supervisor stays blind — **but it is no longer dangerous**. Since
2026-09-26 `decide()` refuses to recover any share it has never successfully read, because
a blind supervisor cannot tell "hung" from "not allowed to look". Before that guard it
remounted a healthy share and produced the operator-visible "Server connections
interrupted" dialog itself. See
[the incident](../incidents/2026-09-26-nfs-supervisor-remounted-a-healthy-share-it-could-not-see.md).

## `TailscaleSplitDnsDrift` — MagicDNS has no route for the internal zone

`tailscale dns status` shows no route for `example.internal`. Measured 2026-09-27 it was
not empty but *wrong* — it still carried the retired single-label zone:

```text
Split DNS Routes:
  - homelab    -> 100.64.0.10     # STALE: dead since the 2026-07-15 .internal cut
  - ts.net.    -> ...              # Tailscale's own MagicDNS, leave alone
                                   # example.internal: MISSING
```

So the fix is a **delete and an add**, not just an add. Effect while missing: every client
resolving through tailscaled — **which is every Docker container**, via
`127.0.0.11 → 100.100.100.100` — gets NXDOMAIN for `*.example.internal`. It looks
intermittent because Docker caches hits.

Get the exact desired state from the box, never hand-write it:

```bash
ssh pi@raspberrypi.local 'sudo /usr/local/bin/tailscale-split-dns-check.sh -v'
```

It prints the drift and the PATCH body, e.g.:

```json
{"example.internal":["100.64.0.10"],"example":null}
```

`null` deletes a stale zone — `example` is the dead single-label domain retired in the
2026-07-15 clean cut and must not be re-added.

**Fix — either route:**

- **Admin console:** <https://login.tailscale.com/admin/dns> — note this is the **DNS**
  item in the left sidebar under *Network*, not *Machines*. Then: delete the existing
  `example` nameserver entry, and *Add nameserver* → *Custom* → `100.64.0.10` → enable
  **Restrict to domain** → `example.internal`. Do not touch the `ts.net.` rows.
- **API:** with a Tailscale API key that has `dns:write`,

  ```bash
  curl -X PATCH https://api.tailscale.com/api/v2/tailnet/-/dns/split-dns \
    -H "Authorization: Bearer $TS_API_KEY" -H 'Content-Type: application/json' \
    -d '{"example.internal":["100.64.0.10"],"example":null}'
  ```

Verify:

```bash
ssh pi@raspberrypi.local 'tailscale dns status | grep -A3 "Split DNS"; \
  curl -s localhost:9100/metrics | grep tailscale_split_dns_ok'   # expect 1
```

`100.64.0.10` is the Pi's tailnet IP. If the Pi is ever re-authenticated onto a new
tailnet node, that address changes and this drifts again — which is what the hourly check
exists to catch.

## Related

- [Incident 2026-09-26](../incidents/2026-09-26-nfs-supervisor-remounted-a-healthy-share-it-could-not-see.md)
