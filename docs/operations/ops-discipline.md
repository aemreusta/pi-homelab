# Operational discipline (cross-cutting)

Role-agnostic operating rules earned from deploy-time incidents. These apply to
**every** role, not any single service, which is why they live here rather than
in a service runbook.

Related: [docs/incidents/](../incidents) (the incident log this feeds),
[docs/conventions/vmalert-rule-quoting.md](../conventions/vmalert-rule-quoting.md).

---

## 1. Live-patch → commit reconciliation discipline

**Rule:** after any *live* edit on the Pi (a `sed`/`python`/manual rewrite of a
file that Ansible also templates), the **next** operator action is `git commit`
the matching template change **and** re-deploy via Ansible. Do not leave the
live file and the git template "matching in your head" and move on.

**Why.** During one deploy, a YAML-escape collision was hot-fixed
live on the Pi (so vmalert would stop crash-looping) before the git template was
updated. For roughly an hour the system was in a state where:

- the on-disk file was correct (hand-patched),
- the git template was *also* about to be correct, but
- any `make <role>` run by a second terminal / another operator during the
  window would have re-templated the file from git — overwriting the live patch
  with whatever git held at that moment, and
- a `--check` run would have shown 0 diffs only *after* the template caught up,
  giving false "nothing to deploy" confidence before then.

The divergence window was small but real. The fix is not "be careful" — it is a
**fixed sequence**:

1. Live-patch to stop the bleeding (vmalert/container crash-loop, etc.).
2. *Immediately* make the same change in the role template under
   `ansible/roles/<role>/...`.
3. `git commit` it.
4. Re-deploy with `make <role>` so live + git + applied state are provably one.

Only after step 4 is the loop closed. Until then, treat the host as *dirty* and
tell anyone else touching deploys.

**Verification that the loop closed:** `make <role>` (whose default gate now
runs a `--check --diff` against the live Pi before applying) reports the
templated files as `ok`, not `changed`. A `changed` on the file you
hand-patched means live and git had diverged.

---

## 2. Single-owner-per-file principle

**Rule:** when more than one role writes/touches the same host file, exactly one
role is the **authoritative owner** of that file's mode/owner. Every other role
that ensures-exists the file must (a) carry a comment pointing at the owner, and
(b) be idempotent (`changed_when: false` on a preserved-timestamp `touch`) so a
re-provision can't flip the mode or emit phantom `changed` churn.

**Why.** A file claimed by N independent code paths, each believing it is
authoritative, produces a "mode war": role A sets `0666`, role B (running later
in a full provision) resets it to `0640`, silently breaking writers until the
next deploy of A. This is invisible without a counter and only surfaces as
"alerts stopped appending after a base re-provision."

### Decision record: `/var/log/alert-audit.log` mode (2026-05-29)

**Context.** The audit log is appended to by three writers:

- the alertmanager **ntfy-bridge container** (runs as `nobody`/UID 65534),
- host-side crons running as `{{ docker_user }}` (UID 1000) — `daily-report.sh`,
  `junk-scan.sh` via `alert-lib.sh`,
- `root` (Ansible + root-owned cron alert scripts).

