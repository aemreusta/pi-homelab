# 040 — External dead-man's-switch: activation (PENDING operator step)

**Status:** the heartbeat is **built and deployed but inert** — it no-ops until
`vault_healthchecks_ping_url` is set. This runbook is the checklist to finish it.

## Why this exists

Every on-box watcher — vmalert, VictoriaMetrics, ntfy, ntfy-bridge, Uptime Kuma —
runs on the same Pi. A power loss, SSD/SD failure, kernel panic, or dead NIC takes
out **the entire observability stack and every alert path at once**: total silent
blackout, zero notifications. You'd find out next time you open an app.

The dead-man's-switch is the only signal that survives the Pi itself dying: a cron
on the Pi beats an **off-box** service (healthchecks.io); if the beat stops, that
service emails/pushes you. It also sidesteps the ntfy single-point-of-failure —
the beat does not route through ntfy.

## What's already in place (no action needed)

- `ansible/roles/common/templates/deadman-heartbeat.sh.j2` → `/usr/local/bin/deadman-heartbeat.sh`, cron every 5 min (`common` role, `monitoring` tag).
- **Composite-liveness** (not a dumb ping): it only beats "ok" if BOTH invariants hold — (a) it can write to disk, and (b) VictoriaMetrics answers a local query. Deliberately just those two: catches the blackout class without re-implementing vmalert (a flaky third check would stop the beat and erode trust).
- Behaviour: invariants hold → ping the bare URL; a check fails but cron runs → ping `<URL>/fail` (fast page); Pi/cron/NIC dead → no ping → healthchecks.io grace timeout pages.
- Emits `deadman_heartbeat_*` textfile metrics; `DeadmanHeartbeatInvariantsFailing` vmalert gives on-box visibility for the disk-fail case.
- Placeholder in `vault.yml.example`; the script no-ops while `vault_healthchecks_ping_url` is empty (safe to have deployed now).

## Activation steps (the part still to do)

1. **Create the external check.** Sign up at <https://healthchecks.io> (free tier:
   email + app push, generous limits, no VPS to run). Create one check:
   - Name: `homelab-pi-deadman`
   - Period: **5 minutes** · Grace: **~15 minutes** (tolerates a missed beat / brief reboot without false-paging).
   - Copy its **ping URL** (looks like `https://hc-ping.com/<uuid>`).
2. **Add a notification channel** in healthchecks.io (email and/or the mobile app) so a stopped beat actually reaches you.
3. **Store the URL in vault:**

   ```bash
   ansible-vault edit ansible/inventory/group_vars/all/vault.yml
   # add:  vault_healthchecks_ping_url: "https://hc-ping.com/<uuid>"
   ```

4. **Deploy** (the script reads the new value):

   ```bash
   ansible-playbook ansible/playbooks/base.yml --tags monitoring -e ansible_host=100.64.0.10
   ```

5. **Verify:**
   - Run it once: `ssh pi@raspberrypi.local 'sudo /usr/local/bin/deadman-heartbeat.sh'`.
   - healthchecks.io should flip the check to **up** within a minute.
   - Confirm the metric: `grep deadman /var/lib/node_exporter/textfile/deadman_heartbeat.prom` → `configured 1`, `invariants_ok 1`.
   - Optional negative test: pause VictoriaMetrics briefly → the next beat pings `/fail` → healthchecks.io shows the failure.

## Future hardening (optional)

- A second healthchecks.io check fed only by an off-Pi device (phone shortcut / a
  friend's box) pinging *the Pi* would also catch "healthchecks.io itself is the
  thing that's wrong" — diminishing returns; the single check covers the blackout
  class.
- The composite gate is intentionally two checks. Resist adding more — the on-box
  alerts (vmalert) already cover service-level conditions; this switch is only for
  "is the box on and is the stack answering."
