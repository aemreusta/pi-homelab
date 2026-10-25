# 2026-06-07 — Container security-hardening sweep bricked 3 services (init/s6 + cap_drop/DAC_OVERRIDE)

- **What happened:** A blanket security-hardening pass added `init: true` to all 27
  compose templates and `cap_drop: [ALL]` to four that lacked it. On deploy, **three
  services crash-looped**: `tdarr` and `speedtest-tracker` (ExitCode 100,
  `s6-overlay-suexec: fatal: can only run as pid 1`) and `fluentbit` (SQLite
  `attempt to write a readonly database`, tail-input init failed). Caught during
  per-service post-deploy verification; the rest of the stack stayed healthy.
- **Why:** Two image-specific assumptions a global flag can't honor. (1) `init: true`
  inserts docker's tini as PID 1, but **s6-overlay** images (linuxserver.io + tdarr)
  *require* s6 to be PID 1 — tini demotes it and s6 aborts. (2) `cap_drop: ALL` strips
  root's default **CAP_DAC_OVERRIDE**; fluentbit runs as root (distroless, no PUID) and
  needs it to write its SQLite tail-DB into a `1000:1000`-owned dir while reading
  root-owned Docker logs — without the cap, the DB dir is unwritable → "readonly database".
- **Class:** Blanket container-config flag applied without honoring per-image init/capability
  assumptions. Sibling of the "valid-config / invalid-to-consumer" class — the YAML is valid
  and `--syntax-check`/`ansible-lint` pass; only the running container reveals the break, and
  a service with no post-deploy healthcheck would crash-loop silently (the recap shows
  `failed=0`).
- **Occurrence:** 1st of this class. (The verify-live-state-not-just-recap discipline it
  reinforces is shared with the silent-cron class.)
- **Structural fix:** Shipped — `init: true` removed from the two s6 templates (tdarr,
  speedtest_tracker) with a self-documenting comment; `fluentbit_cap_add: [DAC_OVERRIDE]`
  added with a load-bearing comment. Rule recorded in
  the operator's hardening-pitfalls notes. Convention for future hardening: before adding
  `init: true`, check the image init system (s6-overlay → no); before `cap_drop: ALL` on a
  **root** container, confirm it doesn't rely on DAC_OVERRIDE to bridge mixed ownership.
  **Verify every hardened container's live state + logs after deploy — `PLAY RECAP failed=0`
  does not prove the container survived.**
