# 2026-10-11 — obsolete user prune collides with retention

- **What happened:** At 04:00 TRT retention cleanup exited 1 with Docker's “a prune operation is already running” and sent a high-priority ntfy failure. The root retention cron and an obsolete `pi` `docker system prune -af --volumes` cron both ran Sunday at 04:00.
- **Why:** The common role removed the superseded cron only for root. The older user-owned copy survived every subsequent deployment.
- **Class:** Incomplete ownership reconciliation leaves a second scheduled writer.
- **Occurrence:** First diagnosed collision of these two retained schedules; container and systemd checks found no current service outage. The old job's presence does not prove it deleted persistent data.
- **Structural fix:** Remove the named legacy cron for both root and `docker_user` through the common monitoring role. Keep the age-gated retention job, apply only monitoring after check/diff, and rerun it to prove completion. Base remains excluded from tag-driven deployment; reconcile it manually and record the result in the operational report.
