"""Legacy shell/Go differential fixtures and fail-closed command integration."""
import json
import os
import shlex
import subprocess
import time
from pathlib import Path

import jinja2
import pytest
import test_go_runtime
import yaml
from test_go_runtime import BASH, ROOT

go_agent = test_go_runtime.go_agent


def policy(directory):
    return {
        "common_watchdog_engine": "go",
        "common_watchdog_unhealthy_threshold": 3,
        "common_watchdog_max_restarts": 5,
        "common_watchdog_overbudget_alert_cooldown": 21600,
        "common_watchdog_state_dir": str(directory),
        "common_watchdog_ledger": str(directory / "ledger.json"),
        "common_watchdog_textfile": str(directory / "watchdog.prom"),
        "ntfy_external_topic_url": "http://fixture.invalid/topic",
    }


def shims(directory):
    directory.mkdir()
    docker = directory / "docker"
    docker.write_text('''#!/bin/bash
if [ "$1" = restart ]; then
    echo restart >> "$CALLS"
    exit "${RESTART_RC:-0}"
fi
if [ "${PS_RC:-0}" != 0 ]; then exit "$PS_RC"; fi
if [ "$2" = -a ]; then
    if [[ "$4" = *'|'* ]]; then
        if [ "${UNHEALTHY:-0}" = 1 ]; then echo 'service|running|unhealthy'; else echo 'service|running|healthy'; fi
    else echo service; fi
elif [ "${UNHEALTHY:-0}" = 1 ]; then echo service; fi
''')
    helper = directory / "notify"
    helper.write_text('#!/bin/bash\necho notify >> "$CALLS"\nexit "${NOTIFY_RC:-0}"\n')
    library = directory / "alert-lib.sh"
    library.write_text('alert_event() { :; }\nntfy_send() { echo notify >> "$CALLS"; return 0; }\n')
    for path in (docker, helper):
        path.chmod(0o755)
    return docker, helper, library


def run_go(binary, directory, docker, helper, environment):
    return subprocess.run(  # noqa: S603 — locally compiled binary and isolated shims
        [str(binary), "unhealthy-watchdog", "--docker", str(docker),
         "--notify-helper", str(helper), "--state-dir", str(directory),
         "--state", str(directory / "ledger.json"),
         "--textfile", str(directory / "watchdog.prom"),
         "--log", str(directory / "watchdog.log")],
        env=environment, capture_output=True, text=True, timeout=10,
    )


@pytest.mark.parametrize("state,bad,restart_rc", [
    ([0, 0, 0, 0], True, 0),
    ([2, 0, 0, 0], True, 0),
    ([2, 0, 0, 0], True, 1),
    ([8, 5, -100, 0], True, 0),
    ([8, 5, -100, -10], True, 0),
    ([2, 5, -86410, 0], True, 0),
    ([8, 5, -100, -10], False, 0),
])
def test_legacy_go_decision_parity(go_agent, tmp_path, state, bad, restart_rc):
    docker, helper, library = shims(tmp_path / "bin")
    now = int(time.time())
    initial = [value if value >= 0 else now + value for value in state]
    results, actions = [], []
    for engine in ("legacy", "go"):
        directory = tmp_path / engine
        directory.mkdir()
        sf = directory / "watchdog-service.state"
        sf.write_text("".join(f"{value}\n" for value in initial))
        calls = directory / "calls"
        environment = dict(os.environ, PATH=f"{docker.parent}:{os.environ['PATH']}",
                           CALLS=str(calls), UNHEALTHY=str(int(bad)), RESTART_RC=str(restart_rc))
        if engine == "legacy":
            env = jinja2.Environment(autoescape=False)  # noqa: S701
            template = (ROOT / "ansible/roles/common/templates/unhealthy-watchdog-legacy.sh.j2").read_text()
            rendered = env.from_string(template).render(**policy(directory))
            rendered = rendered.replace("/usr/local/lib/homelab/alert-lib.sh", str(library))
            rendered = rendered.replace(
                "LOG=/var/log/unhealthy-watchdog.log", f"LOG={shlex.quote(str(directory / 'watchdog.log'))}",
            )
            script = directory / "run.sh"
            script.write_text(rendered)
            completed = subprocess.run([BASH, str(script)], env=environment, capture_output=True, text=True, timeout=10)  # noqa: S603
        else:
            completed = run_go(go_agent, directory, docker, helper, environment)
        assert completed.returncode == 0, completed.stderr
        results.append([int(value) for value in sf.read_text().split()])
        actions.append(calls.read_text().splitlines() if calls.exists() else [])
    assert results[0][:2] == results[1][:2]
    # Wall-clock timestamps differ only by the two fixture execution times;
    # the Go failure-cooldown stamp is the documented bug-fix exception.
    assert abs(results[0][2] - results[1][2]) <= 3
    if restart_rc == 0:
        assert abs(results[0][3] - results[1][3]) <= 3
    assert actions[0] == actions[1]


def test_docker_error_is_not_an_empty_inventory(go_agent, tmp_path):
    docker, helper, _ = shims(tmp_path / "bin")
    directory = tmp_path / "state"
    directory.mkdir()
    orphan = directory / "watchdog-old.state"
    orphan.write_text("9\n5\n100\n100\n")
    completed = run_go(go_agent, directory, docker, helper,
                       dict(os.environ, CALLS=str(directory / "calls"), PS_RC="1"))
    assert completed.returncode != 0
    assert orphan.exists()
    result = json.loads((directory / "ledger.json").read_text())
    assert result["success"] is False
    assert result["docker_read_ok"] is False
    assert result["restart_attempts_total"] == 0


def test_wrapper_quotes_paths_and_keeps_explicit_rollback():
    env = jinja2.Environment(autoescape=False)  # noqa: S701
    env.filters["quote"] = shlex.quote
    template = (ROOT / "ansible/roles/common/templates/unhealthy-watchdog.sh.j2").read_text()
    values = policy(Path("/fixture/private state"))
    values["homelab_runtime_binary"] = "/fixture/runtime binary"
    rendered = env.from_string(template).render(**values)
    assert "'/fixture/runtime binary' unhealthy-watchdog" in rendered
    assert "--state-dir '/fixture/private state'" in rendered
    assert "flock -n" in rendered and "timeout 240" in rendered
    assert "unhealthy-watchdog-legacy.sh" not in rendered
    values["common_watchdog_engine"] = "legacy"
    rollback = env.from_string(template).render(**values)
    assert "unhealthy-watchdog-legacy.sh" in rollback
    assert "--state-dir" not in rollback


def test_legacy_rollback_disables_watchdog_absence_rules():
    env = jinja2.Environment(autoescape=False)  # noqa: S701
    template = (ROOT / "ansible/roles/vmalert/templates/rules/watchdog.yml.j2").read_text()
    assert yaml.safe_load(env.from_string(template).render(common_watchdog_engine="legacy")) == {"groups": []}
    enabled = yaml.safe_load(env.from_string(template).render(common_watchdog_engine="go"))
    assert {rule["alert"] for rule in enabled["groups"][0]["rules"]} == {
        "WatchdogStale", "WatchdogScanFailed", "WatchdogRestartFailed", "WatchdogRestartBudgetExhausted",
    }
