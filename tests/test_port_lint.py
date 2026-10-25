"""A validation gate must reject unresolved ports and use inventory overrides."""

import importlib.util
from pathlib import Path

MODULE = Path(__file__).with_name("lint_ports.py")
spec = importlib.util.spec_from_file_location("port_lint", MODULE)
ports = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ports)


def setup_repo(tmp_path, monkeypatch, global_vars):
    role = tmp_path / "ansible/roles/example"
    (role / "templates").mkdir(parents=True)
    (role / "templates/docker-compose.yml.j2").write_text('services:\n  app:\n    ports:\n      - "{{ ntfy_port }}:80"\n')
    inventory = tmp_path / "ansible/inventory/group_vars/all"
    inventory.mkdir(parents=True)
    (inventory / "main.yml").write_text(global_vars)
    monkeypatch.setattr(ports, "ROOT", tmp_path)
    monkeypatch.setattr(ports, "ROLES", role.parent)


def test_unresolved_port_fails_gate(tmp_path, monkeypatch):
    setup_repo(tmp_path, monkeypatch, "")
    assert ports.main() == 1


def test_global_port_override_is_resolved(tmp_path, monkeypatch, capsys):
    setup_repo(tmp_path, monkeypatch, "ntfy_port: 2586\n")
    assert ports.main() == 0
    assert "Unresolved" not in capsys.readouterr().out
