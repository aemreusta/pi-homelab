"""End-to-end CD drill for the homelab_autodeploy bash orchestrator.

Design 040 residual #3: the Python *decisions* (plan / rollback / held) are
unit-tested in ``test_homelab_autodeploy.py``, but the bash *orchestration* — the
``deploy_target`` retry loop, ``git reset`` rollback, ntfy fan-out, and the
held-state metric emit — was only ever exercised by a real production incident.
There was no CD drill the way there is a backup restore drill.

This is that drill. It renders ``homelab-autodeploy.sh.j2`` and runs it in a
hermetic sandbox: a real ``git`` bare-origin + working clone and the *real*
``homelab_autodeploy.py`` decision module, with mock ``ansible-playbook`` /
``curl`` / ``sleep`` / ``flock`` on a templated ``PATH``. It injects a mid-plan
apply failure and asserts the whole recovery contract:

  * the deploy loop breaks at the failed target,
  * rollback resets to last-good and re-deploys the ATTEMPTED targets —
    **including the one that failed** (the CD-corruption anti-regression, R4),
  * targets after the failure are never restored,
  * the held gauge is emitted as ``homelab_autodeploy_held 1``,
  * the operator gets the rollback + held ntfy.

Plus a happy path (all green → on-success, held 0, "all green").

Runs on stock macOS bash 3.2 and Linux CI alike: the orchestrator uses a
portable ``while read`` (not ``mapfile``) and ``flock`` is mocked, so no bash-4
or util-linux dependency.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "ansible/roles/homelab_autodeploy/templates/homelab-autodeploy.sh.j2"
MODULE = REPO / "ansible/roles/homelab_autodeploy/files/homelab_autodeploy.py"

try:
    import jinja2
    _HAVE_JINJA = True
except ImportError:  # pragma: no cover - jinja2 ships with ansible
    _HAVE_JINJA = False

_HAVE_GIT = shutil.which("git") is not None

# The three make-target roles the drill deploys, in the alphabetical order
# `git diff --name-only` yields (so the plan is deterministic: base-less,
# [adguard, beszel, tdarr]). The middle one is the injected failure.
TARGETS = ["adguard", "beszel", "tdarr"]
FAIL_TARGET = "beszel"


def _render(sandbox_bin: Path, repo_dir: Path, state: Path, textfile_dir: Path,
            log: Path, ntfy_url: str) -> str:
    # autoescape off by design — this renders a shell script, not HTML.
    env = jinja2.Environment(keep_trailing_newline=True)  # noqa: S701
    env.filters["to_json"] = json.dumps
    tmpl = env.from_string(TEMPLATE.read_text())
    # sandbox bin FIRST so mock ansible-playbook/curl/sleep/flock win, then the
    # real system dirs for git/python3/date/mv/rm.
    path = f"{sandbox_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}"
    return tmpl.render(
        homelab_autodeploy_repo_dir=str(repo_dir),
        homelab_autodeploy_branch="master",
        homelab_autodeploy_deploy_tag_glob="deploy-v*",
        homelab_autodeploy_state_file=str(state),
        homelab_autodeploy_bin_dir=str(MODULE.parent),
        homelab_autodeploy_role_target_map={},
        homelab_autodeploy_base_roles=["common", "security", "storage", "docker"],
        homelab_autodeploy_target_allowlist=[],  # no gate → drill exercises the full plan
        homelab_autodeploy_never_auto_targets=["base", "homelab-autodeploy"],  # held out (drill roles differ)

        homelab_autodeploy_ci_token="",           # no CI gate → skip the curl
        homelab_autodeploy_ci_repo="x/y",
        homelab_autodeploy_textfile_dir=str(textfile_dir),
        homelab_autodeploy_log_file=str(log),
        homelab_autodeploy_ntfy_url=ntfy_url,
        homelab_autodeploy_path=path,
    )


_MOCK_AP = """#!/bin/bash
# mock ansible-playbook (cwd = ANSIBLE_DIR). Pass syntax-check + --check always;
# fail the APPLY (neither --syntax-check nor --check) of $DRILL_FAIL_TARGET.
# By default fail ONLY when the role file carries the BROKEN marker — i.e. the
# NEW commit's code is broken but the reverted last-good code is fine, so a
# rollback restores cleanly (the real-incident shape). With DRILL_FAIL_ALWAYS=1
# the target is broken even at last-good → the rollback itself can't restore.
pb=""; for a in "$@"; do pb="$a"; done
base="$(basename "$pb" .yml)"
mode="apply"
for a in "$@"; do
  [ "$a" = "--syntax-check" ] && mode="syntax"
  [ "$a" = "--check" ] && mode="check"
