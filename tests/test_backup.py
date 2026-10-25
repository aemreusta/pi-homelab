"""Tests for the backup role's templated scripts (Sprint 10).

Strategy mirrors test_control_api.py: render each Jinja2 template with
stub vars into a temp file, then run `bash -n` on it for syntax. Plus
content-level checks: orchestrator references every configured service,
retention prune guards against /srv/backups itself, sqlite recipe runs
an integrity check before gzip.

We deliberately use Jinja2 directly (not Ansible) — Ansible needs
inventory + vault; Jinja2 just needs the template and a context dict.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import jinja2
except ImportError as exc:  # pragma: no cover
    raise SystemExit("jinja2 required for test_backup.py — pip install jinja2") from exc

REPO = Path(__file__).resolve().parents[1]
TPL_DIR = REPO / "ansible" / "roles" / "backup" / "templates"

# Minimal but realistic service set covering each method.
STUB_SERVICES = [
    {"name": "jellyfin", "method": "sqlite", "db_host_path": "/h/jellyfin.db"},
    {"name": "sonarr", "method": "sqlite", "db_host_path": "/h/sonarr.db"},
    {"name": "radarr", "method": "sqlite", "db_host_path": "/h/radarr.db"},
    {"name": "prowlarr", "method": "sqlite", "db_host_path": "/h/prowlarr.db"},
    {"name": "bazarr", "method": "sqlite", "db_host_path": "/h/bazarr.db"},
    {"name": "grafana", "method": "sqlite", "db_host_path": "/h/grafana.db"},
    {"name": "qbittorrent", "method": "stop_tar", "container": "qbittorrent", "paths": ["/h/qbit"]},
    {"name": "tdarr", "method": "stop_tar", "container": "tdarr", "paths": ["/h/tdarr/sql"]},
    {"name": "jellyseerr", "method": "stop_tar", "container": "jellyseerr", "paths": ["/h/jellyseerr/db"]},
    {"name": "adguard", "method": "tar", "paths": ["/h/adguard/conf"]},
    {"name": "postgresql-globals", "method": "pg_globals", "container": "postgresql"},
]

STUB_CONTEXT = {
    "backup_root": "/srv/backups",
    "backup_log": "/var/log/homelab-backup.log",
    "backup_state_file": "/var/lib/homelab/backup.state",
    "backup_ntfy_url": "http://ntfy.example:2586/homelab",
    "backup_control_url": "http://192.0.2.100:9099",
    "backup_control_token": "t-test",
    "backup_min_free_gb": 10,
    "backup_warn_free_gb": 30,
    "backup_retention_days": 28,
    "backup_require_mountpoint": True,
    "backup_services": STUB_SERVICES,
}


def render(name: str, **overrides) -> str:
    # autoescape disabled by design — these are shell scripts, not HTML.
    env = jinja2.Environment(  # noqa: S701
        loader=jinja2.FileSystemLoader(str(TPL_DIR)),
        keep_trailing_newline=True,
    )
    return env.get_template(name).render(**{**STUB_CONTEXT, **overrides})


def bash_syntax_check(content: str) -> tuple[int, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write(content)
        path = f.name
    try:
        # bash from $PATH on a path we just wrote — no user input involved.
        result = subprocess.run(  # noqa: S603
            ["bash", "-n", path],  # noqa: S607
            capture_output=True, text=True, check=False,
        )
        return result.returncode, result.stderr
    finally:
        os.unlink(path)


class TestBackupTemplatesParse(unittest.TestCase):
    """Every shell template must render to valid bash."""

    def _check(self, template: str):
        rc, err = bash_syntax_check(render(template))
        self.assertEqual(rc, 0, f"{template} failed bash -n: {err}")

    def test_orchestrator_parses(self):
        self._check("homelab-backup.sh.j2")

    def test_tar_recipe_parses(self):
        self._check("backup-tar.sh.j2")

    def test_stop_tar_recipe_parses(self):
        self._check("backup-stop_tar.sh.j2")

    def test_sqlite_recipe_parses(self):
        self._check("backup-sqlite.sh.j2")


class TestOrchestratorReferencesAllServices(unittest.TestCase):
    """Regression guard: a service silently dropped from the rendered
    orchestrator would never get backed up, with no error at deploy
    time. So we hard-assert each stubbed service name appears."""

    def test_all_services_referenced(self):
        rendered = render("homelab-backup.sh.j2")
        for svc in STUB_SERVICES:
            self.assertIn(
                f'"{svc["name"]}"', rendered,
                f"service {svc['name']} missing from rendered orchestrator",
            )



class TestBackupRootMountpointGuard(unittest.TestCase):
    """backup_root moved onto the second SSD (design 042 Phase 2) via a bind mount.

    That drive is on a marginal USB power budget and has dropped off the bus
    four times. When the bind is down, backup_root still EXISTS — as an empty
    directory on the root filesystem — so an unguarded run would quietly write
    5+ GiB onto the very disk the backup protects, which is already at 97%.
    Silent degradation, the exact class CLAUDE.md's silent-cron rule targets.
    """

    def test_guard_present_and_aborts_before_any_backup_work(self):
        content = render("homelab-backup.sh.j2")
        self.assertIn('mountpoint -q "$BACKUP_ROOT"', content)
        guard = content.index('mountpoint -q "$BACKUP_ROOT"')
        # Must precede the free-space probe: df on an unmounted path reports the
        # ROOT filesystem's free space, which would sail past the gate.
        self.assertLess(
            guard,
            content.index('df --output=avail "$BACKUP_ROOT"'),
            "mountpoint guard must run before the free-space pre-flight",
        )

    def test_guard_exits_nonzero_and_notifies(self):
        content = render("homelab-backup.sh.j2")
        block = content[content.index('mountpoint -q "$BACKUP_ROOT"'):][:900]
        self.assertIn("ntfy_send", block, "a silent abort is as bad as a silent write")
        self.assertIn("exit 2", block)
        self.assertIn("backup_root_not_mounted", block, "abort reason must be logged")

    def test_guard_is_opt_out(self):
        """Off => no guard, for the case where backup_root is back on /."""
        content = render("homelab-backup.sh.j2", backup_require_mountpoint=False)
        self.assertNotIn('mountpoint -q "$BACKUP_ROOT"', content)

    def test_guard_off_still_valid_bash(self):
        content = render("homelab-backup.sh.j2", backup_require_mountpoint=False)
        rc, err = bash_syntax_check(content)
        self.assertEqual(rc, 0, err)


class TestPostgresGlobalsAreBackedUp(unittest.TestCase):
    """`pg_dump <db>` does NOT include roles, passwords or grants.

    Without a globals dump the per-database snapshots are not restorable at
    all: every object references an owner that would not exist on a fresh
    cluster, and every service password would be gone. Found 2026-08-02 —
    five login roles existed and none were backed up.
    """

    def test_orchestrator_dispatches_pg_globals(self):
        content = render("homelab-backup.sh.j2")
        self.assertIn('run_service "postgresql-globals" "pg_globals"', content)

    def test_the_shipped_defaults_actually_include_a_globals_dump(self):
        """The fixture proving the template works is not proof we ship it.

        This asserts against the real defaults, so deleting the service entry
        fails the suite even though every template still renders.
        """
        import yaml
        defaults = yaml.safe_load(
            (REPO / "ansible/roles/backup/defaults/main.yml").read_text()
        )
        methods = [s["method"] for s in defaults["backup_services"]]
        self.assertIn(
            "pg_globals", methods,
            "no pg_globals service — the pg_dump snapshots are not restorable",
        )

    def test_globals_recipe_dumps_roles(self):
        content = render("backup-pg_globals.sh.j2")
        self.assertIn("pg_dumpall", content)
        self.assertIn("--globals-only", content)

    def test_globals_recipe_rejects_a_dump_without_roles(self):
        """pg_dumpall can exit 0 emitting only a header if pg_authid is
        unreadable. An empty globals dump is worse than none — it looks green.
        """
        content = render("backup-pg_globals.sh.j2")
        self.assertIn("CREATE ROLE", content)

    def test_globals_recipe_is_valid_bash(self):
        rc, err = bash_syntax_check(render("backup-pg_globals.sh.j2"))
        self.assertEqual(rc, 0, err)

    def test_globals_recipe_does_not_read_pipestatus_twice(self):
        """Reading PIPESTATUS[0] resets the array; the second index is then
        unbound and `set -u` aborts the script. This recipe failed its first
        live run exactly that way. pipefail already collapses the pipeline
        into $?, so indexing PIPESTATUS is both unnecessary and unsafe here.
        """
        content = render("backup-pg_globals.sh.j2")
        # Strip comments — the recipe documents the trap in prose, and the
        # guard is about executable code, not the explanation of it.
        code = "\n".join(
            ln for ln in content.splitlines() if not ln.lstrip().startswith("#")
        )
        self.assertNotIn("PIPESTATUS", code)
        self.assertIn("set -uo pipefail", code)


class TestRetentionPruneSafety(unittest.TestCase):
    """A `find ... -mtime +N -delete` without `-mindepth 2` could wipe
    the service-name directories themselves if their mtime drifted past
    the retention horizon. -mindepth 2 anchors the prune to the
    YYYY-MM-DD subdirs only."""

    def test_prune_has_mindepth_guard(self):
        rendered = render("homelab-backup.sh.j2")
        self.assertIn("-mindepth 2", rendered)
        self.assertIn("-maxdepth 2", rendered)

    def test_prune_uses_retention_days_var(self):
        # Substituted retention value should appear in the rendered find.
        rendered = render("homelab-backup.sh.j2")
        self.assertIn("28", rendered)


class TestSqliteRecipeIntegrity(unittest.TestCase):
    """The sqlite recipe must run a host-side integrity check on the
    .backup output BEFORE gzip, so a half-written file fails fast rather
    than producing a gzipped corpse the operator only discovers at
    restore time."""

    def test_integrity_check_before_gzip(self):
        import re
        rendered = render("backup-sqlite.sh.j2")
        schema_idx = rendered.find('".schema"')
        # Match the actual command invocation "gzip -f …", not the
        # substring appearing in error messages.
        m = re.search(r"^\s*gzip\s+-f\s", rendered, re.MULTILINE)
        self.assertGreater(schema_idx, -1, ".schema check missing")
        self.assertIsNotNone(m, "gzip -f step missing")
        gzip_idx = m.start()
        self.assertLess(
            schema_idx, gzip_idx,
            "integrity check must run BEFORE gzip (currently after)",
        )


class TestAtomicWritePattern(unittest.TestCase):
    """Each recipe must build into a `.partial.$$` filename and mv to the
    final path at the end. Writing to the final path directly means a
    same-day re-run (manual POST /backup, ad-hoc trigger) corrupts the
    previous snapshot mid-write — caught live on 2026-05-18."""

    def _assert_atomic(self, template: str):
        rendered = render(template)
        self.assertIn(".partial.", rendered, f"{template}: no .partial.$$ tmpfile")
        self.assertIn('mv -f', rendered, f"{template}: no atomic rename")
        # The mv must happen AFTER the integrity / write step. We can't
        # easily ascertain ordering without parsing, but at least assert
        # both tokens are present and the partial appears before mv.
        partial_idx = rendered.find(".partial.")
        mv_idx = rendered.find("mv -f")
        self.assertLess(partial_idx, mv_idx, f"{template}: partial declared after mv")

    def test_sqlite_recipe_atomic(self):
        self._assert_atomic("backup-sqlite.sh.j2")

    def test_tar_recipe_atomic(self):
        self._assert_atomic("backup-tar.sh.j2")

    def test_stop_tar_recipe_atomic(self):
        self._assert_atomic("backup-stop_tar.sh.j2")


class TestStopTarRecipeHasRestartTrap(unittest.TestCase):
    """A docker stop without a guaranteed-restart trap risks leaving qBit /
    Tdarr stopped indefinitely on any tar failure. The recipe must wire a
    trap before the stop."""

    def test_trap_present(self):
        rendered = render("backup-stop_tar.sh.j2")
        trap_idx = rendered.find("trap restart_container")
        stop_idx = rendered.find("docker stop")
        self.assertGreater(trap_idx, -1, "EXIT trap missing")
        self.assertGreater(stop_idx, -1, "docker stop missing")
        self.assertLess(trap_idx, stop_idx, "trap must be set BEFORE docker stop")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class TestBackupEmitsMetrics(unittest.TestCase):
    """CLAUDE.md hard rule #1: every backend cron emits a textfile counter.

    The local backup — the only FAST restore path on this box — violated it.
    It wrote a JSONL log and an ntfy push and nothing a machine could
    evaluate, so a total failure was detectable only by a human noticing
    the weekly push never arrived, or 30 days later via the indirect
    `GdriveBackupNoData` ceiling. Data loss is the one unrecoverable
    failure class, which makes this the highest-blast-radius instance of
    the silent-cron shape the rule exists to prevent.
    """

    def setUp(self):
        self.src = render("homelab-backup.sh.j2")

    def test_emits_on_every_exit_path_via_trap(self):
        self.assertIn("trap emit EXIT", self.src,
                      "metrics must be emitted on abort paths too, not just success")

    def test_writes_atomically(self):
        """Half-written .prom files are parsed by node_exporter as-is."""
        self.assertRegex(self.src, r"mv\s+\"?\$tmp\"?\s+\"?\$", )

    def test_exposes_the_gauges_an_alert_needs(self):
        for metric in (
            "backup_last_run_unix_seconds",
            "backup_success",
            "backup_duration_seconds",
            "backup_services_total",
            "backup_services_failed",
            "backup_bytes",
        ):
            self.assertIn(metric, self.src, f"{metric} missing from the emit")

    def test_abort_paths_set_success_zero(self):
        """The mountpoint / low-disk aborts must not leave yesterday's 1."""
        self.assertRegex(self.src, r"backup_success=0|success=0")


class TestBackupAlertRulesExist(unittest.TestCase):
    """A counter with no rule watching it is still silent — the exact
    lesson of the 2026-06-05 dedup-cron incident."""

    def setUp(self):
        path = (
            REPO / "ansible" / "roles" / "vmalert" / "templates" / "rules" / "backup.yml.j2"
        )
        self.text = path.read_text() if path.exists() else ""

    def test_rule_file_exists(self):
        self.assertTrue(self.text, "roles/vmalert/templates/rules/backup.yml.j2 missing")

    def test_has_stale_and_failing_rules(self):
        self.assertIn("BackupStale", self.text)
        self.assertIn("BackupFailing", self.text)

    def test_registered_in_vmalert_defaults(self):
        defaults = (
            REPO / "ansible" / "roles" / "vmalert" / "defaults" / "main.yml"
        ).read_text()
        # Anchored on the list-item form: a bare `in "backup.yml"` check
        # passes on the substring inside `gdrive-backup.yml`, which is how
        # this test first went green while the file was NOT registered.
        self.assertRegex(
            defaults, r"(?m)^\s*-\s+backup\.yml\b",
            "rule file must be listed in vmalert_metric_rule_files or it is "
            "silently never deployed",
        )
