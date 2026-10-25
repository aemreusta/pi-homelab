# 2026-10-15 — release push blocked by a worktree-only hook env and pre-commit's checkout-wide save-and-restore

- **What happened:** Shipping `deploy-v2026.10.15.2` cost about 45 minutes, for three reasons.
  - At 22:00:05 every uncommitted tracked edit in the shared main checkout, covering two sessions' work, read as HEAD for several minutes. Git showed no reflog entry and no saved changes. It came back later.
  - The first push of the release chain from a clean `git worktree` failed its pre-push suite: three `test_autodeploy_drill` tests died on `git checkout -B master` with exit 128.
  - A concurrent session then pushed `a729f09` first, so the chain had to be replayed onto it.
- **Why:**
  - **The 22:00 revert** was pre-commit, not git. Another session ran `git push` from the shared checkout. Its pre-push hook set aside every unstaged change, all sessions' alike, as a patch in `~/.cache/pre-commit/patch1792090805-*` (51 KB, written 22:00:05), and restored them only after the ~3 min pytest run. Pre-commit-stage hooks during a commit do the same. Any session that reads or edits files in that window sees HEAD and can lose its own edits.
  - **The push failure:** git exports `GIT_DIR` to hooks only when the push runs from a linked worktree. The drill's sandbox git calls, and the rendered script's rollback, inherited `os.environ`, so they targeted the real repository. Pushing from the main checkout instead runs the suite against other sessions' untracked files, whose doc links do not resolve.
- **Class:** Concurrent agent sessions share one checkout and its git state, and tooling acts on the whole tree or inherits the caller's git environment. This is a new row for the class table.
- **Occurrence:** This is the first recorded instance. The parallel `.15.1` release appears to have hit the same push failure: its `/tmp/relpush` worktree was abandoned before `a729f09`.
- **Structural fix:**
  - Shipped in `a729f09` (`deploy-v2026.10.15.1`): the drill drops inherited `GIT_*` variables.
  - What worked in practice:
    - commit and push from a worktree or standalone clone; never run git hooks (commit or push) in a checkout holding someone else's unstaged edits;
    - push from a standalone clone with the pre-push hook installed;
    - move local `master` with `git update-ref refs/heads/master <new> <old>`, and sync only your own paths.
  - Deferred: a repo-wide guard that strips `GIT_*` from tests that shell out to git, and one shared-checkout rule for concurrent sessions in [ops-discipline](../operations/ops-discipline.md).
