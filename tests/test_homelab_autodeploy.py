"""Tests for the homelab_autodeploy GitOps CD decision module.

Module under test: ``ansible/roles/homelab_autodeploy/files/homelab_autodeploy.py``.

This decides what gets auto-deployed to the production Pi on every green master
push, so the diff→targets mapping, the CI-green gate, and the failed-SHA loop
guard are tested exhaustively — a wrong target list or a missed failed-commit
would hit the whole homelab.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest

MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "ansible", "roles", "homelab_autodeploy",
    "files", "homelab_autodeploy.py",
)


def _load():
    spec = importlib.util.spec_from_file_location("homelab_autodeploy", MODULE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["homelab_autodeploy"] = mod
    spec.loader.exec_module(mod)
    return mod


HA = _load()
BASE = {"common", "security", "storage", "docker"}
# real homelab divergences: role dir name != make target
ROLE_MAP = {"speedtest_tracker": "speedtest", "node_exporter": "node-exporter"}


class TestNeedsDeploy(unittest.TestCase):
    def test_origin_ahead_deploys(self):
        self.assertTrue(HA.needs_deploy({"seen_sha": "aaa"}, "bbb"))

    def test_same_head_no_deploy(self):
        self.assertFalse(HA.needs_deploy({"seen_sha": "aaa"}, "aaa"))

    def test_known_failed_not_retried(self):
        # The loop guard: a commit that failed + rolled back is not re-deployed
        # while origin still points at it.
        self.assertFalse(HA.needs_deploy({"seen_sha": "x", "failed_sha": "bad"}, "bad"))

    def test_empty_origin_no_deploy(self):
        self.assertFalse(HA.needs_deploy({"seen_sha": "aaa"}, ""))

    def test_first_run(self):
        self.assertTrue(HA.needs_deploy({}, "abc"))


class TestCiGate(unittest.TestCase):
    def test_success(self):
        self.assertTrue(HA.ci_passed("success"))
        self.assertTrue(HA.ci_passed("SUCCESS"))

    def test_not_success(self):
        for c in ("failure", "cancelled", "", "neutral", "timed_out", None):
            self.assertFalse(HA.ci_passed(c), c)


class TestPlanTargets(unittest.TestCase):
    def test_service_role_maps_to_target(self):
        out = HA.plan_targets(["ansible/roles/adguard/tasks/main.yml"], ROLE_MAP, BASE)
        self.assertEqual(out, ["adguard"])

    def test_role_map_override(self):
        out = HA.plan_targets(
            ["ansible/roles/speedtest_tracker/defaults/main.yml",
             "ansible/roles/node_exporter/tasks/main.yml"], ROLE_MAP, BASE)
        self.assertIn("speedtest", out)
        self.assertIn("node-exporter", out)

    def test_base_roles_collapse_and_lead(self):
        out = HA.plan_targets(
            ["ansible/roles/adguard/tasks/main.yml",
             "ansible/roles/common/tasks/swap.yml",
             "ansible/roles/security/tasks/main.yml"], ROLE_MAP, BASE)
        self.assertEqual(out[0], "base")          # base first
        self.assertEqual(out.count("base"), 1)    # deduped
        self.assertIn("adguard", out)

    def test_underscore_role_converts_to_hyphen(self):
        out = HA.plan_targets(["ansible/roles/github_runner_metrics/tasks/main.yml"], {}, BASE)
        self.assertEqual(out, ["github-runner-metrics"])

    def test_non_ansible_changes_skipped(self):
        out = HA.plan_targets(
            ["docs/archive/design/006-monitoring.md", "mac-worker/github-runner/compose.yml",
             "tests/test_x.py", ".github/workflows/ci.yml", "README.md"], ROLE_MAP, BASE)
        self.assertEqual(out, [])

    def test_playbook_change_maps(self):
        out = HA.plan_targets(["ansible/playbooks/traefik.yml"], ROLE_MAP, BASE)
        self.assertEqual(out, ["traefik"])

    def test_base_playbook_maps_to_base(self):
        out = HA.plan_targets(["ansible/playbooks/base.yml"], ROLE_MAP, BASE)
        self.assertEqual(out, ["base"])

    def test_verify_playbook_is_not_a_deploy_target(self):
        """`verify-*` playbooks VERIFY state — they do not deploy it, so the CD
        must never run one as a deploy target. Two ways this bites (both hit for
        real on 2026-07-15, rolling back the domain-migration tag):
          1. a verification is not check-mode safe (its `command` is skipped
             under the deep-check gate → the assert on `.stdout` crashes rc=2);
          2. worse, a verification that legitimately FAILS would roll back every
             other role in the plan — a red test must not revert a good deploy.
        """
        out = HA.plan_targets(["ansible/playbooks/verify-ntfy.yml"], ROLE_MAP, BASE)
        self.assertEqual(out, [])
        # and it must not be silently "held" either — it is simply not a target
        skipped = HA.plan_skipped(["ansible/playbooks/verify-ntfy.yml"], ROLE_MAP, BASE)
        self.assertEqual(skipped, [])

    def test_verify_playbook_does_not_poison_a_real_plan(self):
        out = HA.plan_targets(
            ["ansible/playbooks/verify-ntfy.yml", "ansible/roles/traefik/tasks/main.yml"],
            ROLE_MAP, BASE,
        )
        self.assertEqual(out, ["traefik"])

    def test_group_vars_change_no_target(self):
        # Shared inventory change is broad — we do NOT fan out to every role.
        out = HA.plan_targets(["ansible/inventory/group_vars/all/main.yml"], ROLE_MAP, BASE)
        self.assertEqual(out, [])

    def test_dedup_multiple_files_same_role(self):
        out = HA.plan_targets(
            ["ansible/roles/adguard/tasks/main.yml",
             "ansible/roles/adguard/defaults/main.yml",
             "ansible/roles/adguard/templates/x.j2"], ROLE_MAP, BASE)
        self.assertEqual(out, ["adguard"])


class TestExtraFileTargets(unittest.TestCase):
    """Files outside ansible/ that are baked into a role at deploy time.

    The homepage role embeds CHANGELOG.md via lookup('file', ...), so the
    dashboard version badge is a deploy-time snapshot. Because the planner
    only looked at paths under ansible/, every release tag promoted the
    CHANGELOG and simultaneously failed to redeploy the thing that shows it —
    the badge went stale at exactly the moment it mattered. Observed on
    deploy-v2026.08.02.3, which shipped ten roles without homepage.
    """

    def test_changelog_change_deploys_homepage(self):
        out = HA.plan_targets(["CHANGELOG.md"], ROLE_MAP, BASE)
        self.assertEqual(out, ["homepage"])

    def test_changelog_combines_with_role_changes(self):
        out = HA.plan_targets(
            ["CHANGELOG.md", "ansible/roles/adguard/tasks/main.yml"], ROLE_MAP, BASE)
        self.assertIn("homepage", out)
        self.assertIn("adguard", out)

    def test_no_duplicate_when_homepage_also_changed(self):
        out = HA.plan_targets(
            ["CHANGELOG.md", "ansible/roles/homepage/tasks/deploy.yml"],
            ROLE_MAP, BASE)
        self.assertEqual(out.count("homepage"), 1)

    def test_unrelated_root_files_still_ignored(self):
        """Only the explicit map triggers — not every repo-root file."""
        out = HA.plan_targets(
            ["README.md", "TODO.md", "Makefile"], ROLE_MAP, BASE)
        self.assertEqual(out, [])

    def test_extra_targets_respect_the_deny_list(self):
        """A never_auto target must not sneak in through this path."""
        out = HA.plan_targets(
            ["CHANGELOG.md"], ROLE_MAP, BASE, None, ["homepage"])
        self.assertEqual(out, [])


class TestPilotAllowlist(unittest.TestCase):
    CHANGED = ["ansible/roles/homepage/x.j2",      # allowed
               "ansible/roles/grafana/y.yml",       # allowed
               "ansible/roles/adguard/z.yml",       # held
               "ansible/roles/common/swap.yml"]     # → base, held

    def test_empty_allowlist_is_no_gate(self):
        # None and [] both mean "deploy everything" (pre-pilot behaviour).
        for allow in (None, [], set()):
            out = HA.plan_targets(self.CHANGED, ROLE_MAP, BASE, allow)
            self.assertEqual(out[0], "base")
            self.assertIn("adguard", out)
            self.assertIn("homepage", out)
            self.assertEqual(HA.plan_skipped(self.CHANGED, ROLE_MAP, BASE, allow), [])

    def test_allowlist_keeps_only_listed_targets(self):
        allow = ["homepage", "grafana", "vmagent"]
        out = HA.plan_targets(self.CHANGED, ROLE_MAP, BASE, allow)
        self.assertEqual(out, ["homepage", "grafana"])  # order preserved, base/adguard dropped
        self.assertNotIn("base", out)
        self.assertNotIn("adguard", out)

    def test_skipped_is_full_minus_allowed(self):
        allow = ["homepage", "grafana"]
        skipped = HA.plan_skipped(self.CHANGED, ROLE_MAP, BASE, allow)
        self.assertEqual(skipped, ["base", "adguard"])  # base first, then adguard

    def test_base_excluded_from_pilot_stays_held(self):
        # base is never in the pilot cohort → a common/security/storage/docker
        # change is held for manual deploy, not auto-applied.
        allow = ["homepage"]
        out = HA.plan_targets(["ansible/roles/security/tasks/main.yml"], ROLE_MAP, BASE, allow)
        self.assertEqual(out, [])
        self.assertEqual(
            HA.plan_skipped(["ansible/roles/security/tasks/main.yml"], ROLE_MAP, BASE, allow),
            ["base"])

    def test_allowlisted_change_alone_deploys_clean(self):
        allow = ["homepage", "grafana"]
        out = HA.plan_targets(["ansible/roles/homepage/x.j2"], ROLE_MAP, BASE, allow)
        self.assertEqual(out, ["homepage"])
        self.assertEqual(HA.plan_skipped(["ansible/roles/homepage/x.j2"], ROLE_MAP, BASE, allow), [])


class TestNeverAutoDenylist(unittest.TestCase):
    # Full-rollout shape: empty allowlist (no gate) + never_auto=["base"].
    CHANGED = ["ansible/roles/homepage/x.j2",
               "ansible/roles/adguard/z.yml",
               "ansible/roles/common/swap.yml"]     # → base, denied

    def test_full_rollout_deploys_all_but_base(self):
        out = HA.plan_targets(self.CHANGED, ROLE_MAP, BASE, [], ["base"])
        self.assertNotIn("base", out)
        self.assertIn("homepage", out)
        self.assertIn("adguard", out)

    def test_denied_base_is_surfaced_as_held_not_dropped(self):
        # base must appear in plan_skipped even under an empty allowlist —
        # silent-cron discipline: held, never silently marked done.
        skipped = HA.plan_skipped(self.CHANGED, ROLE_MAP, BASE, [], ["base"])
        self.assertEqual(skipped, ["base"])

    def test_deny_wins_over_allow(self):
        # even if base were somehow allow-listed, never_auto still withholds it.
        out = HA.plan_targets(["ansible/roles/common/swap.yml"], ROLE_MAP, BASE,
                              ["base", "homepage"], ["base"])
        self.assertEqual(out, [])
        self.assertEqual(
            HA.plan_skipped(["ansible/roles/common/swap.yml"], ROLE_MAP, BASE,
                            ["base", "homepage"], ["base"]),
            ["base"])

    def test_no_denylist_keeps_base(self):
        # empty never_auto → base deploys (the raw no-gate behaviour still works).
        out = HA.plan_targets(self.CHANGED, ROLE_MAP, BASE, [], [])
        self.assertEqual(out[0], "base")

    def test_controller_never_auto_deploys_itself(self):
        # 2026-07-14 incident: homelab-autodeploy must be held out so a rollback
        # can't redeploy the controller at last-good and self-revert mid-cycle.
        changed = ["ansible/roles/homelab_autodeploy/files/homelab_autodeploy.py",
                   "ansible/roles/homepage/x.j2"]
        deny = ["base", "homelab-autodeploy"]
        out = HA.plan_targets(changed, ROLE_MAP, BASE, [], deny)
        self.assertEqual(out, ["homepage"])  # controller change is NOT auto-deployed
        self.assertEqual(HA.plan_skipped(changed, ROLE_MAP, BASE, [], deny), ["homelab-autodeploy"])


class TestTransitions(unittest.TestCase):
    def test_on_success_sets_last_good_clears_failed(self):
        s0 = {"failed_sha": "old", "fail_count": 2}
        s1 = HA.on_success(s0, "good", 100)
        self.assertEqual(s1["last_good_sha"], "good")
        self.assertEqual(s1["failed_sha"], "")
        self.assertEqual(s1["last_deploy_result"], "ok")
        self.assertEqual(s1["fail_count"], 2)  # unchanged on success

    def test_on_fail_records_failed_and_counts(self):
        s0 = {"last_good_sha": "good", "fail_count": 1}
        s1 = HA.on_fail(s0, "bad", 200)
        self.assertEqual(s1["failed_sha"], "bad")
        self.assertEqual(s1["last_good_sha"], "good")  # untouched
        self.assertEqual(s1["last_deploy_result"], "rolled_back")
        self.assertEqual(s1["fail_count"], 2)

    def test_fail_then_no_retry(self):
        s = HA.on_fail({"last_good_sha": "good"}, "bad", 1)
        self.assertFalse(HA.needs_deploy(s, "bad"))      # bad won't redeploy
        self.assertTrue(HA.needs_deploy(s, "newer"))     # but a newer commit will


class TestMetrics(unittest.TestCase):
    def _parse(self, text):
        helped, typed, samples = set(), set(), {}
        for line in text.splitlines():
            if line.startswith("# HELP "):
                helped.add(line.split()[2])
            elif line.startswith("# TYPE "):
                typed.add(line.split()[2])
            elif line and not line.startswith("#"):
                samples.setdefault(line.split("{")[0].split(" ")[0], []).append(line)
        for n in samples:
            assert n in helped and n in typed, f"{n} missing HELP/TYPE"
        return samples

    def test_ok_metrics(self):
        s = {"last_deploy_result": "ok", "last_deploy_ts": 5, "fail_count": 0, "last_good_sha": "abc123def456"}
        sm = self._parse(HA.render_metrics(s, 100, 1))
        self.assertEqual(sm["homelab_autodeploy_last_result_rolled_back"],
                         ["homelab_autodeploy_last_result_rolled_back 0"])
        self.assertIn('homelab_autodeploy_last_good_info{sha="abc123def456"} 1',
                      HA.render_metrics(s, 100, 1))

    def test_rolled_back_metrics(self):
        s = {"last_deploy_result": "rolled_back", "fail_count": 4}
        out = HA.render_metrics(s, 100, 0)
        self.assertIn("homelab_autodeploy_last_result_rolled_back 1", out)
        self.assertIn("homelab_autodeploy_fail_total 4", out)
        self.assertIn("homelab_autodeploy_last_run_success 0", out)

    def test_empty_state_valid(self):
        self._parse(HA.render_metrics({}, 100, 1))

    def test_held_gauge_set_when_failed_sha_present(self):
        s = {"failed_sha": "bad", "last_deploy_result": "rolled_back"}
        out = HA.render_metrics(s, 100, 0)
        self.assertIn("homelab_autodeploy_held 1", out)

    def test_held_gauge_clear_on_success(self):
        # on_success clears failed_sha, so a subsequent green deploy un-holds.
        s = HA.on_success({"failed_sha": "bad", "fail_count": 1}, "good", 100)
        out = HA.render_metrics(s, 100, 1)
        self.assertIn("homelab_autodeploy_held 0", out)

    def test_held_gauge_absent_state_is_zero(self):
        self.assertIn("homelab_autodeploy_held 0", HA.render_metrics({}, 100, 1))


class TestRollbackTargets(unittest.TestCase):
    """The rollback restore-set. A wrong slice here is the CD-corruption class
    that bore this loop (design 040 R4): the failed target ran partial tasks
    before dying, so it MUST be re-deployed at last-good — never skipped."""

    def test_failed_in_middle_restores_through_failure_skips_unreached(self):
        # deploy loop breaks at the failed target; targets after it never ran.
        out = HA.rollback_targets(["base", "media", "traefik"], "media")
        self.assertEqual(out, ["base", "media"])

    def test_failed_target_is_always_included(self):
        # the anti-regression: the failed target is in the restore set.
        for plan, failed in (
            (["a", "b", "c"], "b"),
            (["a", "b", "c"], "a"),
            (["a", "b", "c"], "c"),
            (["only"], "only"),
        ):
            self.assertIn(failed, HA.rollback_targets(plan, failed), (plan, failed))

    def test_failed_last_restores_all(self):
        self.assertEqual(HA.rollback_targets(["a", "b", "c"], "c"), ["a", "b", "c"])

    def test_failed_first_restores_only_it(self):
        self.assertEqual(HA.rollback_targets(["a", "b", "c"], "a"), ["a"])

    def test_single_target(self):
        self.assertEqual(HA.rollback_targets(["media"], "media"), ["media"])

    def test_unknown_or_empty_failure_restores_all_superset(self):
        # never under-restore: if the failure point is unknown, restore everything.
        self.assertEqual(HA.rollback_targets(["a", "b"], ""), ["a", "b"])
        self.assertEqual(HA.rollback_targets(["a", "b"], "ghost"), ["a", "b"])

    def test_cli_rollback_targets(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = HA.main(["x", "rollback-targets", "b", "a", "b", "c"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().split(), ["a", "b"])


class TestCli(unittest.TestCase):
    def test_needs_deploy_exit(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            json.dump({"seen_sha": "a"}, open(p, "w"))
            self.assertEqual(HA.main(["x", "needs-deploy", p, "b"]), 0)
            self.assertEqual(HA.main(["x", "needs-deploy", p, "a"]), 1)

    def test_ci_passed_exit(self):
        self.assertEqual(HA.main(["x", "ci-passed", "success"]), 0)
        self.assertEqual(HA.main(["x", "ci-passed", "failure"]), 1)

    def test_plan_cli(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            # plan <map> <base_csv> <allow_csv> <never_csv> <changed>...  (empty allow+never = no gate)
            rc = HA.main(["x", "plan", json.dumps(ROLE_MAP), "common,security,storage,docker",
                          "", "", "ansible/roles/common/x", "ansible/roles/adguard/y"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().split(), ["base", "adguard"])

    def test_plan_cli_never_auto_holds_base(self):
        import contextlib
        import io
        # empty allowlist but never_auto="base" → adguard deploys, base held.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = HA.main(["x", "plan", json.dumps(ROLE_MAP), "common,security,storage,docker",
                          "", "base", "ansible/roles/common/x", "ansible/roles/adguard/y"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().split(), ["adguard"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = HA.main(["x", "plan-skipped", json.dumps(ROLE_MAP), "common,security,storage,docker",
                          "", "base", "ansible/roles/common/x", "ansible/roles/adguard/y"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().split(), ["base"])

    def test_plan_cli_allowlist_filters_and_skips(self):
        import contextlib
        import io
        changed = ["ansible/roles/homepage/x", "ansible/roles/adguard/y",
                   "ansible/roles/common/z"]
        allow = "homepage"  # base + adguard held
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = HA.main(["x", "plan", json.dumps(ROLE_MAP),
                          "common,security,storage,docker", allow, "", *changed])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().split(), ["homepage"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = HA.main(["x", "plan-skipped", json.dumps(ROLE_MAP),
                          "common,security,storage,docker", allow, "", *changed])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().split(), ["base", "adguard"])

    def test_bad_args(self):
        self.assertEqual(HA.main(["x"]), 2)
        self.assertEqual(HA.main(["x", "bogus"]), 2)


class TestRoleTargetMappingIntegrity(unittest.TestCase):
    """Every deployable role must map to an EXISTING playbook — else the CD
    silently skips it. `deploy_target()` returns 0 ("no playbook — skipping")
    when the derived target has no playbook, so a role whose playbook name
    differs from the underscore→hyphen convention (speedtest_tracker → the
    `speedtest` playbook, NOT `speedtest-tracker`) is dropped from every
    auto-deploy as a *false success*. This guards the REAL production
    `homelab_autodeploy_role_target_map` + `base_roles` (parsed from defaults),
    not a test fixture, so the gap can't reopen. 2026-07-14: speedtest_tracker's
    Phase 0 UFW change was silently skipped exactly this way.
    """

    def _repo(self, *p):
        return os.path.join(os.path.dirname(__file__), "..", *p)

    def test_every_deployable_role_maps_to_an_existing_playbook(self):
        import glob as _glob

        import yaml as _yaml

        defaults = self._repo("ansible", "roles", "homelab_autodeploy",
                              "defaults", "main.yml")
        with open(defaults, encoding="utf-8") as fh:
            d = _yaml.safe_load(fh)
        role_map = d.get("homelab_autodeploy_role_target_map") or {}
        base = set(d.get("homelab_autodeploy_base_roles") or [])
        missing = []
        for role_dir in sorted(_glob.glob(self._repo("ansible", "roles", "*"))):
            role = os.path.basename(role_dir)
            # Only roles with their own deploy.yml are deployed individually.
            if not os.path.isfile(os.path.join(role_dir, "tasks", "deploy.yml")):
                continue
            target = HA._role_to_target(role, role_map, base)
            if target in (None, "base"):
                continue  # base-collapsed or intentionally skipped
            if not os.path.isfile(self._repo("ansible", "playbooks", f"{target}.yml")):
                missing.append(
                    f"{role} → target '{target}' has no "
                    f"ansible/playbooks/{target}.yml (CD would silently skip it)"
                )
        self.assertEqual(
            missing, [],
            "roles the CD would silently skip — add an entry to "
            "homelab_autodeploy_role_target_map:\n" + "\n".join(missing),
        )


if __name__ == "__main__":
    unittest.main()


def test_runtime_sources_are_deployed_and_denylist_still_wins():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "ansible/roles/homelab_autodeploy/files/homelab_autodeploy.py"
    spec = spec_from_file_location("runtime_deploy_plan", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    paths = ["runtime/go.mod", "runtime/internal/runner/metrics.go", "scripts/go-source-id.py"]
    assert module.plan_targets(paths, {}, set()) == ["homelab-runtime"]
    assert module.plan_targets(paths, {}, set(), never_auto=["homelab-runtime"]) == []
    assert module.plan_skipped(paths, {}, set(), never_auto=["homelab-runtime"]) == ["homelab-runtime"]


def test_runtime_deploys_before_its_consumers():
    # 2026-09-29: a container entrypoint execs a `homelab-agent` subcommand. In
    # diff order ansible/ sorts before runtime/, which would recreate the container
    # against the OLD binary (no such subcommand → PID 1 exits → crash loop).
    paths = [
        "ansible/roles/media/templates/docker-compose.yml.j2",
        "ansible/roles/common/tasks/main.yml",
        "runtime/internal/watchdog/watchdog.go",
    ]
    assert HA.plan_targets(paths, {}, BASE) == ["base", "homelab-runtime", "media"]
