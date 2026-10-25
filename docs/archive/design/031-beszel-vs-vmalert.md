# 031 — Beszel vs vmalert: decision gate

> **Historical decision proposal.** The June review date below is past; no
> completed scorecard is asserted. Current code retains Beszel plus vmalert,
> with independent Beszel freshness checks.

**Status:** decision-pending (Sprint 18 Story 7)
**Decision date:** 2026-06-12 (2 wks post-Sprint 18 deploy)
**Owner:** operator

## What we're deciding

After Sprint 18 lands, we'll have **two alerting paths** running side by side:

1. **Beszel** — UI-driven monitoring with built-in rule engine. Currently
   alerts on CPU / memory / container lifecycle. Thresholds were uncalibrated
   pre-Sprint 18 (issue #11). UI is operator-friendly.
2. **vmalert + Alertmanager + ntfy-bridge** — PromQL/LogsQL rule path against
   the VictoriaMetrics + VictoriaLogs substrate. Codified-as-config, fires
   to ntfy via the shim. Audit-trail in `/var/log/alert-audit.log`.

After 2 weeks of parallel operation we make a call: keep one, both, or
something else.

## Why not decide now

We don't have the parallel-run data yet. Both paths' fire rates, false-positive
rates, mobile-reachability rates, and audit-trail completeness are guesses
until we observe them.

Premature decision risk: rip Beszel out for a vmalert that turns out to have
a 10% notification-loss rate; or keep Beszel + vmalert forever because
nobody wants to make a call.

## Decision criteria

Each criterion gets a **B** (Beszel-better), **V** (vmalert-better), or **=**
(tie) after the 2-wk soak. Majority verdict wins.

| Criterion | Why it matters | Verdict (TBD 2026-06-12) |
|---|---|---|
| Fire rate / week | Lower = fewer false positives. | TBD |
| Mobile delivery success | The whole point of alerting. Both go via ntfy, so this should tie unless one path has more curl-style bugs. | TBD |
| Audit trail | Can we answer "did the disk alert fire last Tuesday at 3am?" | TBD |
| Threshold tuning ergonomics | How painful is it to bump a threshold? Beszel: UI clicks. vmalert: edit YAML, redeploy, vmalert auto-reloads. | TBD |
| Rule expressiveness | Does the rule language let us write what we mean? Beszel's rule UI is shallower than PromQL. | TBD |
| Container memory cost | Pi has finite RAM. Beszel ~256M, vmalert+AM+bridge ~384M combined. | TBD |
| Mac client UX | Beszel has a phone app; vmalert/AM is web only. | TBD |

## Candidate outcomes

### Option A — Keep both ✋

Beszel for the UI / dashboards; vmalert for the alert path. Beszel's
alert rules get disabled or set to silent (already done for disk + memory
in Sprint 14 — extend to all rules).

**When this wins:** vmalert proves itself for alerting AND Beszel dashboards
remain useful AND combined memory cost is acceptable.

### Option B — Keep Beszel, decom vmalert + AM 🪦

Operator finds Beszel sufficient after threshold recalibration; vmalert/AM
adds operational complexity (rule files, AM config, bridge container) without
proportional benefit.

**When this wins:** vmalert turns out to misfire often, or the bridge container
loses notifications, or the threshold-tuning ergonomics make YAML-driven
rules worse in practice than Beszel UI clicks.

### Option C — Decom Beszel, keep vmalert + AM 🚀

vmalert+AM covers everything Beszel does plus log-based rules; Beszel's UI
isn't worth its memory + scrape cost.

**When this wins:** vmalert proves stable AND Beszel's threshold-recalibration
work (Sprint 18 Story 5) reveals it was always under- or over-tuned in ways
PromQL handles more cleanly.

### Option D — Both, but reduce overlap 🧹

Beszel keeps lifecycle alerts (container restarts / disappearance) — those
are inherently UI-shaped events. vmalert owns saturation + log-based rules.
No deletions; smaller Beszel rule set.

**When this wins:** parallel-run shows each tool is genuinely better at
different alert shapes.

## Recommendation (subject to data)

**Default to Option D** unless soak data points strongly elsewhere. Beszel's
"container disappeared" alert is qualitatively different from vmalert's
PromQL — easier to express as a UI rule than as a clever
`absent()` PromQL. vmalert is qualitatively better at log-based alerts and
at audit-trail-grade reliability.

The doc captures this so when the 2026-06-12 review happens, the operator
has a paper trail, not a hot-take.

## Process

1. 2026-05-29: Sprint 18 deployed + 1-week soak begins.
2. 2026-06-05: cron decom step (Sprint 18 Story 6 — flip `common_use_vmalert`).
3. 2026-06-12: open this doc, fill in the verdict column from observed data,
   commit the recommendation, action it in a follow-up commit.
