# 2026-05-29 — Tdarr-dedup deploy incident tail

The Tdarr watcher dedup sprint shipped cleanly, but the deploy itself produced
four operational incidents. Each is logged below in the five-field format. See
the PM-review follow-up work (this commit series) for the fixes.

---

## 1. YAML-escape / Go-template quote collision (vmalert) — **2nd occurrence**

- **What happened:** `tdarr-watcher.yml.j2` shipped with a double-quoted YAML
  annotation containing a rendered `printf "%.0f"`; vmalert-metrics restart-
  looped on deploy (`yaml: line 55: found character that cannot start any
  token`). Hot-fixed live, then committed (`86d1c25`).
- **Why:** the colliding `"` don't exist in the `.j2` source — they appear only
  *after* Jinja2 renders the escaped Go-template block. Review can't see it.
- **Class:** YAML-escape / Go-template quote collision in vmalert annotations.
- **Occurrence:** 2nd. 1st was `368e10a` (a pipeline alert-rule template, qbit-cleanup
  sprint) — confirmed identical shape via `git show`.
- **Structural fix:** **shipped** — `tests/test_vmalert_rules.py` renders every
  rule template and `yaml.safe_load`s it, with a pinned regression fixture for
  this exact broken annotation; wired into `make test`/`make validate`. Plus
  the [quoting convention doc](../conventions/vmalert-rule-quoting.md) so it's
  written right, not just caught. Jinja2 macro deferred to a hypothetical 3rd.

## 2. Stuck `ansible-playbook` + SSH ControlPath wedge — **1st occurrence**

- **What happened:** ~1h lost. The crash-looping vmalert deploy (incident #1)
  left a stuck/retrying playbook; new SSH to the Pi started being throttled.
  Recovered via manual `pkill -9` of orphaned ansible processes + `rm` of
  `~/.ansible/cp/*`.
- **Why:** **not** a configured parallel-SSH storm — verified: the repo has no
  `forks`/`serial`/`strategy` overrides, and `verify-vmalert.sh` runs *on* the
  Pi polling localhost via curl, not SSH fan-out. The actual mechanism: the
  stuck playbook held ansible's default ControlMaster socket alive
  (`~/.ansible/cp/`, ControlPersist), and incident-response habit (re-running
  `make`, opening SSH sessions to tail logs, verify scripts) layered several
  concurrent unauthenticated connections within sshd's default
  `MaxStartups 10:30:100` window → transient random connection drops. It
  recovered within ~30s of killing the orphans (connection count fell back
  below the throttle floor). **Confirmed NOT fail2ban** (2026-05-29: sshd jail
  showed `Currently banned: 0`, `Total banned: 0`) — so a future legitimate
  deploy from a flaky network is *not* at lockout risk.
- **Class:** stale SSH ControlPath socket pile-up vs `MaxStartups`.
- **Occurrence:** 1st.
- **Structural fix:** **shipped** — `make cp-clean` prunes `~/.ansible/cp/*`
  sockets idle >30 min, prepended to `DEPLOY_GATE` so it runs before every
  deploy ([ops-discipline §3](../operations/ops-discipline.md)). The *trigger*
  is also removed: the lint gate (incident #1) prevents the crash-loop deploy
  that wedged the playbook. **Deferred:** replace the blunt time-prune with
  `ssh -O check`-based wedge detection (a legit >30 min in-flight deploy could
  lose its own socket and reconnect). No ansible serialization needed — there
  is no fan-out to bound.

## 3. `alert-audit.log` multi-role mode war — **1st occurrence**

- **What happened:** the log's mode kept flipping between `0666` and `0640`
  across deploys, silently breaking non-root writers (the ntfy-bridge container
  and `docker_user` crons) until the next alertmanager deploy reset it.
- **Why:** two roles (`alertmanager` producer + `common` ensure-exist/logrotate)
  each set the mode, with no designated owner; `common` runs *after*
  `alertmanager` on a full provision and clobbered `0666→0640`.
- **Class:** multi-role file-ownership "mode war".
- **Occurrence:** 1st.
- **Structural fix:** **shipped** — single-owner-per-file principle:
  `alertmanager` is authoritative for the mode; `common`'s touch defers
  (`changed_when: false` + cross-ref comment), logrotate `create` mirrors. Full
  decision record (alternatives evaluated, why `0666` over shared-group `0664`)
  in [ops-discipline §2](../operations/ops-discipline.md). **Deferred:** revisit
  `0664` + shared group if the ntfy-bridge container is reworked to run as
  `docker_user`.

## 4. `monitoring.yml` left uncommitted at close-out — **1st occurrence**

- **What happened:** the `0640→0666` fix sat in the working tree uncommitted at
  close-out, creating a dirty-tree ambiguity for the next pull.
- **Why:** a live-patch was made and verified but not committed before the
  report was written.
- **Class:** live-patch not reconciled to git (working-tree drift).
- **Occurrence:** 1st (as a logged class).
- **Structural fix:** **shipped** — committed (`f183576`), and the
  live-patch → commit reconciliation discipline is now documented in
  [ops-discipline §1](../operations/ops-discipline.md).

## 5. SSH connection storm vs `MaxStartups` during the *follow-up* deploy — **2nd occurrence**

- **What happened:** while deploying *these very fixes*, `ansible-playbook`
  hung on `Gathering Facts` over Tailscale (>9 min, no progress). Retries +
  concurrent verification SSH sessions piled up and the Pi's sshd began
  refusing connections (`Connection closed by … port 22`). ~30 min lost; killed
  orphans + `cp-clean` + a backoff-and-probe loop; then deployed surgically over
  single SSH sessions.
- **Why:** the Pi's fact-gathering is pathologically slow right now (host load
  ~4, 91% disk). Slow facts → long-lived ansible connection → my retries and
  parallel `ssh` checks pushed concurrent unauthenticated connections past sshd
  `MaxStartups 10:30:100`. Same class as incident #2 (stale/excess control
  connections vs `MaxStartups`), here triggered by slow facts rather than a
  stuck `docker_compose` task. Confirmed **not** fail2ban (0 banned).
