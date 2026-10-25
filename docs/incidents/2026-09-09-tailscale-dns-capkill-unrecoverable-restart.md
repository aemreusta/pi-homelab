# 2026-09-09 — a stripped init could not signal its child, so a routine restart left tailscale-dns dead for 30 h

- **What happened:** `tailscale-dns` sat `Exited (1)` for ~30 h with `RestartCount=0`, so
  `restart: unless-stopped` never brought it back. `unhealthy-watchdog` had caught it, tried to
  restart it, logged `RESTART FAILED`, and eventually garbage-collected its own state as
  *"container absent"* — the watchdog stopped tracking the thing it failed to fix. Found while
  checking the fleet before a deploy, not by an alert.
- **Why:** two independent faults stacked, and only the second was ours.
  **(1) Tailscale was logged out** — `invalid key: API key does not exist`, i.e. the auth key the
  node was registered with had been revoked or expired. `tailscale-dns` forwards through the
  tailnet, so its healthcheck started failing.
  **(2) The container could not be restarted.** The compose sets `init: true`, so Docker injects
  tini as PID 1; dnsmasq drops privileges after start; tini runs as root but `cap_drop: ALL` with
  no `KILL` in `cap_add`, and signalling a process you no longer share a UID with needs
  `CAP_KILL`. Every restart therefore logged
  `[FATAL tini (1)] Unexpected error when forwarding signal: 'Operation not permitted'`, SIGTERM
  never reached dnsmasq, the stop was unclean, and the container landed in `Exited (1)` with
  `RestartCount=0` — the one state `unless-stopped` will not recover. Reproduced on demand with
  `docker restart` before the fix and confirmed gone after it.
- **Class:** *hardening that strips a capability the injected init needs*, 3rd instance
  ([2026-06-07: `init: true` vs s6-overlay, and `cap_drop: ALL` vs `DAC_OVERRIDE`](
  2026-06-07-container-hardening-sweep-init-and-capdrop-bricks.md)). The property that makes this
  class expensive is that **the damage only appears on RESTART, never at first start** — a green
  deploy proves nothing about it, and the container can run for weeks before the first restart
  exposes it. Neighbouring shape: *the self-healer gave up and erased its own evidence.* The
  watchdog's `RESTART FAILED` was the highest-value line in the whole incident and nothing alerted
  on it; the later `GC (state file orphaned — container absent)` then made the service invisible
  to the very thing watching it.
- **Occurrence:** 1st of the watchdog-gives-up shape. 3rd of the hardening-vs-init class. The
  Tailscale logout is its own 1st (previous tailnet incidents were routing/DNS, not auth).
- **Structural fix:** `KILL` added to `tailscale_dns_cap_add` with the reasoning inline; verified
  in place — `docker restart` now produces zero `FATAL tini` lines and the container returns
  `healthy`. Tailscale was re-authenticated by the operator with the full flag set
  (`--accept-routes --advertise-routes=192.0.2.0/24 --hostname=homelab`; a bare `tailscale up`
  is refused and would have dropped the subnet-router advertisement), and key expiry is now
  disabled on the node (`KeyExpiry: None`), which removes the recurrence. **Deferred:** nothing
  alerts on `RESTART FAILED` in `unhealthy-watchdog`, and no other container has been audited for
  the same `init: true` + `cap_drop` shape — both still open. Note also that a `--check --diff`
  gate cannot catch this class: `command` tasks are skipped in check mode, which is why
  `traefik`'s deep check passed cleanly while `tailscale cert` was in fact failing.
