# Alert Discipline

## Why this exists

ntfy was being repaged for the same sustained condition every 1.5–2 hours
and every cron tic (`memory-alert.sh` flapping at 75 %). The user stopped reading the alerts. This doc fixes both
the threshold logic and the policy.

Two principles:

1. **One source per metric.** No metric is alerted from two places.
2. **Match cooldown to alert shape.** Bursty errors need fast re-fires; sustained-state alerts need 24 h or more.

## Alert taxonomy

| Shape | Examples | Cooldown | Hysteresis |
|---|---|---|---|
| Bursty | OOM kill | 3 h | n/a (each fire is a new event) |
| Sustained-state | swap_high | 24 h | high/low watermarks |
| Lifecycle | container Status (up/down) | n/a — Beszel emits exactly once per transition | built into Beszel |

**Bursty:** A re-fire after the cooldown window is a *new incident*. The first
qBit auth break and the next one a day later are independent. Fast cooldowns OK.

**Sustained-state:** The alert is true continuously. Re-firing it every 3 h is
nagging, not informing. 24 h re-fire is the floor; pair it with a high/low
hysteresis so a "cleared" event is sent when the condition resolves.

**Lifecycle:** originally Beszel's built-in min-duration (`min: 5, value: 12`
on Load). Superseded 2026-10-15: Beszel re-evaluates a bare threshold, so a host
sitting at the line fired an above/below pair every few minutes. Load is now the
vmalert `CPULoadHigh`/`CPULoadCritical` pair, whose `keep_firing_for` holds one
episode open across brief dips.

## Source of truth — which alerter owns which metric

| Metric | Owner | Why |
|---|---|---|
| Swap % | `common/memory-alert.sh.j2` | Hysteresis + cooldown + ntfy actions |
| OOM kills | `common/memory-alert.sh.j2` | Reads `journalctl -k` — Beszel can't see this |
| Disk % | `common/disk-alert.sh.j2` | Cooldown + 5 pp jump escape hatch + clean-junk action |
| CPU | `vmalert` | Covered by the load rules below; Beszel's 85% CPU rule was removed 2026-10-15 (no hysteresis, flapped at its threshold) |
| **Load** | `vmalert` | `CPULoadHigh` (1.5× cores, 30m) / `CPULoadCritical` (3×, 10m) with `keep_firing_for: 30m` (moved from Beszel LoadAvg5 2026-10-15) |
| Temperature | `beszel` | Same |
| Container Status | `beszel` | Same — lifecycle alert |

**Removed from Beszel:** Memory (was duplicating the script-side hysteresis
rule), Disk (was duplicating the script-side rule with cooldown + actions).
See `beszel_alert_rules_to_remove` in `ansible/roles/beszel/defaults/main.yml`.

## State file conventions

- Directory: `/var/lib/homelab/` (cron-deployed alert scripts) and
  `/opt/homelab/control/` (homelab-control).
- Format: shell-flat (`<ts> <pct> <fired>`) where one rule per file works
  (`disk-alert`, `memory-alert`); JSON dict (`{rule: epoch}`) where one
  state file holds many rules.
- Writes for the JSON form are atomic (`tempfile.mkstemp` + `os.replace`).
  Direct `open('w')` corrupts state under `kill -9` and resets cooldowns →
  alert spam.

## Memory-alert hysteresis — operator guide

`ansible/roles/common/templates/memory-alert.sh.j2` runs hourly. Defaults
(`ansible/roles/common/defaults/main.yml`):

```yaml
common_swap_alert_high_pct: 85       # fire above this
common_swap_alert_low_pct: 70        # send cleared event below this, re-arm
common_swap_alert_cooldown_hours: 24
```

State file (`/var/lib/homelab/memory-alert.state`):

```text
1747591800 87 1
└─ last_fire_ts
            └─ last_seen_pct
               └─ fired_flag (1 = currently alerting, 0 = quiet)
```

- `fired=0` and `pct >= 85` → fire, set `fired=1`, record ts.
- `fired=1` and `pct >= 85` → silent until 24 h since last_fire_ts elapses; then re-fire.
- `fired=1` and `pct <= 70` → send "cleared" ntfy (priority `low`), set `fired=0`.
- Anything else → just refresh `last_seen_pct` for debugging.

## Alertmanager per-route repeat_interval — sustained-state vmalert alerts

The cooldown taxonomy above is for the **cron/script** alerters (memory-alert,
disk-alert) that own their own ntfy POST. The **vmalert → Alertmanager →
ntfy-bridge** path has no per-rule cooldown knob — Alertmanager re-notifies on a
single global `repeat_interval` (`alertmanager_repeat_interval`, 4h). For a
*sustained-state* vmalert alert (a chronic condition that is the known steady
state, not a recurring incident) that 4h cadence is the same nag-spam this doc
exists to kill: `SrvDiskHigh` (warn, 93%) re-paged ~6×/day for a week while `/`
sat chronically at 87–93%.

