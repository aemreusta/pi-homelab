# 2026-08-04 — a `check_mode: false` probe with a stale precondition rolled back a whole deploy tag

- **What happened:** The CD picked up `deploy-v2026.08.04.1` at 13:40, ran for 68 minutes,
  failed all 3 attempts at the `filebrowser` target, and rolled the checkout back to
  `deploy-v2026.08.03.3` at 14:48. Every 5-minute cron tick in that window logged
  `already_running` and load average sat at 15–18 while the rollback re-deployed six roles.
  Nothing was stuck in the deadlock sense — it was progressing the whole time — but the tag
  never landed, and it never will: `needs_deploy()` returns `False` while `head == failed_sha`,
  so the shipped work sat unapplied until a new tag carried the fix.
- **Why:** `filebrowser/tasks/deploy.yml` stopped the container to get exclusive access to
  FileBrowser's bolt database, then read the current auth method with `check_mode: false` so
  the read would still populate under `--check`. Under `--check` those two do not compose:
  `docker_compose_v2: state: stopped` is a **no-op that still reports `changed`**, while the
  probe marked `check_mode: false` **really runs** — against a container that is therefore
  still up and still holding the lock. `Error: timeout`, rc=1. Because the CD gates every
  deploy on `ansible-playbook --check --diff` *before* apply, the failure happened in the
  gate; the apply phase was never reached, which is why the container's `StartedAt` never
  moved and the "it was stopped" reading of the log was wrong. Reproduced deterministically
  from the Mac: identical `ok=24 changed=2 failed=1`. Stopping the container by hand made the
  same command exit 0 — and print `Auth Method: noauth`, i.e. the block had been a no-op for
  its entire short life and was pure deploy risk.
- **Class:** *check-mode divergence bricks the deep-check gate* — the same family as the
  [`uri`-skip `.json` poison](2026-07-02-autodeploy-strict-templating-brick-loop.md), with a
  different accessor. There the consumer read a field the skipped task never produced; here
  the probe depends on **runtime state** the skipped task never changed. The general shape:
  *anything that runs under `--check` while its precondition does not*. `check_mode: false` on
  a read-only probe is a good pattern and is not itself the bug — the bug is a probe that is
  read-only but not *precondition-free*.
- **Occurrence:** 5th in the check-mode family, 1st of this accessor. Two siblings in the
  repo were already safe by accident of a different fix: another service role's `configure.yml` has the
  identical stop→probe shape but its include is gated `not ansible_check_mode` (from the
  2026-07-02 fix), and `storage/tasks/ssd2.yml`'s probe carries `failed_when: false` so it
  cannot fail a play. Neither was protected on purpose.
- **Structural fix:** shipped. The stop/read/set sequence now lives in a `block` gated
  `when: … and not ansible_check_mode`, and the read carries `failed_when: false` so it can
  never again be the thing that fails a deploy. Class guard =
  [`tests/test_check_mode_probe_safety.py`](../../tests/test_check_mode_probe_safety.py):
  repo-wide, it fails CI on any `command`/`shell` probe that is check-mode-reachable, marked
  `check_mode: false`, able to fail, and downstream of a runtime-service mutator
  (`docker_compose_v2`, `systemd_service`, `posix.mount`, …). It accepts all three real
  escapes — gate the task, gate the include, or tolerate the failure — so both siblings
  pass as-is rather than needing edits. Wired into `make test` (which enumerates files
  explicitly, so a new test that isn't added there never runs).
