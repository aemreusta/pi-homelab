#!/usr/bin/env python3
"""Decision logic for the Pi-side homelab GitOps CD controller.

Pure, parameter-free logic (CLAUDE.md: cron transition/diff/state logic lives in
a tested Python module, NOT bash). The bash orchestrator does the side effects
(git fetch/reset, GitHub CI-status query, `make` deploys, ntfy); this module owns
every DECISION:

  • needs_deploy()   — is origin ahead, and is its HEAD not a known-failed commit?
  • plan_targets()   — which `make` targets does a diff of changed files imply,
                       in dependency order (base roles first)?
  • ci_passed()      — did the commit's required CI checks pass?
  • on_success()/on_fail() — last-good / failed-sha state transitions.
  • render_metrics() — node_exporter textfile output.

Trigger model (TAG-GATED, mirrors the image-repo CI/deploy split): the orchestrator
passes the commit SHA the NEWEST `deploy-v*` tag points at (NOT origin/master HEAD)
as `origin_head`. A plain push to master runs CI only; deploy happens when a deploy
tag is cut. We key 'already handled' on the SHA we attempted, so a tag that fails +
rolls back is NOT retried every cycle while it stays newest — only when a newer
deploy tag appears. `origin_head` is thus "the deploy target SHA"; the decision math
below is agnostic to whether that SHA came from a branch HEAD or a tag.

Subcommands:
  needs-deploy <state.json> <origin_head>                                  -> exit 0 if deploy needed
  plan         <map.json> <base_csv> <allow_csv> <never_csv> <changed>...  -> deployable targets (one/line)
  plan-skipped <map.json> <base_csv> <allow_csv> <never_csv> <changed>...  -> targets held (allowlist or never_auto)
  rollback-targets <failed> <target>...                        -> restore set (one/line)
  ci-passed    <conclusion>                                -> exit 0 if 'success'
  on-attempt   <state.json> <sha>                          -> prints new state json
  on-success   <state.json> <sha> <now>                    -> prints new state json
  on-fail      <state.json> <sha> <now>                    -> prints new state json
  metrics      <state.json> <now> <last_run_ok>            -> prints prometheus text
"""

from __future__ import annotations

import json
import sys


def _load(path: str) -> dict:
    try:
        with open(path) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def needs_deploy(state: dict, origin_head: str) -> bool:
    """Deploy when origin's HEAD differs from the last SHA we attempted AND it is
    not the commit we already know failed (avoids a deploy→fail→reset→deploy loop
    while origin still points at the bad commit)."""
    head = (origin_head or "").strip()
    if not head:
        return False
    if head == state.get("failed_sha"):
        return False
    return head != state.get("seen_sha")


def on_attempt(state: dict, sha: str) -> dict:
    s = dict(state)
    s["seen_sha"] = (sha or "").strip()
    return s


def ci_passed(conclusion: str) -> bool:
    return (conclusion or "").strip().lower() == "success"


#: Files OUTSIDE ansible/ that are nevertheless baked INTO a role at deploy
#: time, and therefore imply that role must redeploy when they change.
#:
#: CHANGELOG.md -> homepage: the homepage role embeds the changelog via
#: lookup('file', ...) so the dashboard's version badge and changelog drawer
#: are a snapshot taken at deploy time. Without this mapping the badge goes
#: stale at exactly the worst moment — every release tag promotes the
#: CHANGELOG, and every release tag therefore failed to refresh the thing that
#: displays it. Observed 2026-08-02: deploy-v2026.08.02.3 shipped ten roles and
#: homepage was not among them, so the dashboard kept showing the previous
#: version.
EXTRA_FILE_TARGETS: dict[str, str] = {
    "CHANGELOG.md": "homepage",
    "scripts/go-source-id.py": "homelab-runtime",
}


def _role_to_target(role: str, role_target_map: dict, base_roles: set) -> str | None:
    """A role directory name → its `make` target. base roles collapse to 'base';
    an explicit map wins; otherwise the convention is underscores→hyphens. A role
    mapped to '' (or unknown with no convention target) is intentionally skipped."""
    if role in base_roles:
        return "base"
    if role in role_target_map:
        return role_target_map[role] or None
    return role.replace("_", "-")