done
echo "ap mode=$mode target=$base" >> "$DRILL_AP_LOG"
if [ "$mode" = "apply" ] && [ -n "${DRILL_FAIL_TARGET:-}" ] && [ "$base" = "$DRILL_FAIL_TARGET" ]; then
  if [ "${DRILL_FAIL_ALWAYS:-0}" = "1" ] || grep -q BROKEN "roles/$base/tasks/main.yml" 2>/dev/null; then
    echo "mock apply FAIL $base" >&2
    exit 1
  fi
fi
exit 0
"""

_MOCK_CURL = """#!/bin/bash
# mock curl: record the ntfy -d payload; never touch the network.
payload=""; prev=""
for a in "$@"; do [ "$prev" = "-d" ] && payload="$a"; prev="$a"; done
[ -n "$payload" ] && echo "$payload" >> "$DRILL_NTFY_LOG"
exit 0
"""

_MOCK_NOOP = "#!/bin/bash\nexit 0\n"  # sleep (skip backoff) + flock (no util-linux on mac)


def _mock(bindir: Path, name: str, body: str) -> None:
    p = bindir / name
    p.write_text(body)
    p.chmod(0o755)


def _isolated_env(**extra: str) -> dict[str, str]:
    """os.environ without inherited GIT_* variables, plus ``extra``.

    Inside a git hook (the pre-push suite run from a linked worktree), git
    exports GIT_DIR and friends. Inherited, they point every sandbox git call,
    and the rendered autodeploy script's rollback, at the operator's real
    repository instead of the temp one.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(extra)
    return env


def _git(cwd: Path, *args: str) -> str:
    env = _isolated_env(
        GIT_AUTHOR_NAME="drill", GIT_AUTHOR_EMAIL="drill@test",
        GIT_COMMITTER_NAME="drill", GIT_COMMITTER_EMAIL="drill@test",
    )
    return subprocess.run(  # noqa: S603
        ["git", *args], cwd=cwd, env=env, check=True,  # noqa: S607
        capture_output=True, text=True,
    ).stdout.strip()


