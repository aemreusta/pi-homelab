# Incident log

Lightweight, append-only log of **operational incidents** — the deploy-time
friction that costs real minutes but isn't a code bug with a runbook. The point
is **class detection**: spotting that a fresh failure is the Nth instance of a
shape we've already paid for, so the structural fix happens on incident #2
instead of #4.

This complements, it does not replace:

- **runbooks** (`docs/runbooks/`) — how to operate/recover a specific service,
- **`docs/operations/ops-discipline.md`** — the cross-cutting rules these
  incidents distilled into,
- **the private repo's agent rules** — the load-bearing ones earned through
  repetition (silent-cron, cron-diff-logic-in-a-tested-module).

## When to write an entry (the trigger)

Write a five-line entry **before sprint/deploy close-out** whenever a deploy or
operation produces any of:

- **≥30 min lost** to friction (stuck process, throttling, retries),
- a **manual destructive recovery** (`pkill -9`, `rm` of state/sockets, forced
  restart loop),
- a **runbook deviation** (you had to do something the runbook didn't cover).

If you're unsure whether it qualifies, it does — entries are cheap.

This trigger is also recorded in the private repo's agent working rules so it
survives operator turnover.

## Cadence

At sprint/deploy close-out, confirm any qualifying incident from that window has
an entry here. A close-out report that names an incident in prose but leaves no
entry here is incomplete.

## Entry format

One file per incident: `YYYY-MM-DD-short-slug.md`. Five fields:

```markdown
# YYYY-MM-DD — <title>

- **What happened:** one or two sentences.
- **Why:** the mechanism (not "it broke" — the actual cause).
- **Class:** the generic bug/friction class this belongs to.
- **Occurrence:** 1st / Nth — link the prior instances.
- **Structural fix:** what stops the class (shipped or deferred + where tracked).
```

## Known recurring classes

| Class | Instances | Structural fix |
| --- | --- | --- |
| Valid-YAML / invalid-to-consumer monitoring config → crash-loop (quote/escape collision) | 4× (vmalert: `368e10a`, `86d1c25`, + 1 caught **pre-merge** 2026-05-29; Alertmanager: [folded inhibit matcher 06-07](2026-06-07-alertmanager-crash-loop-folded-matcher.md)) | Per-consumer gate: `tests/test_vmalert_rules.py` (vmalert) + [quoting convention](../conventions/vmalert-rule-quoting.md); Alertmanager now gated by `amtool check-config` in deploy + `tests/test_alertmanager_config.py`. `--syntax-check` can't see these — only the consuming program's parser can. |
| Silent-cron (cron exits 0 having done nothing) | 5× (incl. the Tdarr schedule drift and qbit-cleanup `deleted=0`) | Hard rule #1 of the repo's agent rules — every cron emits a textfile counter. **4th showed the counter is necessary but not sufficient: a `*_last_status` gauge with no vmalert rule is still silent.** 5th was a level deeper — the *metric itself was missing* and a daily maintenance restart silently disabled a scheduled task; fix added a last-execution gauge + staleness rules + a self-documenting CAUTION in the restart SQL. |
| Cron-driven diff logic buried in bash/Jinja2 | 5× (incl. qbit-cleanup, Tdarr drift, watcher spam) | Hard rule #2 of the repo's agent rules — diff logic in a tested module. The 5th shipped data loss. |
| Review verified a claim against a decoy / non-authoritative source | 2× (2026-05-31: read an empty `>>` redirect log instead of the authoritative event log; mistook one byparr snapshot for steady state. 2026-09-04: "0 bulk edits through traefik in the window" read as an absence proof, when traefik logged **zero** requests of any kind to that app in the window — its UI is LAN-open on its own port and bypasses traefik. A vacuous zero: the denominator was zero) | [ops-discipline §4](../operations/ops-discipline.md) verify-before-build — confirm against the *authoritative* source; don't refute a bursty symptom with one sample. |
| The monitor is the blind spot — a watcher whose own failure nothing can observe | 2× ([watchdog logged `RESTART FAILED`, then GC'd its own state 09-09](2026-09-09-tailscale-dns-capkill-unrecoverable-restart.md); **[beszel recorded no container stats for 10 days 09-24](2026-09-24-beszel-container-stats-blackout-ten-days-unseen.md)** — not scraped, no rule, no Prometheus endpoint, agent logging off) | Deferred — `BZ-1`: the watcher emits its own liveness counter to the textfile collector plus a vmalert rule. Same rule as silent-cron, one level up: **a monitor with no counter is a cron with no counter**. Both instances were found by a human looking at a screen, not by an alert. |
| SSH connection storm vs `MaxStartups` (stale sockets / slow fact-gather) | 2× ([2026-05-29 ×2: Tdarr-dedup deploy, follow-up deploy](2026-05-29-tdarr-deploy-incident-tail.md)) | `make cp-clean` time-prune (now). Class-level: `gather_facts: false` on fact-free playbooks + `ssh -O check` wedge-detection (both deferred to N+1). |
| Multi-role file-ownership "mode war" | 3× (`alert-audit.log`; `/opt/homelab/control` chowned root by a second role → six watcher state files froze, 74 duplicate pushes/day 08-04; `/var/lib/homelab` claimed by two roles with different owners, latent, found by the guard) | Single-owner-per-file principle — [ops-discipline §2](../operations/ops-discipline.md) — sharpened for directories: **one role owns the DIRECTORY, others own their FILES inside it**; the least-privileged writer must win, because root writes into a user-owned dir but not the reverse. Instance 2 added the non-obvious mechanism: atomic writes (`mkstemp` + `os.replace`) need permission on the **directory**, so a writable state file can still be unwritable, and the symptom (duplicate notifications) appears nowhere near the cause. Class guard = [`tests/test_shared_path_ownership.py`](../../tests/test_shared_path_ownership.py), which found instance 3 on its first run. A later recurrence: [indexer observer claimed the shared textfile dir 10-11](2026-10-11-indexer-shared-metric-ownership.md). |
| Blanket container-config flag ignores per-image init/capability assumptions | 3× ([hardening sweep: init:true vs s6-overlay + cap_drop ALL vs DAC_OVERRIDE 06-07](2026-06-07-container-hardening-sweep-init-and-capdrop-bricks.md); **[tailscale-dns: injected tini stripped of CAP_KILL → unrecoverable restart 09-09](2026-09-09-tailscale-dns-capkill-unrecoverable-restart.md)**) | Per-image awareness: s6-overlay images (linuxserver.io and similar) need PID 1 → no `init: true`; root containers that bridge mixed file-ownership need `cap_add: DAC_OVERRIDE`. YAML is valid + lint passes; only the running container reveals it. **Verify live state + logs per service after deploy — `failed=0` ≠ container survived.** |
| Delivery-side blind spot (health check proves input accepted, not output received) | 1× ([ntfy 36h silent, subscribers=0 unwatched 06-06](2026-06-06-ntfy-silent-delivery-subscribers-zero.md)) | **Shipped 06-21 (`d3b2c98`):** `NtfyNoSubscribers` rule watches `max_over_time(ntfy_subscribers_total[6h]) == 0` — sustained zero, not naive `==0` (the gauge oscillates 0↔1 as the phone app fore/backgrounds, so `==0` would false-fire). `NtfyDown` only sees `up==0`. Push-proxy still deferred. Sibling of silent-cron (metric green, no rule watching the *delivery* metric). |
| Stale single-instance lockfile → silent-exit respawn loop (qBit) | 1× ([2026-05-29](2026-05-29-qbit-stale-lockfile-respawn-loop.md)) | media custom-init `02-clear-stale-lock.sh` clears the lock before s6 starts qbittorrent. |
| Self-healing loop whose "state changed?" predicate is structurally always-true (heals every tick, destroys what it guards) | 1× ([NFS mount daemon 30s remount storm 06-07](2026-06-07-tdarr-nfs-mount-30s-remount-storm.md): compared mounted `ip:path` vs picked bare `ip` → force-unmounted both NFS shares every 30s → killed every multi-GB read) | Fixed: compare against the full expected `ip:path` source, switch only on a real LAN↔Tailscale change. Lesson: a self-healing daemon's change-detection predicate must compare like-for-like; an always-true predicate turns a healer into a wrecker on a timer. Was misdiagnosed once as a `soft`-mount/server-hiccup issue — `dd` + the daemon log (every-30s `switching`) found the real cause. |
| **Check-mode `uri`-skip poisons `.json` consumers** (deep-check `--check` skips every `uri` GET → `{skipped:true}`, no `.json` → the next task that runs and resolves `X.json` in an arg/set_fact/loop crashes at finalization) | 4× — a media-app role (2026-07-02 brick loop; deployed live), + latent in three more roles incl. **beszel, jellyseerr** (found by the class test) ([2026-07-02 autodeploy brick loop](2026-07-02-autodeploy-strict-templating-brick-loop.md)) | Root cause is **CHECK MODE, not ansible-core version** — reproduces on 2.20.5 too, so **pinning fixes nothing** (the earlier "pin = warning suppression" framing was itself wrong). Fix = `when: … and not ansible_check_mode` on each live-API config `include_tasks` (check mode can validate nothing there anyway). Class guard = [`tests/test_uri_check_mode_safety.py`](../../tests/test_uri_check_mode_safety.py) (repo-wide: any check-mode-reachable `uri`-skip `.json` crash fails CI). |
| **Check-mode probe with a stale precondition** (a `check_mode: false` `command` runs under `--check` while the task that establishes what it probes — a container stop, a mount, a service start — is a no-op that still reports `changed`) | 1× ([filebrowser bolt-lock timeout rolled back `deploy-v2026.08.04.1`](2026-08-04-check-mode-probe-stale-precondition-rolled-back-tag.md)) | Sibling accessor of the `uri`-skip row above: same gate, same blast radius, invisible to that row's guard. Three accepted escapes — gate the task/`block` on `not ansible_check_mode`, gate the whole include, or make the probe `failed_when: false`. Class guard = [`tests/test_check_mode_probe_safety.py`](../../tests/test_check_mode_probe_safety.py). Note two other roles (incl. storage) already had the identical shape and survived only because an unrelated fix happened to cover them — "no other instance has bitten" was not evidence of safety. |
| **Component pinned to "latest" with no migration path for the state it owns** | 1× ([MLflow schema crash-loop rolled back a fleet deploy 09-07](2026-09-07-mlflow-unpinned-latest-without-db-migration.md)) | Track-latest + `build: always` guarantees the schema eventually moves; no migration step guarantees the next deploy after it moves is a crash loop. Fix = run the vendor's migration as a deploy step (idempotent, before the health probe), **not** pinning — the no-pinning directive is deliberate. Also **latent-until-deployed**: a role can be broken for months while the running container predates the break, so the trigger is the first redeploy, not a change. Every other unpinned service with persistent state has the same shape and is unaudited. |
| **Self-healer gives up and erases its own evidence** | 1× ([tailscale-dns 30 h dead 09-09](2026-09-09-tailscale-dns-capkill-unrecoverable-restart.md)) | `unhealthy-watchdog` logged `RESTART FAILED` — the highest-value line in the incident — and nothing alerted on it, then GC'd its own state as "container absent", making the dead service invisible to the thing watching it. A watchdog that cannot heal must escalate, not forget. Unalerted today. |

## Entries

- [2026-10-15 — release push blocked by a worktree-only hook env and pre-commit's checkout-wide save-and-restore](2026-10-15-shared-checkout-release-push-friction.md)
- [2026-10-11 — obsolete user prune collides with retention](2026-10-11-retention-prune-cron-collision.md)
- [2026-10-11 — indexer observer claimed a shared metric directory](2026-10-11-indexer-shared-metric-ownership.md)
- [2026-10-03 — MLflow rebuild crash-looped on SQLAlchemy 2.1's psycopg default; CD rolled back](2026-10-03-mlflow-rebuild-psycopg-dialect-rollback.md)
- [2026-09-28 — Tdarr playbook bypassed the base-role deployment boundary](2026-09-28-tdarr-playbook-base-role-escape.md)
- [2026-09-28 — shared Drive quota interrupted restore reads](2026-09-28-shared-drive-oauth-quota.md)
- [2026-09-28 — Mac sleep left the GitHub runner down 19h; Mac alerts flapped as a burst](2026-09-28-mac-sleep-runner-one-shot-agent.md)
- [2026-09-26 — the NFS supervisor remounted a healthy share it was not allowed to see](2026-09-26-nfs-supervisor-remounted-a-healthy-share-it-could-not-see.md)
- [2026-09-25 — cAdvisor filesystem scans stalled metrics at the memory cap](2026-09-25-cadvisor-scrapes-stalled-at-memory-cap.md)
- [2026-09-24 — the native NFS health check called a missing binary](2026-09-24-nfs-health-check-missing-binary.md)
- [2026-09-24 — beszel recorded no container stats for ten days and nothing noticed](2026-09-24-beszel-container-stats-blackout-ten-days-unseen.md)
- [2026-09-09 — a stripped init could not signal its child, so a routine restart left tailscale-dns dead for 30 h](2026-09-09-tailscale-dns-capkill-unrecoverable-restart.md)
- [2026-09-07 — "track latest" with no migration step crash-looped MLflow and rolled back a whole-fleet deploy](2026-09-07-mlflow-unpinned-latest-without-db-migration.md)
- [2026-09-03 — Jellyfin's ffprobe was OOM-killed, so a direct-playable remux became a 16× transcode loop](2026-09-03-jellyfin-probe-oom-forced-transcode-loop.md)
- [2026-08-04 — a `check_mode: false` probe with a stale precondition rolled back a whole deploy tag](2026-08-04-check-mode-probe-stale-precondition-rolled-back-tag.md)
- [2026-08-02 — Declared the USB 2.0 workaround "resolved" one boot early; it wasn't](2026-08-02-usb2-not-sufficient-premature-resolution.md)
- [2026-08-02 — Phase 3 migration took the Pi down under sustained load; ssd2 rolled back entirely](2026-08-02-ssd2-migration-sustained-load-outage-and-rollback.md)
- [2026-08-01 — Hot-plugging the second SSD took the root fs read-only in under 15 seconds](2026-08-01-usb-ssd-hotplug-readonly-recurrence.md)
- [2026-08-01 — Second USB SSD browned out the root SSD; root fs went read-only for ~15 min, silently](2026-08-01-usb-ssd-root-dropout-readonly.md)
- [2026-07-16 — Watchtower pulled a distroless victoria-logs image; the exec healthcheck went permanently unhealthy](2026-07-16-victorialogs-distroless-image-broke-exec-healthcheck.md)
- [2026-07-16 — ntfy-bridge crashed on em-dash alert titles: notifications silently undelivered](2026-07-16-ntfy-bridge-emdash-title-crashed-delivery.md)
- [2026-07-15 — CD ran a verification playbook as a deploy target, rolling back the domain-migration tag](2026-07-15-cd-ran-verify-playbook-as-deploy-target.md)
- [2026-07-14 — CD silently skipped speedtest_tracker (role→playbook name mismatch, false success)](2026-07-14-cd-silent-skip-role-playbook-name-mismatch.md)
- [2026-07-14 — CD self-sabotaging rollback: zot check-mode htpasswd bug reverted the controller mid-cycle](2026-07-14-cd-self-sabotaging-rollback-zot-check-mode.md)
- [2026-07-14 — CD rollback: adguard check-mode `uri`-skip (`.content` variant) blocked the SSO Phase 0 tag](2026-07-14-adguard-check-mode-content-uri-skip-rollback.md)
- [2026-07-11 — tailscale-dns dark ~3h with an empty Docker network endpoint (REFUSED on every forward)](2026-07-11-tailscale-dns-lost-network-endpoint-refused-forward.md)
- [2026-07-02 — homelab-autodeploy multi-day brick loop (strict ansible-core 2.21 + self-reverting rollback)](2026-07-02-autodeploy-strict-templating-brick-loop.md)
- [2026-06-25 — Zot rejected every image push (HTTP 415); the build→Zot push path had never actually worked](2026-06-25-zot-rejected-every-push-415-never-exercised.md)
- [2026-06-07 — Tdarr transcode errors were a 30s self-inflicted NFS remount storm](2026-06-07-tdarr-nfs-mount-30s-remount-storm.md)
- [2026-06-07 — Container security-hardening sweep bricked 3 services (init/s6 + cap_drop/DAC_OVERRIDE)](2026-06-07-container-hardening-sweep-init-and-capdrop-bricks.md)
- [2026-06-07 — Alertmanager crash-looped 3h on a folded inhibit matcher (notification blackout)](2026-06-07-alertmanager-crash-loop-folded-matcher.md)
- [2026-06-06 — ntfy delivered nothing for 36h while every health check stayed green (subscribers=0)](2026-06-06-ntfy-silent-delivery-subscribers-zero.md)
- [2026-05-29 — qBittorrent stale lockfile → silent-exit respawn loop](2026-05-29-qbit-stale-lockfile-respawn-loop.md)
- [2026-05-29 — Tdarr-dedup deploy incident tail](2026-05-29-tdarr-deploy-incident-tail.md)
- [2026-05-29 — byparr core dumps silently filled 132G of the SSD](2026-05-29-byparr-coredump-disk-fill.md)
