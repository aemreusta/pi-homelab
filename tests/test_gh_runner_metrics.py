"""Tests for the gh_runner_metrics renderer (design 039).

Module under test: ``ansible/roles/github_runner_metrics/files/gh_runner_metrics.py``.

It turns the Mac supervisor's NFS status.json + heartbeat into Prometheus
text. The load-bearing properties (CLAUDE.md cron-diff rule — the logic is in
Python, not bash, so it's testable here):
  • ALWAYS valid output, even with a missing/garbage status file (state, not
    silence): heartbeat_stale=1 + zeros;
  • correct staleness decision vs the threshold;
  • per-repo + per-status label series render correctly;
  • emitted text is well-formed Prometheus (every metric has HELP + TYPE).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest

MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "ansible",
    "roles",
    "github_runner_metrics",
    "files",
    "gh_runner_metrics.py",
)


def _load_module():
    spec = importlib.util.spec_from_file_location("gh_runner_metrics", MODULE_PATH)
    assert spec and spec.loader, "spec_from_file_location returned None"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gh_runner_metrics"] = mod
    spec.loader.exec_module(mod)
    return mod


GRM = _load_module()

NOW = 1_800_000_000  # fixed "now" so staleness math is deterministic

FULL_STATUS = {
    "supervisor_heartbeat_ts": NOW - 30,
    "idle_count": 1,
    "orphan_containers": 0,
    "jobs_total": 12,
    "jobs_failed_total": 2,
    "last_job_exit_code": 0,
    "github_api_auth_failures_total": 0,
    "jit_config_failures": {"422": 1},
    "jit_last_success_ts": NOW - 60,
    "github_api_rate_remaining": 4800,
    "image_build_timestamp": NOW - 100000,
    "runner_version": "2.328.0",
    "repos": {
        "raspi-homelab": {"registered": 1, "online": 1, "busy": 0, "offline": 0, "queued_jobs": 0, "idle": 1},
        "image-builds": {"registered": 1, "online": 1, "busy": 1, "offline": 0, "queued_jobs": 2, "idle": 0},
    },
}


def _write(tmpdir: str, name: str, content: str) -> str:
    path = os.path.join(tmpdir, name)
    with open(path, "w") as fh:
        fh.write(content)
    return path


def _parse_metrics(text: str) -> dict[str, list[str]]:
    """Return {metric_name: [sample_lines]} and validate HELP/TYPE coverage."""
    helped: set[str] = set()
    typed: set[str] = set()
    samples: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith("# HELP "):
            helped.add(line.split()[2])
        elif line.startswith("# TYPE "):
            typed.add(line.split()[2])
        elif line and not line.startswith("#"):
            name = line.split("{")[0].split(" ")[0]
            samples.setdefault(name, []).append(line)
    # Every emitted sample's metric must have HELP + TYPE.
    for name in samples:
        assert name in helped, f"{name} missing HELP"
        assert name in typed, f"{name} missing TYPE"
    return samples


class TestRenderFullStatus(unittest.TestCase):
    def test_fresh_heartbeat_not_stale(self):
        with tempfile.TemporaryDirectory() as d:
            sp = _write(d, "status.json", json.dumps(FULL_STATUS))
            hp = _write(d, "heartbeat", str(NOW - 30))
            out = GRM.render(sp, hp, 600, NOW)
            samples = _parse_metrics(out)
            self.assertEqual(samples["gh_runner_heartbeat_stale"], ["gh_runner_heartbeat_stale 0"])
            self.assertEqual(samples["gh_runner_idle_count"], ["gh_runner_idle_count 1"])
            self.assertEqual(samples["gh_runner_jobs_total"], ["gh_runner_jobs_total 12"])
            self.assertEqual(samples["gh_runner_jobs_failed_total"], ["gh_runner_jobs_failed_total 2"])

    def test_per_repo_series(self):
        with tempfile.TemporaryDirectory() as d:
            sp = _write(d, "status.json", json.dumps(FULL_STATUS))
            hp = _write(d, "heartbeat", str(NOW - 30))
            out = GRM.render(sp, hp, 600, NOW)
            self.assertIn('gh_runner_queued_jobs{repo="image-builds"} 2', out)
            self.assertIn('gh_runner_busy_count{repo="image-builds"} 1', out)
            self.assertIn('gh_runner_registered_count{repo="raspi-homelab"} 1', out)

    def test_jit_failures_and_version_labels(self):
        with tempfile.TemporaryDirectory() as d:
            sp = _write(d, "status.json", json.dumps(FULL_STATUS))
            hp = _write(d, "heartbeat", str(NOW - 30))
            out = GRM.render(sp, hp, 600, NOW)
            self.assertIn('gh_runner_jit_config_failures_total{status="422"} 1', out)
            self.assertIn('gh_runner_version_info{version="2.328.0"} 1', out)

    def test_last_run_is_now(self):
        with tempfile.TemporaryDirectory() as d:
            sp = _write(d, "status.json", json.dumps(FULL_STATUS))
            hp = _write(d, "heartbeat", str(NOW - 30))
            out = GRM.render(sp, hp, 600, NOW)
            self.assertIn(f"gh_runner_metrics_last_run_unix_seconds {NOW}", out)


class TestRebuildMetrics(unittest.TestCase):
    def test_rebuild_metrics_emitted_when_file_present(self):
        with tempfile.TemporaryDirectory() as d:
            sp = _write(d, "status.json", json.dumps(FULL_STATUS))
            hp = _write(d, "heartbeat", str(NOW - 30))
            rp = _write(d, "rebuild-status.json", json.dumps(
                {"last_attempt_ts": NOW - 100, "last_success_ts": NOW - 100, "last_exit_code": 0}))
            out = GRM.render(sp, hp, 600, NOW, rp)
            samples = _parse_metrics(out)  # also asserts HELP/TYPE coverage
            self.assertEqual(
                samples["gh_runner_rebuild_last_success_timestamp_seconds"],
                [f"gh_runner_rebuild_last_success_timestamp_seconds {NOW - 100}"])
            self.assertEqual(samples["gh_runner_rebuild_last_exit_code"],
                             ["gh_runner_rebuild_last_exit_code 0"])

    def test_rebuild_metrics_zero_when_file_absent(self):
        with tempfile.TemporaryDirectory() as d:
            sp = _write(d, "status.json", json.dumps(FULL_STATUS))
            hp = _write(d, "heartbeat", str(NOW - 30))
            out = GRM.render(sp, hp, 600, NOW)  # no rebuild path
            self.assertIn("gh_runner_rebuild_last_success_timestamp_seconds 0", out)
            self.assertIn("gh_runner_rebuild_last_exit_code 0", out)

    def test_rebuild_failure_exit_code_surfaced(self):
        with tempfile.TemporaryDirectory() as d:
            sp = _write(d, "status.json", json.dumps(FULL_STATUS))
            hp = _write(d, "heartbeat", str(NOW - 30))
            rp = _write(d, "rebuild-status.json", json.dumps(
                {"last_attempt_ts": NOW - 50, "last_success_ts": NOW - 9000, "last_exit_code": 1}))
            out = GRM.render(sp, hp, 600, NOW, rp)
            self.assertIn("gh_runner_rebuild_last_exit_code 1", out)


class TestStaleness(unittest.TestCase):
    def test_old_heartbeat_is_stale(self):
        with tempfile.TemporaryDirectory() as d:
            sp = _write(d, "status.json", json.dumps(FULL_STATUS))
            hp = _write(d, "heartbeat", str(NOW - 5000))  # > 600s threshold
            out = GRM.render(sp, hp, 600, NOW)
            self.assertIn("gh_runner_heartbeat_stale 1", out)

    def test_heartbeat_file_preferred_over_status_ts(self):
        # status says fresh, heartbeat file says old → file wins → stale.
        with tempfile.TemporaryDirectory() as d:
            sp = _write(d, "status.json", json.dumps(FULL_STATUS))
            hp = _write(d, "heartbeat", str(NOW - 5000))
            out = GRM.render(sp, hp, 600, NOW)
            self.assertIn("gh_runner_heartbeat_stale 1", out)


class TestMissingAndGarbage(unittest.TestCase):
    def test_missing_files_emit_valid_stale_output(self):
        with tempfile.TemporaryDirectory() as d:
            out = GRM.render(
                os.path.join(d, "nope.json"), os.path.join(d, "nohb"), 600, NOW
            )
            samples = _parse_metrics(out)  # must still be well-formed
            self.assertIn("gh_runner_heartbeat_stale 1", out)
            self.assertEqual(samples["gh_runner_idle_count"], ["gh_runner_idle_count 0"])
            self.assertIn('gh_runner_version_info{version="unknown"} 1', out)
            self.assertIn('gh_runner_jit_config_failures_total{status="none"} 0', out)
            self.assertIn('gh_runner_registered_count{repo="none"} 0', out)

    def test_garbage_json_treated_as_missing(self):
        with tempfile.TemporaryDirectory() as d:
            sp = _write(d, "status.json", "{not json at all")
            hp = _write(d, "heartbeat", str(NOW - 10))
            out = GRM.render(sp, hp, 600, NOW)
            _parse_metrics(out)
            # heartbeat file is fresh, so not stale even though status is garbage.
            self.assertIn("gh_runner_heartbeat_stale 0", out)
            self.assertEqual(
                _parse_metrics(out)["gh_runner_idle_count"], ["gh_runner_idle_count 0"]
            )

    def test_non_numeric_fields_coerce_to_zero(self):
        bad = dict(FULL_STATUS, idle_count="lots", jobs_total=None)
        with tempfile.TemporaryDirectory() as d:
            sp = _write(d, "status.json", json.dumps(bad))
            hp = _write(d, "heartbeat", str(NOW - 10))
            out = GRM.render(sp, hp, 600, NOW)
            self.assertIn("gh_runner_idle_count 0", out)
            self.assertIn("gh_runner_jobs_total 0", out)


class TestCli(unittest.TestCase):
    def test_main_bad_args(self):
        self.assertEqual(GRM.main(["prog"]), 2)

    def test_main_happy_path(self):
        with tempfile.TemporaryDirectory() as d:
            sp = _write(d, "status.json", json.dumps(FULL_STATUS))
            hp = _write(d, "heartbeat", str(NOW - 10))
            self.assertEqual(GRM.main(["prog", sp, hp, "600", str(NOW)]), 0)


if __name__ == "__main__":
    unittest.main()