@unittest.skipUnless(_HAVE_JINJA and _HAVE_GIT, "needs jinja2 + git")
class TestAutodeployDrill(unittest.TestCase):
    def _sandbox(self, tmp: Path, fail_target: str | None, fail_always: bool = False):
        """Build origin.git + a working clone with two commits (last_good →
        head), a mock bin dir, and a state file pinned at last_good. Returns
        (script_path, state, textfile_dir, ntfy_log, run_log, env, head_sha,
        base_sha)."""
        bindir = tmp / "bin"
        bindir.mkdir()
        _mock(bindir, "ansible-playbook", _MOCK_AP)
        _mock(bindir, "curl", _MOCK_CURL)
        _mock(bindir, "sleep", _MOCK_NOOP)
        _mock(bindir, "flock", _MOCK_NOOP)

        origin = tmp / "origin.git"
        _git(tmp, "init", "--bare", "-q", str(origin))
        repo = tmp / "work"
        _git(tmp, "clone", "-q", str(origin), str(repo))
        _git(repo, "checkout", "-q", "-B", "master")

        # last_good commit: full ansible skeleton (playbooks must EXIST for the
        # deploy_target file-guard) + role task files.
        (repo / "ansible/playbooks").mkdir(parents=True)
        for t in TARGETS:
            (repo / f"ansible/playbooks/{t}.yml").write_text(f"# {t} play\n")
            role = repo / f"ansible/roles/{t}/tasks"
            role.mkdir(parents=True)
            (role / "main.yml").write_text("# ok last-good\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "initial")
        _git(repo, "push", "-q", "-u", "origin", "master")
        base_sha = _git(repo, "rev-parse", "HEAD")

        # head commit: touch each role (BROKEN marker on the changed code) →
        # plan = [adguard, beszel, tdarr].
        for t in TARGETS:
            (repo / f"ansible/roles/{t}/tasks/main.yml").write_text("# BROKEN new code\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "second")
        _git(repo, "push", "-q", "origin", "master")
        head_sha = _git(repo, "rev-parse", "HEAD")

        # TAG-GATED CD: the controller deploys the commit the newest deploy-v*
        # tag points at, NOT origin/master HEAD. Cut an annotated deploy tag on
        # the head commit and push it so the drill has something to deploy.
        _git(repo, "tag", "-a", "deploy-v0.0.1", "-m", "drill deploy", head_sha)
        _git(repo, "push", "-q", "origin", "deploy-v0.0.1")

        # rewind the working checkout to last_good so the controller sees origin
        # ahead (as it would after a prior successful deploy).
        _git(repo, "reset", "--hard", "-q", base_sha)

        state = tmp / "state.json"
        state.write_text(json.dumps({"last_good_sha": base_sha, "seen_sha": base_sha}))
        textfile_dir = tmp / "textfile"
        textfile_dir.mkdir()
        ntfy_log = tmp / "ntfy.log"
        ntfy_log.write_text("")
        run_log = tmp / "run.log"
        ap_log = tmp / "ap.log"
        ap_log.write_text("")

        script = tmp / "autodeploy.sh"
        script.write_text(_render(bindir, repo, state, textfile_dir, run_log,
                                  f"file://{ntfy_log}"))
        script.chmod(0o755)

        env = _isolated_env(
            DRILL_AP_LOG=str(ap_log),
            DRILL_NTFY_LOG=str(ntfy_log),
        )
        if fail_target:
            env["DRILL_FAIL_TARGET"] = fail_target
        if fail_always:
            env["DRILL_FAIL_ALWAYS"] = "1"
        return script, state, textfile_dir, ntfy_log, run_log, ap_log, env, head_sha, base_sha

    def _run(self, tmp: Path, fail_target: str | None, fail_always: bool = False):
        (script, state, textfile_dir, ntfy_log, run_log, ap_log,
         env, head, base) = self._sandbox(tmp, fail_target, fail_always)
        subprocess.run(  # noqa: S603
            ["bash", str(script)], env=env, timeout=60,  # noqa: S607
            capture_output=True, text=True,
        )
        st = json.loads(state.read_text())
        prom = (textfile_dir / "homelab_autodeploy.prom").read_text()
        ntfy = ntfy_log.read_text()
        log = run_log.read_text()
        return st, prom, ntfy, log, head, base

    # ── failure path: the whole rollback→held→alert contract ────────────────
    def test_midplan_failure_rolls_back_holds_and_restores_failed_target(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            st, prom, ntfy, log, head, base = self._run(Path(d), FAIL_TARGET)

        # state: held at last_good, failed_sha = head, last_good UNCHANGED.
        self.assertEqual(st["failed_sha"], head)
        self.assertEqual(st["last_good_sha"], base)
        self.assertEqual(st["last_deploy_result"], "rolled_back")

        # the silent-failure backstop metric fired.
        self.assertIn("homelab_autodeploy_held 1", prom)
        self.assertIn("homelab_autodeploy_last_result_rolled_back 1", prom)

        # the deploy loop broke at the failed target.
        self.assertIn(f"deploy FAILED at target {FAIL_TARGET}", log)

        # R4 anti-regression: the RESTORE set is the ATTEMPTED targets —
        # adguard + beszel (the failure), NOT tdarr (never reached).
        self.assertIn("restoring: adguard", ntfy)
        self.assertIn(f"restoring: {FAIL_TARGET}", ntfy)
        self.assertNotIn("restoring: tdarr", ntfy)

        # operator was told.
        self.assertIn("rolling back", ntfy)
        self.assertIn("held", ntfy)

    # ── happy path: all green ───────────────────────────────────────────────
    def test_all_green_advances_last_good_and_clears_held(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            st, prom, ntfy, log, head, base = self._run(Path(d), None)

        self.assertEqual(st["last_good_sha"], head)
        self.assertEqual(st["last_deploy_result"], "ok")
        self.assertEqual(st.get("failed_sha", ""), "")
        self.assertIn("homelab_autodeploy_held 0", prom)
        self.assertIn("homelab_autodeploy_last_result_rolled_back 0", prom)
        self.assertIn("all green", ntfy)
        self.assertNotIn("rolling back", ntfy)

        # Noise budget (2026-09-29: one green deploy pushed 14 notifications).
        # A green deploy is start + result; per-step progress stays in the log.
        pushes = [line for line in ntfy.splitlines() if line.strip()]
        self.assertLessEqual(len(pushes), 2, pushes)
        self.assertTrue(any("deploying" in p and "targets" in p for p in pushes), pushes)
        self.assertNotIn("CI green", ntfy)
        self.assertNotIn("deploying: ", ntfy)
        self.assertIn("deploying: ", log)

    # ── rollback itself can't restore (target broken even at last-good) ──────
    def test_incomplete_rollback_raises_manual_fix_and_stays_held(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            st, prom, ntfy, log, head, base = self._run(Path(d), FAIL_TARGET, fail_always=True)

        # still recorded as held (failed_sha set, not advanced).
        self.assertEqual(st["failed_sha"], head)
        self.assertEqual(st["last_deploy_result"], "rolled_back")
        self.assertIn("homelab_autodeploy_held 1", prom)
        # the max-priority escalation the operator must not miss.
        self.assertIn("ROLLBACK INCOMPLETE", ntfy)
        self.assertIn("MANUAL FIX NEEDED", ntfy)
        self.assertIn(f"ROLLBACK FAILED at {FAIL_TARGET}", log)


if __name__ == "__main__":
    unittest.main()
