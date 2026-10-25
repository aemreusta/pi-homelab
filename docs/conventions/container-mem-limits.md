# Convention — container `mem_limit` budget on the 8 GiB Pi

**TL;DR:** The sum of all container `mem_limit` ceilings *intentionally*
overcommits physical RAM. A ceiling is a **blast-radius cap for a runaway
container**, not a reserved working set — so "sum of limits > RAM + zram" is the
designed state, not a bug. Do **not** panic-shrink a cap because the total looks
scary; that reintroduces documented outages. Watch **live working-set + swap
pressure**, not the ceiling sum.

## Why this doc exists

An operator note once carried a flat rule — *"OOM risk is real if container
limits sum > RAM + 6 GiB."* Taken literally it triggers the wrong reflex: the sum
*already* exceeds the surface (see audit below), and the natural "fix" is to
shrink the biggest caps. Those shrinks are regressions — caps on this box have
been *raised to their current containment value after* an outage. This doc records the correct mental model so the next reader doesn't
relearn it the hard way.

## Audit snapshot (2026-06-05)

33 containers carry a `mem_limit`. Ceiling sum vs the host surface:

| | Value |
|---|---|
| Σ all `mem_limit` ceilings | **16.69 GiB** (updated 2026-06-21) |
| Physical RAM | 8 GiB |
| zram swap surface (compressed) | 6 GiB |
| RAM + zram | 14 GiB |
| **Σ ceilings ÷ surface** | **119 %** (209 % of physical RAM) |

Live aggregate container RSS at audit time was **≈ 4.0 GiB** — i.e. real
working-set margin is comfortable; only the ceiling *sum* overcommits. The caps
below are deliberately oversized **containment** caps:

| Container | Cap | Live RSS | Rationale |
|---|---|---|---|
| `qbittorrent` | 3072M | ~230 MiB | libtorrent RSS scales with active-torrent count (piece-hash + peer/DHT tables); hundreds of torrents. Same containment semantics. |
| `grafana` | 768M | ~190 MiB | Plugin-load spike on reboot (grafana_tmp restart-loop incident). |
| `byparr` | 1024M | ~820 MiB | Camoufox/Firefox leak; **the one cap actually near its ceiling (~80 %)** — mitigated by the weekly restart cron, not by raising the cap. |

## The principle

- A `mem_limit` is the point at which Docker **OOM-kills one container** (which
  then `restart: unless-stopped` recovers in seconds). It is *not* memory
  reserved away from other containers.
- Overcommitting ceilings is correct **when caps are containment caps**, because
  not all containers run away simultaneously. The cap converts a host-wide
  swap-thrash (manual reboot, minutes of downtime) into a single-container
  OOM-kill + auto-restart (seconds).
- Therefore the metric that signals real risk is **live working-set vs RAM** and
  **sustained swap pressure**, never the ceiling sum.

## What to actually watch

```bash
ssh pi@raspberrypi.local 'free -m; docker stats --no-stream \
  --format "{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}"'
```

- Any container sustained **> ~85 %** of its cap that is *not* a known-leak +
  restart-cron pair (byparr) → investigate that container, don't just raise the
  cap.
- `free -m` `available` trending toward zero **with** swap filling and staying
  full (this box pages chronically — ~2 GiB zram resident is normal; *growth* is
  the signal) → host-level pressure; the alert path is the SrvDisk/host alerts,
  not a per-cap change.

## Before changing any cap

1. Check the cap's inline comment — byparr/qbit document why.
   A cap with incident history is load-bearing.
2. Confirm against **live** RSS (above), not the ceiling sum.
3. If raising a cap, re-run the audit (`grep -rn mem_limit ansible/roles/*/defaults`)
   and confirm live working-set still leaves RAM headroom — the surface is 8 GiB
   physical, and zram is compressed cold-page spill, not free headroom.

## See also

- `ansible/roles/media/defaults/main.yml` — qbittorrent/byparr cap rationale
- grafana_tmp restart-loop incident — why grafana's 768M is not shrinkable
