"""Every Mac LaunchAgent that owns a compose project must be a recovery loop.

Twice now a Mac compose project stayed down because its LaunchAgent only ran
at login: the worker compose project for 26 days (2026-08, fixed per-plist), then
the GitHub runner supervisor for 19h on 2026-09-28 — the same one-shot shape,
in a plist the first fix never touched. Guard the class, not the instance.
"""

import plistlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAC = ROOT / "mac-worker"
ENSURE = "mac-worker/ensure-compose.sh"


def _compose_agents():
    agents = []
    for path in sorted(MAC.rglob("*.plist")):
        data = plistlib.loads(path.read_bytes())
        argv = " ".join(data.get("ProgramArguments", []))
        if "compose" in argv or "ensure-compose" in argv:
            agents.append((path, data, argv))
    return agents


def test_compose_agents_found():
    labels = {data["Label"] for _, data, _ in _compose_agents()}
    assert {"io.homelab.mac-worker", "io.homelab.github-runner"} <= labels


@pytest.mark.parametrize("path,data,argv", _compose_agents(), ids=lambda v: getattr(v, "name", ""))
def test_compose_agent_is_periodic_and_uses_shared_ensure(path, data, argv):
    assert ENSURE in argv, f"{path.name} must run {ENSURE}, not an inline compose one-shot"
    assert "docker compose up" not in argv, f"{path.name} inlines compose instead of the tested script"
    assert data.get("RunAtLoad") is True
    assert 0 < data.get("StartInterval", 0) <= 900, f"{path.name} must re-assert at least every 15 min"
    assert data.get("KeepAlive") is False


def test_ensure_script_is_executable():
    assert (ROOT / ENSURE).stat().st_mode & 0o111
