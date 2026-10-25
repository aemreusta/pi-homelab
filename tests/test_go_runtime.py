"""Differential contracts and real Go/shell integration, without production I/O."""
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_gdrive_backup import render

ROOT = Path(__file__).resolve().parents[1]
# Production scripts require associative arrays; macOS still bundles Bash 3.2.
BASH = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").is_file() else shutil.which("bash")


@pytest.fixture(scope="session")
def go_agent(tmp_path_factory):
    binary = tmp_path_factory.mktemp("go-runtime") / "homelab-agent"
    subprocess.run(  # noqa: S603,S607 — fixed Go command over repository source
        [shutil.which("go"), "build", "-o", str(binary), "./cmd/homelab-agent"], cwd=ROOT / "runtime", check=True
    )
    return binary


@pytest.mark.parametrize("status", [
    {}, {"supervisor_heartbeat_ts": 1000, "jobs_total": 9007199254740993, "idle_count": True},
    {"repos": {"b": {"online": "2"}, "a": None}, "jit_config_failures": {"500": 2, "422": "1"}, "runner_version": "2.337.0"},
    {"repos": [1], "jit_config_failures": "bad", "idle_count": "bad", "jobs_total": 2.8},
])
def test_python_go_contract(go_agent, tmp_path, status):
    spec = importlib.util.spec_from_file_location(
        "runner_reference", ROOT / "ansible/roles/github_runner_metrics/files/gh_runner_metrics.py"
    )
    ref = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ref)
    source, hb, rebuild = [tmp_path / p for p in ("status", "heartbeat", "rebuild")]
    source.write_text(json.dumps(status))
    hb.write_text("999")
    rebuild.write_text(json.dumps({"last_attempt_ts": 123, "last_exit_code": 7}))
    args = [str(source), str(hb), "600", "1600", str(rebuild)]
    actual = subprocess.check_output(  # noqa: S603 — locally compiled test binary
        [str(go_agent), "runner-metrics", *args], text=True
    )
    expected = ref.render(str(source), str(hb), 600, 1600, str(rebuild))
    assert sorted(actual.splitlines()) == sorted(expected.splitlines())


def test_indexer_health_wrapper_publishes_failure_with_quoted_paths(go_agent, tmp_path):
    import shlex

    import jinja2

    directory = tmp_path / "private state with spaces"
    directory.mkdir()
    config = directory / "config.xml"
    config.write_text("<Config><ApiKey>fixture-secret</ApiKey></Config>")
    state, metrics = directory / "status.json", directory / "indexer_health.prom"
    env = jinja2.Environment(autoescape=False)  # noqa: S701 — shell, not HTML
    env.filters["quote"] = shlex.quote
    template = (ROOT / "ansible/roles/media/templates/indexer-health.sh.j2").read_text()
    script = tmp_path / "probe.sh"
    rendered = env.from_string(template).render(
        homelab_runtime_binary=str(go_agent),
        media_indexer_health_prowlarr_url="http://127.0.0.1:1",
        media_indexer_health_byparr_url="http://127.0.0.1:1",
        media_indexer_health_target_url="https://example.com/",
        media_indexer_health_config=str(config),
        media_indexer_health_state=str(state),
        media_indexer_health_textfile=str(metrics),
        media_indexer_health_timeout="1s",
    )
    # The test covers wrapper quoting/publication; Go's HTTP deadline is tested
    # with real servers. GNU timeout exists on the Pi, but not stock macOS.
    timeout = tmp_path / "timeout"
    timeout.write_text('#!/bin/sh\nshift\nexec "$@"\n')
    timeout.chmod(0o755)
    script.write_text(rendered.replace("/usr/bin/timeout", str(timeout)))
    completed = subprocess.run(  # noqa: S603 — isolated rendered fixture
        [BASH, str(script)], capture_output=True, text=True, timeout=5,
    )
    assert completed.returncode == 0, completed.stderr
    assert "fixture-secret" not in completed.stdout + completed.stderr
    observed = json.loads(state.read_text())
    assert observed["prowlarr_read_ok"] is False
    assert observed["browser_ok"] is False
    assert observed["checked_at"] > 0
    assert "indexer_health_browser_success 0\n" in metrics.read_text()
    assert "indexer_health_prowlarr_read_success 0\n" in metrics.read_text()


@pytest.fixture
def backup_run(go_agent, tmp_path):
    source, metrics, shims = [tmp_path / p for p in ("source", "metrics", "bin")]
    for p in (source, metrics, shims):
        p.mkdir()
    (source / "backup").write_text("test backup")
    rclone = shims / "rclone"
    rclone.write_text('''#!/bin/bash
case "$1" in
 sync) echo '{"level":"error","msg":"preserved diagnostic"}'
       echo '{"stats":{"bytes":42,"errors":1,"totalBytes":42}}'
       exit "${MOCK_RC:-0}";;
 about) echo '{"total": 100, "used": 20, "free": 80}';;
 size) echo '{"bytes":42,"count":1}';;
esac
''')
    flock = shims / "flock"
    flock.write_text('#!/bin/bash\nexit "${FLOCK_RC:-0}"\n')
    for p in (rclone, flock):
        p.chmod(0o755)
    script = tmp_path / "sync.sh"
    script.write_text(render("gdrive-sync.sh.j2", gdrive_backup_rclone_bin=str(rclone),
        gdrive_backup_source=str(source), gdrive_backup_textfile_dir=str(metrics),
        gdrive_backup_log=str(tmp_path / "summary.log"), gdrive_backup_detail_log=str(tmp_path / "detail.log"),
        gdrive_backup_last_success_file=str(tmp_path / "last-success"),
        gdrive_backup_lock_file=str(tmp_path / "lock"), gdrive_backup_ntfy_url="",
        gdrive_backup_go_binary=str(go_agent)))

    def run(**env):
        return subprocess.run(  # noqa: S603,S607 — rendered fixture, all side effects isolated
            [BASH, str(script)],
            env={**os.environ, "PATH": str(shims) + os.pathsep + os.environ["PATH"], **env},
            capture_output=True, text=True, timeout=10,
        )
    return run, tmp_path, metrics, source


def test_sync_failure_cannot_be_masked_and_last_success_survives(backup_run):
    run, path, metrics, _ = backup_run
    good = run()
    assert good.returncode == 0, good.stderr
    success = (path / "last-success").read_text()
    failed = run(MOCK_RC="7")
    assert failed.returncode == 7, failed.stderr
    assert (path / "last-success").read_text() == success
    text = (metrics / "gdrive_backup.prom").read_text()
    assert "gdrive_backup_success 0\n" in text
    assert "gdrive_backup_exit_code 7\n" in text
    assert "gdrive_backup_transferred_bytes 42\n" in text
    assert "preserved diagnostic" in (path / "detail.log").read_text()


def test_overlap_does_not_replace_metrics_and_empty_source_never_syncs(backup_run):
    run, path, metrics, source = backup_run
    current = metrics / "gdrive_backup.prom"
    current.write_text("previous\n")
    assert run(FLOCK_RC="1").returncode == 0
    assert current.read_text() == "previous\n"
    (source / "backup").unlink()
    assert run().returncode == 2
    assert not (path / "detail.log").exists()


def test_observer_failure_prevents_success(backup_run):
    run, path, metrics, _ = backup_run
    (path / "detail.log").mkdir()
    result = run()
    assert result.returncode != 0
    assert not (path / "last-success").exists()
    assert "gdrive_backup_success 0\n" in (metrics / "gdrive_backup.prom").read_text()
