"""Execute the actual probe: curl failures must not poison the whole textfile."""

import os
import re
import subprocess
from pathlib import Path

import jinja2
import pytest

TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "ansible/roles/homepage/templates/homepage-probe.sh.j2"
)
ROLES = TEMPLATE.parents[2]


@pytest.mark.parametrize(
    ("curl_status", "curl_output", "expected_code", "expected_up"),
    [
        (0, "200 0.024100", "200", "1"),
        (0, "503 0.012345", "503", "1"),
        (7, "000 0.000123", "000", "0"),
        (28, "000 5.000001", "000", "0"),
        (28, "200 5.000001", "000", "0"),
        (127, "", "000", "0"),
    ],
)
def test_probe_emits_valid_metrics_on_success_and_failure(
    tmp_path, curl_status, curl_output, expected_code, expected_up
):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    curl = bindir / "curl"
    curl.write_text('#!/bin/sh\nprintf "%s" "$CURL_OUTPUT"\nexit "$CURL_STATUS"\n')
    curl.chmod(0o755)
    metrics = tmp_path / "metrics"
    metrics.mkdir()
    script = tmp_path / "probe.sh"
    script.write_text(
        jinja2.Template(TEMPLATE.read_text(), undefined=jinja2.StrictUndefined).render(
            homepage_probe_host="127.0.0.1",
            homepage_probe_timeout=5,
            homepage_probe_textfile_dir=str(metrics),
            homepage_probe_schedule_minute="*/2",
            homepage_probe_targets=[{"name": "service", "port": 1234}],
        )
    )
    subprocess.run(  # noqa: S603 - repository template in an isolated test directory
        ["/bin/bash", str(script)],
        env={
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "CURL_OUTPUT": curl_output,
            "CURL_STATUS": str(curl_status),
        },
        check=True,
        timeout=10,
        capture_output=True,
        text=True,
    )
    result = (metrics / "homepage_probe.prom").read_text()
    for line in result.splitlines():
        if line and not line.startswith("#"):
            assert re.fullmatch(r'\w+(?:\{[^}]*\})? \d+(?:\.\d+)?', line), line
    assert f'homepage_probe_http_code{{target="service"}} {expected_code}\n' in result
    assert f'homepage_probe_up{{target="service"}} {expected_up}\n' in result
    assert len(list(metrics.iterdir())) == 1, "atomic publish left a temporary file"
