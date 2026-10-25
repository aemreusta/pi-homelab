"""Guard: no service role may pull a base role in through meta/main.yml.

Background (2026-09-05, deploy-v2026.09.05.1)
---------------------------------------------
The GitOps CD deny-lists ``base`` (``common``/``security``/``storage``/``docker``)
so host provisioning stays a deliberate manual ``make base``. A
successful-but-bricking base change will not auto-roll-back — rollback fires only
on a non-zero ansible exit — and the CD runs on the very Pi it would brick.

That guarantee was half true. The deny-list gates **playbook targets**, but a
role listed under ``dependencies:`` in ``meta/main.yml`` is not merely imported,
it is **executed**, before the role that names it. 35 service roles declared
``dependencies: [docker]`` (several also ``storage``, one ``common``), so the
docker role ran on essentially every service deploy. Observed live: the CD plan
for that tag was ``homepage media vmalert`` — correctly no ``base`` —
and the run still reported ``changed: [homelab]`` on the docker role's "Add
Docker GPG key" task.

Why the dependencies existed at all: to inherit one string, ``docker_user``,
which was defined in ``roles/docker/defaults``. Everything else those roles
consumed from base (``common_node_exporter_textfile_dir``) already carried a
``| default(...)`` and never needed the dependency. ``docker_user`` now lives in
``inventory/group_vars/all/main.yml``, which is where a value 36 roles read
belongs, and the dependencies are gone.

The contract that replaces them: **host provisioning is a prerequisite, not a
dependency.** ``make base`` runs ``common -> security -> storage -> docker``
first; service roles assume it has. A service deployed onto an unprovisioned
host fails loudly at the first ``community.docker.*`` task rather than silently
provisioning the host as a side effect.

This test keeps that true. A meta dependency is easy to add back and its blast
radius is invisible at the call site — the CD log is the only place it shows up,
and only if someone reads it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
ROLES_DIR = REPO / "ansible" / "roles"
PLAYBOOKS_DIR = REPO / "ansible" / "playbooks"

# The roles `make base` owns. Deploying any of these mutates host state that a
# failed service deploy cannot undo, which is why the CD refuses to run them.
BASE_ROLES = {"common", "security", "storage", "docker"}
# This entrypoint contains only the collector template and cron, not common/main.
COLLECTOR_ENTRYPOINTS = {"common:smart"}


def _meta_files() -> list[Path]:
    return sorted(ROLES_DIR.glob("*/meta/main.yml"))


def _declared_dependencies(meta_path: Path) -> list[str]:
    """Role names under ``dependencies:``, accepting both accepted spellings.

    Ansible allows ``- rolename`` and ``- role: rolename`` (plus a dict with
    extra params). Checking only one spelling is how a guard like this quietly
    stops guarding — the first sweep of this repo missed three roles that used
    the short form.
    """
    data = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
    deps = data.get("dependencies") or []
    names: list[str] = []
    for dep in deps:
        if isinstance(dep, str):
            names.append(dep)
        elif isinstance(dep, dict):
            name = dep.get("role") or dep.get("name")
            if name:
                names.append(str(name))
    return names


def test_meta_files_exist():
    """Sanity: the glob finds roles, so a passing suite means something."""
    assert len(_meta_files()) > 20


@pytest.mark.parametrize("meta_path", _meta_files(), ids=lambda p: p.parts[-3])
def test_no_service_role_depends_on_a_base_role(meta_path: Path):
    role = meta_path.parts[-3]
    if role in BASE_ROLES:
        return  # base roles may order themselves; base.yml is deny-listed anyway
    offending = sorted(set(_declared_dependencies(meta_path)) & BASE_ROLES)
    assert not offending, (
        f"role '{role}' declares meta dependencies on base role(s) {offending}. "
        "A meta dependency EXECUTES that role, so this makes every deploy of "
        f"'{role}' also run host provisioning — defeating the CD's `base` "
        "deny-list. Host provisioning is a prerequisite (`make base`), not a "
        "dependency. If you need a value from a base role, put it in "
        "inventory/group_vars/all/main.yml instead; if you need base to have "
        "run, assert the thing you actually need and fail with a clear message."
    )


def _playbook_calls(value) -> set[str]:
    """Include direct roles and task/playbook imports in the provisioning guard."""
    names = set()
    if isinstance(value, list):
        for item in value:
            names.update(_playbook_calls(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            short_key = key.rsplit(".", 1)[-1]
            if short_key == "roles" and isinstance(item, list):
                for role in item:
                    name = role if isinstance(role, str) else role.get("role", role.get("name", ""))
                    names.add(str(name).rsplit("/", 1)[-1].rsplit(".", 1)[-1])
            elif short_key in {"include_role", "import_role"} and isinstance(item, dict):
                name = str(item.get("name", "")).rsplit("/", 1)[-1].rsplit(".", 1)[-1]
                tasks_from = str(item.get("tasks_from", "main"))
                names.add(name if tasks_from == "main" else f"{name}:{tasks_from}")
            elif short_key == "import_playbook" and isinstance(item, str):
                names.add(Path(item).stem)
            names.update(_playbook_calls(item))
    return names


def _provisioning_calls(document) -> set[str]:
    return {
        name for name in _playbook_calls(document)
        if name.split(":", 1)[0] in (BASE_ROLES | {"base"})
        and name not in COLLECTOR_ENTRYPOINTS
    }


@pytest.mark.parametrize("playbook", sorted(PLAYBOOKS_DIR.glob("*.yml")), ids=lambda p: p.stem)
def test_service_playbooks_cannot_execute_host_provisioning(playbook: Path):
    if playbook.stem == "base":
        return
    calls = _provisioning_calls(yaml.safe_load(playbook.read_text()))
    assert not calls, (
        f"{playbook.name} bypasses the CD base deny-list via {sorted(calls)}. "
        "Host provisioning belongs only in the explicitly invoked base playbook."
    )


@pytest.mark.parametrize("document", [
    [{"roles": ["docker"]}],
    [{"roles": [{"role": "docker"}]}],
    [{"tasks": [{"block": [{"ansible.builtin.include_role": {"name": "docker"}}]}]}],
    [{"pre_tasks": [{"import_role": {"name": "docker"}}]}],
    [{"tasks": [{"include_role": {"name": "common", "tasks_from": "install"}}]}],
    [{"ansible.builtin.import_playbook": "./base.yml"}],
])
def test_playbook_guard_recognizes_provisioning_entrypoints(document):
    assert _provisioning_calls(document)


def test_smart_collector_exception_cannot_grow_into_host_provisioning():
    assert not _provisioning_calls([
        {"tasks": [{"include_role": {"name": "common", "tasks_from": "smart"}}]}]
    )
    tasks = yaml.safe_load((ROLES_DIR / "common/tasks/smart.yml").read_text())
    # Restrict the exception's actual contents, not just its filename.
    for task in tasks:
        actions = set(task) - {"name"}
        assert actions <= {"ansible.builtin.template", "ansible.builtin.cron"}
        assert len(actions) == 1
    assert not _declared_dependencies(ROLES_DIR / "common/meta/main.yml")


def test_docker_user_is_defined_in_group_vars_not_role_defaults():
    """The specific coupling that made 35 roles reach for a meta dependency."""
    group_vars = (
        REPO / "ansible" / "inventory" / "group_vars" / "all" / "main.yml"
    ).read_text(encoding="utf-8")
    assert "\ndocker_user:" in group_vars, (
        "docker_user must be defined in group_vars/all/main.yml — 36 roles read "
        "it, and defining it in roles/docker/defaults is what forced them to "
        "declare `dependencies: [docker]` and drag host provisioning into every "
        "service deploy."
    )

    docker_defaults = (
        ROLES_DIR / "docker" / "defaults" / "main.yml"
    ).read_text(encoding="utf-8")
    for line in docker_defaults.splitlines():
        assert not line.startswith("docker_user:"), (
            "docker_user is defined in roles/docker/defaults/main.yml as well as "
            "group_vars. Two sources of truth for a value 36 roles interpolate is "
            "exactly the drift this repo has paid for before — keep the group_vars "
            "definition and delete this one."
        )
