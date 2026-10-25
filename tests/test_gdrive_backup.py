"""Guard tests for the off-site Google Drive backup (roles/gdrive_backup).

This is the only backup copy that survives the Pi losing its disk — which on
this box is not hypothetical (five USB power outages 2026-08-01/02, see
docs/conventions/pi4-usb-power-budget.md). The failure modes that matter are
the quiet ones: a sync that stops running, or one that "succeeds" while
destroying what it was protecting.

Run: pytest tests/test_gdrive_backup.py -v
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
    raise SystemExit("jinja2 required — pip install jinja2") from exc

REPO = Path(__file__).resolve().parents[1]
TPL_DIR = REPO / "ansible" / "roles" / "gdrive_backup" / "templates"

CONTEXT = {
    "gdrive_backup_rclone_bin": "/usr/local/bin/rclone",
    "gdrive_backup_source": "/srv/backups",
    "gdrive_backup_remote": "gdrive:homelab/backups",
    "gdrive_backup_log": "/var/log/homelab-gdrive-sync.log",
    "gdrive_backup_ntfy_url": "http://ntfy.example.internal/homelab",
    "gdrive_backup_textfile_dir": "/var/lib/node_exporter/textfile",
    "gdrive_backup_config_path": "/root/.config/rclone/rclone.conf",
    "gdrive_backup_transfers": 4,
    "gdrive_backup_checkers": 8,
    "gdrive_backup_chunk_size": "64M",
    "gdrive_backup_tpslimit": 8,
    "gdrive_backup_bwlimit": "4M",
    "gdrive_backup_root_folder": "homelab",
    "vault_rclone_gdrive_token": '{"access_token":"x"}',
}


def render(name: str, **overrides) -> str:
    env = jinja2.Environment(  # noqa: S701 — shell/ini output, not HTML
        loader=jinja2.FileSystemLoader(str(TPL_DIR)),
        keep_trailing_newline=True,
        trim_blocks=True,
    )
    return env.get_template(name).render(**{**CONTEXT, **overrides})


class TestSyncScript(unittest.TestCase):
    def test_renders_to_valid_bash(self):
        content = render("gdrive-sync.sh.j2")
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            r = subprocess.run(  # noqa: S603
                ["bash", "-n", path],  # noqa: S607
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            os.unlink(path)

    def test_refuses_to_sync_an_empty_source(self):
        """THE important one.

        `rclone sync` mirrors deletions. If the local backup set is empty or
        its filesystem is unmounted, an unguarded sync DELETES the off-site
        copy — converting a local problem into the loss of the last surviving
        backup. Exactly what happened locally on 2026-08-02 when the ssd2 bind
        went away and /srv/backups became an empty directory.
        """
        content = render("gdrive-sync.sh.j2")
        self.assertIn("source_empty", content)
        guard = content.index('file_count=$(find "$SRC"')
        self.assertLess(
            guard,
            content.index('"$RCLONE" sync'),
            "empty-source guard must run BEFORE rclone sync",
        )

    def test_empty_source_aborts_loudly(self):
        content = render("gdrive-sync.sh.j2")
        block = content[content.index("source_empty") - 400:][:900]
        self.assertIn("ntfy_send", block, "a silent abort is as bad as a silent delete")
        self.assertIn("exit 2", block)

    def test_metrics_emitted_on_every_exit_path(self):
        """Silent-cron rule: a job that dies must not leave a stale green gauge."""
        content = render("gdrive-sync.sh.j2")
        self.assertIn("; emit\' EXIT", content)

    def test_metric_lines_are_valid_prometheus(self):
        content = render("gdrive-sync.sh.j2")
        emitted = [
            ln.strip().strip('"')
            for ln in content.splitlines()
            if ln.strip().startswith('echo "gdrive_backup_')
        ]
        self.assertTrue(emitted, "no gauge sample lines found")
        for ln in emitted:
            payload = ln[len('echo "'):] if ln.startswith('echo "') else ln
            name, _, value = payload.partition(" ")
            with self.subTest(line=ln):
                self.assertTrue(name.startswith("gdrive_backup_"))
                self.assertTrue(
                    value.startswith("$") or value.replace(".", "").isdigit(),
                    f"non-numeric metric value: {value!r}",
                )

    def test_every_gauge_has_help_and_type(self):
        content = render("gdrive-sync.sh.j2")
        for metric in (
            "gdrive_backup_last_run_unix_seconds",
            "gdrive_backup_success",
            "gdrive_backup_duration_seconds",
            "gdrive_backup_transferred_bytes",
            "gdrive_backup_errors",
        ):
            with self.subTest(metric=metric):
                self.assertIn(f"# HELP {metric} ", content)
                self.assertIn(f"# TYPE {metric} gauge", content)

    def test_uses_flock(self):
        content = render("gdrive-sync.sh.j2")
        self.assertIn("flock -n 9", content)

    def test_atomic_textfile_write(self):
        """node_exporter may scrape mid-write; a partial .prom sets scrape_error."""
        content = render("gdrive-sync.sh.j2")
        self.assertIn('mv "$tmp" "$tf"', content)
        self.assertIn('[ -s "$tmp" ]', content, "must not publish a 0-byte temp")


class TestDriveBestPractices(unittest.TestCase):
    def test_deletions_bypass_the_drive_trash(self):
        """--drive-use-trash=false is a quota correctness flag, not tuning.

        `sync` mirrors the weekly retention prune. With Drive's default
        trash behaviour those deletions keep consuming the 5 TB quota for
        another 30 days, so usage climbs forever while the tree looks right.
        """
        content = render("gdrive-sync.sh.j2")
        self.assertIn("--drive-use-trash=false", content)

    def test_upload_limit_aborts_cleanly(self):
        """Drive caps uploads at 750 GB/day; grinding retries just burn quota."""
        content = render("gdrive-sync.sh.j2")
        self.assertIn("--drive-stop-on-upload-limit", content)

    def test_quota_gauges_emitted(self):
        content = render("gdrive-sync.sh.j2")
        for m in ("gdrive_quota_bytes", "gdrive_remote_bytes", "gdrive_remote_objects"):
            with self.subTest(metric=m):
                self.assertIn(f"# HELP {m} ", content)
                self.assertIn(f"# TYPE {m} gauge", content)

    def test_quota_parse_tolerates_pretty_printed_json(self):
        """`rclone about --json` pretty-prints, `size --json` is compact.

        A pattern written for one returns nothing for the other. That exact
        mismatch shipped the quota gauges as permanent zeros on the first live
        run, which left GdriveQuotaHigh computing 0/0 — a rule that can never
        fire while looking healthy.
        """
        content = render("gdrive-sync.sh.j2")
        helper = content[content.index("json_num()"):].split("\n", 1)[0]
        self.assertIn("[[:space:]]*:[[:space:]]*", helper)

    def test_no_flag_renders_with_an_empty_value(self):
        """A flag whose Jinja var is missing renders as `--flag  \\` — which
        either silently swallows the next argument or fails at runtime. The
        template must never emit a bare flag expecting a value.
        """
        content = render("gdrive-sync.sh.j2")
        for flag in ("--drive-chunk-size", "--tpslimit", "--transfers",
                     "--checkers", "--bwlimit", "--retries"):
            line = next(
                (ln for ln in content.splitlines() if flag in ln), None
            )
            with self.subTest(flag=flag):
                self.assertIsNotNone(line, f"{flag} missing entirely")
                value = line.strip().removeprefix(flag).strip().rstrip("\\").strip()
                self.assertTrue(value, f"{flag} rendered with an empty value")

    def test_quota_probe_cannot_mask_the_sync_result(self):
        """`rclone about`/`size` run AFTER the sync and must not change rc."""
        content = render("gdrive-sync.sh.j2")
        self.assertLess(content.index('"$RCLONE" sync'), content.index('"$RCLONE" about'))
        self.assertLess(content.index("rc=${pipe_status[0]}"), content.index('"$RCLONE" about'))


class TestRcloneConfig(unittest.TestCase):
    def test_single_drive_remote_no_crypt(self):
        """Encryption was deliberately dropped (operator decision 2026-08-02).

        A stray crypt remote left behind would silently make the tree
        unbrowsable from the Drive UI, which is the whole point of the layout.
        """
        conf = render("rclone.conf.j2")
        self.assertIn("[gdrive]", conf)
        self.assertIn("type = drive", conf)
        self.assertNotIn("type = crypt", conf)

    def test_config_carries_the_token(self):
        conf = render("rclone.conf.j2")
        self.assertIn("token = ", conf)
        self.assertIn("scope = drive", conf)

    def test_sync_target_is_under_the_homelab_tree(self):
        """Backups must land in homelab/backups, never at the Drive root.

        `rclone sync` mirrors deletions — pointed at the account root it would
        delete everything else in the Drive.
        """
        content = render("gdrive-sync.sh.j2")
        self.assertIn('DST="gdrive:homelab/backups"', content)
        self.assertNotIn('DST="gdrive:"', content)

    def test_client_id_block_is_optional(self):
        """Falls back to rclone's shared client ID when none is supplied."""
        conf = render("rclone.conf.j2", vault_rclone_gdrive_client_id="")
        self.assertNotIn("client_id =", conf)
        conf = render(
            "rclone.conf.j2",
            vault_rclone_gdrive_client_id="cid",
            vault_rclone_gdrive_client_secret="csec",  # noqa: S106 — test fixture
        )
        self.assertIn("client_id = cid", conf)


if __name__ == "__main__":
    unittest.main()
