# 2026-06-07 — Alertmanager crash-looped 3h on a folded inhibit matcher (notification blackout)

- **What happened:** A "lots of errors spawning" sweep found Alertmanager in a crash-restart loop —
  `RestartCount=114`, restarting every ~60s since the config that shipped in `3a72e43`. Every restart
  logged `level=ERROR ... Loading configuration file failed ... err="matcher value contains unescaped
  double quote: \"warn\" alertname=~\"SpeedtestScrapeDown|UptimeKumaDown|NtfyDown\""` and exited 1.
  Because Alertmanager was never up, **no alert reached any notification channel for ~3h** — 6 alerts
  were firing in vmalert (incl. `SrvDiskHigh`) and routed nowhere.
- **Why:** The `VMAgentScrapeDown` inhibit rule's `target_matchers` was written in YAML block style but
  the second matcher was missing its leading dash:

  ```yaml
  target_matchers:
    - severity="warn"
      alertname=~"SpeedtestScrapeDown|UptimeKumaDown|NtfyDown"   # no leading dash
  ```

  YAML folds an un-dashed continuation line into the **previous scalar**, so Alertmanager received one
  matcher string `severity="warn" alertname=~"..."` — two matchers glued together — whose embedded `"`
  is an "unescaped double quote." Valid YAML, valid Jinja2, passes `ansible-playbook --syntax-check`;
  only the Alertmanager config parser rejects it, at container start. Fix = make each matcher its own
  list item (`- severity="warn"` / `- alertname=~"..."`); the sibling flow-style rules `[a, b]` were
  always correct.
- **Class:** Config that is valid YAML but invalid to the consuming program — the **same shape as the
  vmalert Go-template quote collision** ([368e10a/86d1c25](../conventions/vmalert-rule-quoting.md)): a
  templated monitoring config whose error is invisible to YAML/syntax checks and only surfaces as a
  runtime crash-loop. Compounded by a **delivery-side blind spot** (sibling of the
  [ntfy subscribers=0 incident](2026-06-06-ntfy-silent-delivery-subscribers-zero.md)): the alert router
  itself being down is the one failure nothing in the alerting pipeline can page about.
- **Occurrence:** 1st for *Alertmanager config* specifically; 3rd of the broader "valid-YAML /
  invalid-to-consumer monitoring config → crash-loop" class after the two vmalert quote collisions.
- **Structural fix (shipped):** two gates, mirroring how vmalert rules are guarded:
  1. **Deploy-time** — `alertmanager/tasks/deploy.yml` now runs `amtool check-config` (the authoritative
     parser, `/bin/amtool` in the image) on the rendered file **before** starting the container, so a
     malformed config fails the deploy instead of crash-looping the running service.
  2. **CI-time** — `tests/test_alertmanager_config.py` renders the Jinja2 surface and asserts every
     route/inhibit matcher is a single `label op value` token (a folded matcher has ≥2). The exact
     pre-fix string is pinned as a regression fixture. Wired into `make test`.
  Recovery was just the corrected template + `make alertmanager` (container recreated, `RestartCount=0`,
  config loads, alerts route to `ntfy-bridge` again — verified end-to-end).
- **Follow-up (deferred, low):** the blackout itself was only caught by a manual sweep. A durable catch
  would be an `up{job="alertmanager"}==0` / Alertmanager-self-watch rule — but that has the same
  "who-watches-the-watcher" gap (a dead router can't page for itself); the real backstop is the
  deploy/CI gates above (prevent the bad config) plus the existing `VMAgentScrapeDown` dead-man's-switch
  pattern extended to the router. Noted, not shipped this pass.
