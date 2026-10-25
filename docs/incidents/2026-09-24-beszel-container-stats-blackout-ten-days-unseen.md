# 2026-09-24 — beszel recorded no container stats for ten days and nothing noticed

- **What happened:** beszel's Docker CPU/Memory charts show a solid hole from **Sep 6 to Sep 17**.
  In beszel's own store the `480m` (8-hour rollup) tier holds **zero** `container_stats` rows for
  **2026-09-07 through 2026-09-14**, one of three on 09-15, one of two on 09-16, and a complete
  3-of-3 every day from **09-17** onward. `system_stats` kept its 3 rows/day throughout, which is
  why only the Docker charts went blank while CPU/Memory stayed continuous. Found by the operator
  looking at a chart, ten days in — there is no alert that could have found it.
- **Why:** the recording failure itself is **not root-caused**, and this entry deliberately does
  not guess. What the timestamps do establish:
  - The blackout brackets the beszel-agent process almost exactly. The agent container was started
    **2026-09-07 06:48** and ran until the Pi rebooted **2026-09-17 06:52**; container recording is
    absent for essentially that whole span and healthy immediately after.
  - It is **not** the 0.20.0 agent upgrade — that landed **09-20**, three days *after* recovery.
  - It is **not** (or not only) the `*/5` cron herd. The stagger that `a1e191e` deployed on
    **09-14 ~19:00** did not restore recording: 09-15 and 09-16 were still short (1/3, 1/2), and
    full recovery lines up with the **reboot**, 2.5 days later. **That commit message overclaims
    — treat the stagger as unproven, not as the fix.** The original 1-minute symptom (~25 % of
    samples missing, clustered on 5-minute boundaries) and this coarse-tier blackout may be two
    different faults; 1m data only retains ~81 minutes, so the earlier pattern can no longer be
    re-checked after the fact.
  - Current state is clean at every tier: `1m` 81/81, `10m` 74/74, `20m` 73/73, `120m` 12/12 per
    day since 09-17, **40 of 40 containers in every sample**. Verified in the UI at the 1-hour
    range — solid, no gaps.
- **Class:** *silent-failure in a monitoring system that is itself unmonitored* — the observability
  island. beszel is **not scraped by vmagent, has no vmalert rule, and exposes no Prometheus
  endpoint** (`:8090/metrics` returns the SPA HTML, not metrics). Its data lives only in its own
  PocketBase SQLite, so every other failure on this box can page and this one cannot. Cousin of the
  silent-cron class (the repo's every-cron-emits-a-metric rule) one level up: there the *job* had no counter, here the
  *monitor* has no counter. Related shape: **the watchdog that cannot watch itself** — see
  [2026-09-09 tailscale-dns](2026-09-09-tailscale-dns-capkill-unrecoverable-restart.md), where
  `unhealthy-watchdog`'s own `RESTART FAILED` was the highest-value line and nothing alerted on it.
- **Occurrence:** 1st for beszel specifically; 2nd of *the monitor is the blind spot* (1st being the
  watchdog-gives-up shape above). Note the deeper pattern: a 10-day outage found
  by a human glancing at a dashboard, like the earlier qbit-cleanup `deleted=0` run.
- **Structural fix:** **deferred** (`BZ-1`…`BZ-4`). The shape is already
  decided by the silent-cron rule: a cron emits `beszel_container_stats_age_seconds` and a
  system-vs-container row ratio to the node_exporter textfile collector, with a matching vmalert
  rule — a monitor with no counter is the same severity class as a cron with no counter. Secondary:
  the agent runs with no `LOG_LEVEL`, so it logged **nothing** across the entire ten days; the next
  recurrence must leave evidence. **No retroactive repair is possible** — beszel rolls up forward
  and never backfills, so the Sep 6–17 hole is permanent and will simply age out of the 30-day
  `480m` window around **2026-10-17**. Do not read that disappearance as a fix.