def plan_targets(changed_files: list[str], role_target_map: dict, base_roles: set,
                 allowlist: set | list | None = None,
                 never_auto: set | list | None = None) -> list[str]:
    """Ordered, de-duplicated list of `make` targets implied by the changed files.

    'base' (common/security/storage/docker, or anything under base.yml) always
    runs FIRST. Runtime sources deploy homelab-runtime; otherwise ansible/ changes deploy; docs/mac-worker/tests/.github do
    not. A change to shared inventory (group_vars) is broad — but we deliberately
    do NOT fan out to every role for it (that's a full re-provision); instead it
    maps to no target here and the orchestrator treats a group_vars-only change
    as 'nothing auto-deployable' (surfaced via ntfy for a manual call).

    `allowlist` is the deploy gate (design 039 phased CD rollout). When it is
    non-empty, a target survives ONLY if it is in the allowlist; everything else
    is dropped from the auto-deploy plan and instead surfaced via plan_skipped()
    so the orchestrator can ntfy 'changed but held for manual deploy' — NOT
    silently marked done (silent-cron discipline). An EMPTY/None allowlist means
    'no gate' → every changed target auto-deploys (the full-rollout behaviour, and
    the shape the CD drill exercises).

    `never_auto` is a DENY-list applied AFTER the allowlist: a target in it is
    never auto-deployed no matter what the allowlist says (held for manual +
    surfaced by plan_skipped). It exists to keep `base`/host-provisioning off the
    CD even under a full 'no gate' rollout — the general controller only rolls
    back on an ansible non-zero exit, so a base change that SUCCEEDS but bricks
    the host (a ufw rule cutting SSH, a docker-daemon config killing the socket)
    would NOT auto-recover, and the CD runs on the Pi it just bricked. base stays
    a deliberate manual `make base`. Deny wins over allow by construction.
    """
    allow = set(allowlist or ())
    deny = set(never_auto or ())
    targets: list[str] = []
    saw_base = False
    for f in changed_files:
        f = f.strip().lstrip("./")
        if f in EXTRA_FILE_TARGETS:
            t = EXTRA_FILE_TARGETS[f]
            if t not in targets:
                targets.append(t)
            continue
        if f.startswith("runtime/"):
            if "homelab-runtime" not in targets:
                targets.append("homelab-runtime")
            continue
        if not f.startswith("ansible/"):
            continue
        parts = f.split("/")
        # ansible/roles/<role>/...
        if len(parts) >= 3 and parts[1] == "roles":
            t = _role_to_target(parts[2], role_target_map, base_roles)
        # ansible/playbooks/<name>.yml
        elif len(parts) >= 3 and parts[1] == "playbooks" and parts[2].endswith(".yml"):
            name = parts[2][:-4]
            # `verify-*` playbooks VERIFY live state; they do not deploy it, so
            # they are never a deploy target (not even a held one). Running one
            # as a target is doubly wrong: it isn't check-mode safe (its command
            # is skipped by the deep-check gate → the .stdout assert crashes),
            # and a verification that legitimately FAILS would roll back every
            # other role in the plan. Both bit on 2026-07-15.
            if name.startswith("verify-"):
                t = None
            else:
                t = "base" if name == "base" else name
        else:
            t = None  # inventory/group_vars/ansible.cfg/etc. — no single target
        if t == "base":
            saw_base = True
        elif t and t not in targets:
            targets.append(t)
    # homelab-runtime goes right after base: roles consume the installed
    # homelab-agent binary (some container entrypoints exec a subcommand), so a
    # tag that changes both the Go source and a consumer must install the new
    # binary first — diff order would otherwise deploy ansible/ before runtime/.
    if "homelab-runtime" in targets:
        targets = ["homelab-runtime"] + [t for t in targets if t != "homelab-runtime"]
    ordered = (["base"] if saw_base else []) + targets
    if allow:
        ordered = [t for t in ordered if t in allow]
    if deny:
        ordered = [t for t in ordered if t not in deny]
    return ordered


def plan_skipped(changed_files: list[str], role_target_map: dict, base_roles: set,
                 allowlist: set | list | None = None,
                 never_auto: set | list | None = None) -> list[str]:
    """Targets that a change touched but the CD withholds from auto-deploy — held
    by EITHER the allowlist gate OR the never_auto deny-list — i.e. what the
    operator must still `make` by hand. Empty only when everything the diff
    implies actually deploys.

    Ordered like the full plan and computed as (unfiltered plan) − (what
    plan_targets actually deploys) so it can never disagree with the plan. NOTE:
    with an empty allowlist but a non-empty never_auto (the full-rollout-except-
    base shape), this is NOT empty — base is correctly surfaced as held, never
    silently dropped (silent-cron discipline)."""
    deployed = set(plan_targets(changed_files, role_target_map, base_roles, allowlist, never_auto))
    full = plan_targets(changed_files, role_target_map, base_roles, None, None)
    return [t for t in full if t not in deployed]


