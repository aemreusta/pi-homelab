# 2026-09-07 — "track latest" with no migration step crash-looped MLflow and rolled back a whole-fleet deploy

- **What happened:** `deploy-v2026.09.07.1` removed base-role `meta` dependencies from all 35 service
  roles, so the CD's changed-role plan was every service at once. It reached `mlflow`, whose health
  probe failed 30 times with `Connection reset by peer`, retried the target three times over 13
  minutes, then declared `deploy FAILED at target mlflow` and rolled the Pi back to
  `deploy-v2026.09.05.1`. The rollback worked as designed and every other service stayed healthy;
  MLflow did not, because the rollback rebuilds from the same unpinned spec and hits the same wall.
  Recovery was manual: `pg_dump` the 11 MB database, `mlflow db upgrade` in a throwaway container,
  restart. Healthy at 10:46, `http=200`.
- **Why:** the MLflow role deliberately does not pin a version — `requirements.txt` carries an
  explicit *"Operator directive: no version pinning — track latest"* and the compose build is
  `build: always`, so **every** deploy installs the newest MLflow. MLflow refuses to start when its
  expected schema is ahead of the database's (`Detected out-of-date database schema (found version
  6f8d9c3b2a1e, but expected b7e2c1a4d9f3)`), and the role had no migration step. Track-latest and
  no-migration are an incoherent pair: the first guarantees the schema will eventually move, the
  second guarantees the next deploy after it moves is a crash loop. The role had shipped that way
  since PRD-004. The fleet-wide deploy did not cause this — it was the first thing in months to
  deploy `mlflow` at all, and any single `make mlflow` would have done the same.
- **Class:** *a component pinned to "latest" with no migration path for the state it owns.* Sibling
  of the vendor-`latest` healthcheck breakage (2026-07-16, victoria-logs going distroless), but a
  level worse: that one broke a probe, this one breaks startup and cannot self-heal, because the
  fix needs a mutation of persistent state that only a human was authorised to make. The
  neighbouring shape is **latent-until-deployed** — a role can be broken for months and look fine,
  because nothing re-deploys it and the running container predates the break. `build: always`
  converts "we track latest" into "the blast radius is whatever upstream shipped since last time",
  and the interval is invisible.
- **Occurrence:** 1st of the missing-migration shape. 2nd of *vendor-latest bites on redeploy*
  ([victoria-logs distroless healthcheck 2026-07-16](2026-07-16-victorialogs-distroless-image-broke-exec-healthcheck.md)). Also the 1st real
  exercise of the CD's rollback path outside `make cd-drill` — it did exactly what it promised:
  three bounded retries, fail, revert to the last good tag, leave the fleet up.
- **Structural fix:** `mlflow db upgrade` now runs as a deploy step, between the image build and the
  health probe, in a `docker compose run --rm --no-deps` container (the server may be crash-looping,
  so there is nothing to `docker exec` into) with the URI read from the container's own environment
  so the password never reaches a command line. It is idempotent, so it is safe on every deploy, and
  a `flush_handlers` puts the restart before the health probe instead of at end-of-play.
  `when: not ansible_check_mode` keeps the deep `--check --diff` gate honest. **Deferred:** every
  other unpinned service with persistent state has the same latent shape and none has been audited. **Do not "fix" this by pinning MLflow**; the no-pinning directive is
  deliberate and recorded in `requirements.txt`. The defect was the missing migration, not the
  policy.
