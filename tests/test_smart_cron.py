"""Execute SMART monitoring with Debian user-cron's minimal PATH."""

import os
import subprocess
from pathlib import Path

import jinja2
import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / "ansible/roles/common/templates/smart-check.sh.j2"


@pytest.mark.parametrize("health", ["SMART Health Status: OK", "SMART overall-health self-assessment test result: PASSED"])
def test_smart_binary_outside_cron_path_still_provides_health(tmp_path, health):
    sbin = tmp_path / "sbin"
    sbin.mkdir()
    smartctl = sbin / "smartctl"
    smartctl.write_text('#!/bin/sh\ncase "$*" in *-H*) printf "%s\\n" "$SMART_HEALTH";; esac\n')
    smartctl.chmod(0o755)
    metrics = tmp_path / "metrics"
    metrics.mkdir()
    alert_lib = tmp_path / "alert-lib.sh"
    alert_lib.write_text('alert_event() { :; }\nntfy_send() { echo "Unexpected alert" >&2; exit 1; }\n')
    script = tmp_path / "smart-check.sh"
    source = TEMPLATE.read_text().replace("/usr/sbin/smartctl", str(smartctl))
    source = source.replace("/usr/local/lib/homelab/alert-lib.sh", str(alert_lib))
    script.write_text(jinja2.Template(source).render(
        ntfy_external_topic_url="http://127.0.0.1:1/unused",
        common_alert_state_dir=str(tmp_path / "state"),
        common_node_exporter_textfile_dir=str(metrics),
        common_smart_device_type="scsi",
    ))
    subprocess.run(  # noqa: S603 - real template with sandboxed SMART and notification helpers
        ["/bin/bash", str(script)], check=True, capture_output=True, timeout=5,
        env={**os.environ, "PATH": "/usr/bin:/bin", "SMART_HEALTH": health},
    )
    result = (metrics / "smart_check.prom").read_text()
    assert "smart_check_data_available 1\n" in result
    assert "smart_check_health_ok 1\n" in result