Two roles touch the file: `alertmanager` (`tasks/deploy.yml`) and `common`
(`tasks/monitoring.yml`, plus the logrotate `create` directive). There is **no**
standalone `logrotate` role — logrotate config is embedded in `common`. (The PM
review's "three roles" was two; the third was the embedded logrotate stanza.)

**Alternatives evaluated** (not a foregone conclusion):

| Option | Mode | Verdict |
| --- | --- | --- |
| (a) Run the ntfy-bridge container as `{{ docker_user }}` (PUID/PGID 1000), group-own the log `docker_user`, set `0664` | `0664` | **Cleaner Unix answer**, but requires changing the bridge container's runtime user in the compose template and re-validating it still works against its read-only rootfs. Bigger blast radius than this fix warrants; **deferred** as a follow-up (see incident log). |
| (b) Make alertmanager the *sole* writer; host crons emit via a shared logger alertmanager owns | n/a | Largest change; reroutes every cron's audit path. Rejected for now — over-engineering relative to the problem. |
| (c) Keep `0666` on a zero-secret audit log; designate one authoritative owner | `0666` | **Chosen.** The bridge runs as `nobody/65534`, which cannot be cleanly added to a host group, so a shared-group `0664` (option a) is not achievable without also changing the container user. The log holds zero secrets. Pairing `0666` with a single authoritative owner (below) removes the mode-war root cause without the risk of (a). |

**Decision.** `0666`, with **`alertmanager` as the authoritative owner** of the
mode (it runs the producer). `common`'s touch is a base-provisioning *floor*
(so crons can write before alertmanager is ever deployed) that explicitly
defers, is `changed_when: false`, and mirrors the mode. The logrotate `create`
mode mirrors it too. If the mode ever needs to change, change it in
`alertmanager` first, then mirror in `common`'s touch and logrotate `create`.

**Follow-up (deferred):** revisit option (a) if/when the ntfy-bridge container
is reworked — a `0664` + shared-group model is preferable to world-writable on
principle, even for a zero-secret log.

---

## 3. Deploy hygiene: stale SSH ControlPath sockets

`make` prunes `~/.ansible/cp/*` sockets idle >30 min before every deploy (the
`CP_CLEAN` step in the `DEPLOY_GATE` macro; also `make cp-clean` standalone).

**Why.** A stuck `ansible-playbook` can leave orphaned SSH control connections
that pile up past sshd's default `MaxStartups` (`10:30:100`) and transiently
throttle new SSH from the control node. This is **not** a fail2ban ban (verified
2026-05-29: the sshd jail showed 0 banned). See the incident log for the full
signature and the planned upgrade to `ssh -O check`-based wedge detection (the
current time-prune is a blunt instrument — a legitimate >30 min in-flight deploy
could lose its own socket and simply reconnect).

**Recurred 2026-05-29** during the follow-up deploy itself — slow fact-gathering
over Tailscale (host load ~4, 91% disk) kept the ansible connection alive while
retries + parallel verification `ssh` sessions pushed past `MaxStartups`. This is
now a **2nd-occurrence class**. Class-level mitigation beyond `cp-clean`: set
`gather_facts: false` (or `gather_subset: '!all,!min'`) on playbooks whose roles
use no facts (e.g. vmalert), so a slow `setup` can't wedge the deploy.

## 4. Plans and reviews can be wrong about codebase state — verify, then correct inline

**Rule:** a PM review, a plan, or a prior runbook can assert a *fact about the
codebase* that is wrong. Before implementing on top of such an assertion, verify
it against live state or current code. If it's wrong, correct it **in code with
a citation**, and note the correction inline so the next reader doesn't inherit
the wrong premise. No defending the assertion — just verify and update.

**Why.** This sprint's review asserted "`/srv` is a separate filesystem, point
the predict rule at `mountpoint="/srv"`." Live state said otherwise: `/dev/sda`
is mounted at `/` only and `/srv` is a directory on it, so `mountpoint="/srv"`
returns an empty series and the rule would never fire. Implementing the review
verbatim would have shipped a dead alert. Reading actual state caught it; the
correction is documented inline in `host-saturation.yml.j2` and runbook 037 §2.

The silent-cron rule and the cron-diff-Python rule were earned from *incidents*.
This one is earned from *process* — but it's the same severity class: an
unverified premise is as dangerous as an unobserved cron.

## 5. Fix a class, not an instance — sweep ALL occurrences

**Rule:** when a fix addresses a *pattern* (an anti-pattern, a stale string, a
deprecated idiom), grep the whole tree for that pattern in the SAME change and
fix every occurrence — not just the one that manifested. Name the sweep command
in the commit so the next person can re-run it.

**Why.** This has now bitten twice on the textfile-emitter ENOSPC pattern.
`0467623` fixed the `{ } > tmp && mv` short-circuit in *one* emitter and
stopped there; a later review caught *two* more — and a `grep` then found
**six** total. The fix-one-instance habit guarantees the same bug ships
N more times. The canonical sweeps that would have caught these in one pass:

```bash
# textfile emitters that drop their temp on ENOSPC:
grep -rnE '> *"\$(tmp|TMP)" *&& *mv' ansible/roles/*/templates/

# any cron/handler reusing a log-banner or status string the producer renamed:
#   grep the CONSUMER's match string against the PRODUCER's actual output —
#   they drift silently across refactors (one history parser kept matching a
#   start banner for ~2 weeks after the producer renamed it, parsing zero runs).
```

Same severity class as the others: a half-swept pattern is an unobserved cron in
waiting. When you fix a shape, fix the shape *everywhere*, and write the grep
down.

## 6. "Works locally, bricks under autodeploy" → diff the INVOCATION, not the version

**Rule:** when a change passes locally (`make <role>` from the Mac) but crashes
*only* under the Pi-side autodeploy, the **first** question is *"what is different
about how the CD invokes ansible?"* — **not** *"what ansible-core version differs,
should we pin?"*. The CD's `deploy_target` runs
`--syntax-check` → `--connection=local --check --diff` → apply. The `--check --diff`
pass was the differentiator when the Mac reachability probe silently skipped
the deep check. That probe was repaired on 2026-09-07; current `make <role>`
also runs syntax and deep checks by default. Enumerate the actual invocation
and skipped phases before reaching for a version pin.

**Why (the most expensive miss in this repo's history).** The 2026-07-02 brick
loop was `object of type 'dict' has no attribute 'json'`, reproducible under
autodeploy, invisible to the same `make <role>` from the Mac. Multiple sessions read
the divergence as *"Pi runs ansible-core 2.21 (strict), Mac runs 2.20.5
(lenient) → pin the Pi to 2.20.5"* and chased that red herring **to a production
failure**. The real cause was the CD's `--check --diff`: `ansible.builtin.uri` is
**skipped in check mode** → registers `{skipped: true}` with no `.json` → the
next `X.json` consumer crashes at arg-finalization. It reproduces on 2.20.5 too;
**pinning fixes nothing** and would have re-buried a live-config bug (a duplicate
POST instead of an idempotent PUT).

### The check-mode `uri`-skip class (recognise the Nth instance)

A `uri`/`command` result consumed as `X.json` in a task **arg** (`method:`/`url:`/
`body:`/`set_fact`/`loop:`) with no `'json' in X` guard will crash the moment its
producing GET is *skipped* — and a whole live-API config phase is skipped whenever
the play runs under `--check`. Two structural defences, both shipped:

- **Exempt the phase from check mode:** `when: ... and not ansible_check_mode` on
  live-API config includes (a 100%-`uri`/`command` phase validates *nothing* in
  check mode anyway). Applied to `beszel` and `jellyseerr`.
- **A CI guard that models check-mode reachability:**
  `tests/test_uri_check_mode_safety.py` walks the include graph from `main.yml`,
  prunes `not ansible_check_mode`-gated branches, and fails on any remaining
  `uri`→`.json` crash site. This *replaces* the deep-check coverage those exempt
  phases lost (see [deploy-deep-check.sh](../../scripts/deploy-deep-check.sh)
  header + [ops rule 4 above](#4-plans-and-reviews-can-be-wrong-about-codebase-state--verify-then-correct-inline)).

**Trade-off, accepted and recorded:** exempting the phase narrows the
`--check --diff` gate — an undefined var inside an exempt `configure.yml` now
fails at apply, not at the gate. The static guard test is the compensating
control; the boundary is documented in the deep-check header so the next reader
knows where check-mode coverage stops.

### Accepted residual: the CD deploys its own control logic (SPOF)

`homelab_autodeploy` renders and deploys **its own** rollback orchestrator
(`homelab-autodeploy.sh.j2` + `homelab_autodeploy.py`), so a bug in that template
can brick the CD mid-cycle — the same self-sabotaging-rollback class as the
incident. This is a **known, accepted** structural risk, bounded by three
controls, not eliminated:

1. `/usr/bin/ansible-playbook` is a create-only symlink → survives a rollback to
   a commit predating a PATH fix (so the rollback can still run ansible).
2. `homelab_autodeploy_held` gauge + `HomelabAutodeployHeld` alert (crit, 4h) →
   a stuck-held commit is **visible**, not silent (the failure mode that hid this
   incident for days). Recovery is still **manual** — the operator fixes + re-pushes.
3. `tests/test_autodeploy_drill.py` (`make cd-drill`) exercises the bash
   orchestrator end-to-end — deliberate mid-plan break → rollback → failed target
   restored → held → gauge `1` — so a regression in the rollback path is caught
   in CI, the way the backup restore drill proves recovery before it is needed.
