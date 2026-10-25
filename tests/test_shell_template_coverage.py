"""Keep every shell template in the gate, including quoted Go delimiters."""

import importlib.util
import subprocess
from pathlib import Path

MODULE = Path(__file__).with_name("render_pipeline_scripts.py")
spec = importlib.util.spec_from_file_location("shell_renderer", MODULE)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def test_discovery_includes_every_template_with_unique_output_names():
    expected = set((renderer.REPO / "ansible/roles").glob("*/templates/*.sh.j2"))
    assert {renderer.REPO / path for path, _ in renderer.TEMPLATES} == expected
    assert len({name for _, name in renderer.TEMPLATES}) == len(expected)


def test_nested_quoted_go_delimiters_remain_valid_shell():
    source = (renderer.REPO / "ansible/roles/common/templates/unhealthy-watchdog-legacy.sh.j2").read_text()
    subprocess.run(  # noqa: S603 - shell syntax validation, no code execution
        ["/bin/bash", "-n"], input=renderer.strip_jinja(source), text=True,
        check=True, timeout=5, capture_output=True,
    )