- **Class:** SSH connection storm vs `MaxStartups`.
- **Occurrence:** **2nd** (incident #2 above was the 1st). Now a recurring class.
- **Structural fix:** `make cp-clean` (shipped) removes the socket residue.
  **The trigger this time was slow fact-gathering**, so the class-level fix is
  ansible-side: set `gather_facts: false` (or `gather_subset: '!all,!min'`) on
  playbooks whose roles don't use facts (vmalert uses none), and/or raise the
  SSH/`gather_timeout` so a slow gather fails fast instead of wedging. **Pinned
  as a Sprint N+1 follow-up** (not shipped tonight — would need a deploy over the
  same degraded link). Operational rule already in
  [ops-discipline §3](../operations/ops-discipline.md).

## Positive validation — the lint test caught its author's own bug pre-merge

Not an incident — **evidence the meta-process works**. While adding
`SrvDiskFillRateHigh`, I wrote its `description:` with a double-quoted-outer YAML
scalar containing a rendered `printf "%.3f"` — the **exact** incident-#2 shape.
`tests/test_vmalert_rules.py` failed immediately on render→parse and I fixed it
to the single-quoted-outer canonical form before commit.

- **Class:** vmalert annotation quote collision.
- **Occurrence:** **3rd in class, 1st caught pre-merge by a structural fix**
  (368e10a and 86d1c25 both shipped to prod before detection).
- **Takeaway:** the YAML-render lint gate paid for itself inside the same sprint
  that created it. This is the positive counterpart to the incident reports — the
  log records both what bit us and what the structural fixes prevented.

## Open follow-ups (must close within 48h, per close-out review)

- **Convergence test (≤5 min, when the LAN/Tailscale link is healthy):**
  `make vmalert && make base` must both report **zero changes** — proving the
  surgical live state matches the committed templates. If either reports changes,
  live drifted from git and needs reconciliation (live-patch discipline,
  ops-discipline §1).
- **`RetentionNeverReclaims` vmalert rule (Sprint N+1):** the retention cron
  emits `retention_cleanup_*` metrics but has no alert on a stuck/silent-zero
  retention. `increase(retention_cleanup_last_run_unix_seconds[…])`-style
  staleness + a long-horizon "never reclaims" check would apply the silent-cron
  principle to retention itself. See [runbook 038](../runbooks/038-2026-05-29-retention-cleanup.md).
