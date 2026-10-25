# Uptime Kuma backup JSON (reproducibility artifact)

Uptime Kuma 1.x has no REST API for monitor CRUD, so its config (monitors,
notification, status page, maintenance windows) is entered once in the UI and
exported here as the **source of truth**.

After the one-time UI setup (see `docs/archive/runbooks/039-2026-05-31-uptime-kuma-setup.md`):

1. UK → Settings → Backup → **Export**.
2. Save the file here as `../templates/uptime-kuma-backup.json.j2` and commit it (the ntfyserverurl field MUST stay `{{ uptime_kuma_ntfy_url }}` — never paste a literal hostname back in).
3. Re-export + re-commit whenever monitors change — that diff is the review trail.

**Restore** (after a data-volume wipe / rebuild): `make uptime-kuma`, complete
first-run admin (`vault_uptime_kuma_password`), then Settings → Backup → **Import**
the committed JSON. UK 1.x import is UI-only — there is no headless import.