def rollback_targets(all_targets: list[str], failed_target: str) -> list[str]:
    """The set of targets to re-deploy at last-good after a failed deploy.

    The deploy loop runs targets in dependency order and BREAKS at the first
    failure, so every target from the start up to AND INCLUDING the one that
    failed was *attempted*. The failed target ran some of its tasks before dying
    (an *arr API PUT, a compose recreate, a half-applied SQL migration), so its
    RUNTIME state is a partial mix — `git reset --hard last_good` restores FILES,
    not that already-applied runtime. It MUST therefore be re-deployed at
    last-good to reconverge. Targets AFTER the failure were never reached (still
    at last-good runtime) and are skipped.

    Excluding the failed target ("restore only the succeeded ones") is a load
    optimisation that silently leaves it half-migrated — precisely the CD
    corruption class this loop was born from (design 040 R4). When the failure
    point is unknown, restore the full set (never under-restore).
    """
    targets = [t for t in (all_targets or []) if t]
    failed = (failed_target or "").strip()
    if not failed or failed not in targets:
        return targets
    return targets[: targets.index(failed) + 1]


def on_success(state: dict, sha: str, now: int) -> dict:
    s = dict(state)
    s["last_good_sha"] = sha
    s["last_deploy_sha"] = sha
    s["last_deploy_ts"] = now
    s["last_deploy_result"] = "ok"
    s["failed_sha"] = ""
    return s


def on_fail(state: dict, sha: str, now: int) -> dict:
    s = dict(state)
    s["failed_sha"] = sha
    s["last_deploy_sha"] = sha
    s["last_deploy_ts"] = now
    s["last_deploy_result"] = "rolled_back"
    s["fail_count"] = int(state.get("fail_count", 0)) + 1
    return s


def _num(v: object) -> int:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def render_metrics(state: dict, now: int, last_run_ok: int) -> str:
    lines: list[str] = []

    def g(name: str, help_text: str, value: object, labels: str = "") -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name}{labels} {value}")

    result = state.get("last_deploy_result", "none")
    g("homelab_autodeploy_last_run_unix_seconds", "Unix time of the last controller run.", now)
    g("homelab_autodeploy_last_run_success", "1 if the last run completed without error.",
      1 if last_run_ok else 0)
    g("homelab_autodeploy_last_deploy_unix_seconds", "Unix time of the last deploy/rollback.",
      _num(state.get("last_deploy_ts")))
    g("homelab_autodeploy_fail_total", "Cumulative failed deploys that triggered a rollback.",
      _num(state.get("fail_count")))
    g("homelab_autodeploy_last_result_rolled_back", "1 if the most recent deploy rolled back.",
      1 if result == "rolled_back" else 0)
    # Held = a commit is being withheld (on_fail set failed_sha; on_success
    # clears it). last_result_rolled_back fires immediately (warn); the Held
    # alert keys off this gauge with a long `for:` to catch a commit stuck held
    # for hours — the silent-failure class where the loop never advances
    # last_good and nobody noticed (design 040 R4 / CLAUDE.md silent-cron rule).
    g("homelab_autodeploy_held", "1 while a failed commit is held (not yet superseded by a green deploy).",
      1 if str(state.get("failed_sha", "")).strip() else 0)
    sha = str(state.get("last_good_sha", "none")).replace('"', "")[:12] or "none"
    g("homelab_autodeploy_last_good_info", "Constant 1; labels the last-good commit SHA.", 1,
      f'{{sha="{sha}"}}')
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write("usage: homelab_autodeploy.py <subcommand> ...\n")
        return 2
    cmd = argv[1]
    if cmd == "needs-deploy":
        return 0 if needs_deploy(_load(argv[2]), argv[3]) else 1
    if cmd == "ci-passed":
        return 0 if ci_passed(argv[2]) else 1
    if cmd in ("plan", "plan-skipped"):
        # plan[-skipped] <map.json> <base_csv> <allowlist_csv> <never_auto_csv> <changed_file>...
        role_map = json.loads(argv[2]) if argv[2] else {}
        base = set(filter(None, (argv[3] or "").split(",")))
        allow = list(filter(None, (argv[4] or "").split(",")))
        never = list(filter(None, (argv[5] or "").split(",")))
        fn = plan_targets if cmd == "plan" else plan_skipped
        for t in fn(argv[6:], role_map, base, allow, never):
            sys.stdout.write(t + "\n")
        return 0
    if cmd == "rollback-targets":
        # rollback-targets <failed_target> <all_target>...
        for t in rollback_targets(argv[3:], argv[2]):
            sys.stdout.write(t + "\n")
        return 0
    if cmd == "on-attempt":
        sys.stdout.write(json.dumps(on_attempt(_load(argv[2]), argv[3]), indent=2) + "\n")
        return 0
    if cmd == "on-success":
        sys.stdout.write(json.dumps(on_success(_load(argv[2]), argv[3], _num(argv[4])), indent=2) + "\n")
        return 0
    if cmd == "on-fail":
        sys.stdout.write(json.dumps(on_fail(_load(argv[2]), argv[3], _num(argv[4])), indent=2) + "\n")
        return 0
    if cmd == "metrics":
        sys.stdout.write(render_metrics(_load(argv[2]), _num(argv[3]), _num(argv[4])))
        return 0
    sys.stderr.write(f"unknown subcommand: {cmd}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