**Mechanism:** a child route under the top-level route in
`roles/alertmanager/templates/alertmanager.yml.j2` overrides `repeat_interval`
for matched alerts only:

```yaml
route:
  ...
  repeat_interval: {{ alertmanager_repeat_interval }}      # 4h — incidents
  routes:
    - matchers: ['alertname=~"{{ alertmanager_sustained_alert_pattern }}"']
      repeat_interval: {{ alertmanager_sustained_repeat_interval }}   # 24h — sustained
```

Defaults (`roles/alertmanager/defaults/main.yml`):
`alertmanager_sustained_repeat_interval: 24h`,
`alertmanager_sustained_alert_pattern: "SrvDiskHigh|SrvDiskFillRateHigh"`.
Add a future sustained-state alert by extending the pattern var — no template
edit. This is the vmalert-path equivalent of the 24h cron cooldown.

**Scoping rule (same as the cron cooldowns): only sustained STATE qualifies.**

- `SrvDiskCritical` (95%) is excluded — a true emergency should keep nagging at 4h.
- `SwapUsageHigh` (85%) is excluded — swap is ~27% steady-state, so an 85% breach
  is an actionable OOM-risk excursion, not a chronic level. Any breach deserves 4h.
- Composes with `inhibit_rules`: `repeat_interval` controls re-notify *cadence*
  during the chronic-high phase; the `SrvDiskCritical→SrvDiskHigh` inhibition
  controls alert *count* once disk crosses 95%. Different phases, both needed.

## Reboot requirement for the zram change

`common_swap_zram_size_mib` was bumped 4096 → 6144 in Sprint 014. The zram
systemd unit is static (generated at boot from `/etc/rpi/swap.conf`) and
cannot be reloaded at runtime. After `make base` deploys the new config:

```bash
ssh pi@100.64.0.10 'sudo reboot'
# wait ~60 s
ssh pi@100.64.0.10 'swapon --show; free -m'
# expect: zram0 ~6 GB + /var/swap 8 GB
```

The file swap at `/var/swap` (8 GB, priority 10) was committed in Sprint 013
as `ansible/roles/common/tasks/swap.yml` but never deployed — Sprint 014's
`make base` activates it together with the resized zram.

## What we did *not* do (and why)

- **No MemFree/MemAvailable rule.** The previous script had no such rule;
  adding one would *increase* noise, not reduce it. OOM detection + swap
  hysteresis covers the pre-OOM and at-OOM signals.
- **No vmalert migration.** Out of scope for Sprint 014. Cron-script alerts
  remain the architecture.

## Observation window — 48 h after Sprint 014 deploy

The four changes touch three independent surfaces. None of them
generate signal on the day of deploy — they pay off only when the
underlying condition (memory pressure / load spike)
recurs. Plan to check back at +24 h and +48 h:

| When | What to verify | How |
|---|---|---|
| +1 h | swap state file present, well-formed | `ssh pi@100.64.0.10 'cat /var/lib/homelab/memory-alert.state'` → `<ts> <pct> 0` |
| +24 h | Load alert fires correctly under sustained load (now vmalert `CPULoadHigh`, after 30m) | `ssh pi@100.64.0.10 'stress --cpu 4 --timeout 360'` while watching ntfy — expect one Load alert 5 min in |
| +24 h | Memory alert hasn't flapped | `grep -c ALERT /var/log/memory-monitor.log` (delta should be 0 unless swap genuinely crossed 85 %) |
| +48 h | Beszel alerts present | `curl -s -H "Authorization: $TOKEN" http://localhost:8090/api/collections/alerts/records` → 2 items (Temperature, Status; CPU and LoadAvg5 moved to vmalert 2026-10-15) |
| +48 h | Swap stable after a full duty cycle | `cat /proc/swaps` — zram still primary, /var/swap not in /proc/swaps (it's writeback only) |

If any check fails, the rollback in each commit message is the
source of truth. The `git revert <sha>` path is clean — no state
migrations to worry about.

## Verification cheatsheet

After deploy + reboot:

```bash
# Swap state file present + well-formed
ssh pi@100.64.0.10 'cat /var/lib/homelab/memory-alert.state'

# Beszel rules (Memory, CPU and LoadAvg5 absent; Temperature + Status present)
ssh pi@100.64.0.10 'curl -s -H "Authorization: $TOKEN" http://localhost:8090/api/collections/alerts/records | python3 -m json.tool | grep -E "\"name\""'
```
