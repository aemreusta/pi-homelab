"""Repo-wide guard against the check-mode *stale-precondition probe* class.

Sibling of ``test_uri_check_mode_safety.py``. Same root cause — the CD gates
every deploy on ``ansible-playbook --check --diff`` BEFORE apply, so anything
that fails under ``--check`` bricks the role's next deploy — but a different
accessor, and the ``uri``/``.json`` guard is blind to it.

The shape (RCA 2026-08-04, cost the ``deploy-v2026.08.04.1`` tag a rollback)::

    - name: Stop FileBrowser            # docker_compose_v2 — under --check this
      community.docker.docker_compose_v2  # is a NO-OP that still reports `changed`
        state: stopped

    - name: Read current auth method    # ...but `check_mode: false` means THIS
      ansible.builtin.command: ...      # really runs, against a container that
      check_mode: false                 # is therefore STILL RUNNING and still
                                        # holding the database lock -> rc=1

``check_mode: false`` on a read-only probe is a good pattern and this guard does
not object to it. What it objects to is a probe whose **precondition is
established by a task check mode skips**: the probe then runs against live state
the play believes it has already changed, and a failure there is indistinguishable
from a real one. Three ways out, any one is accepted here:

  1. gate the probe (or an enclosing ``block``) on ``not ansible_check_mode``
     — what filebrowser now does;
  2. gate the whole include on ``not ansible_check_mode``  — what tdarr's
     ``configure.yml`` already did, which is why it never bit;
  3. make the probe non-fatal with ``failed_when: false`` / ``ignore_errors``
     — what ``storage/ssd2.yml`` already did, which is why it never bit.

Only *runtime service state* mutators count as preconditions. A ``template`` or
``file`` task ahead of a probe is not this class: it changes file content, not
whether the probe can run at all.
"""

import os
import re
from collections.abc import Mapping
from glob import glob

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROLES_DIR = os.path.join(REPO_ROOT, "ansible", "roles")

# Modules that change RUNTIME SERVICE STATE and are a no-op under `--check`
# while still reporting `changed`. These are the ones that can leave a later
# probe reasoning about state the play never actually reached.
_STATE_MUTATORS = {
    "community.docker.docker_compose_v2",
    "community.docker.docker_container",
    "ansible.builtin.systemd_service",
    "ansible.builtin.systemd",
    "ansible.builtin.service",
    "ansible.posix.mount",
}
# Probes only matter if they can FAIL the play. `command`/`shell` are the ones
# that run a real binary against real state.
_PROBE_MODULES = {
    "ansible.builtin.command",
    "ansible.builtin.shell",
    "command",
    "shell",
}
_CHECK_GATE = re.compile(r"not\s+ansible_check_mode")


def _stringify(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    import json as _json

    return _json.dumps(value, default=str)


def _module_name(task):
    for key in task:
        if key in _STATE_MUTATORS or key in _PROBE_MODULES:
            return key
    return None


def _load_role_files(role_dir):
    files = {}
    for path in glob(os.path.join(role_dir, "tasks", "*.yml")):
        try:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if isinstance(data, list):
                files[os.path.basename(path)] = data
        except yaml.YAMLError:
            continue
    return files


def _flatten(tasks, inherited_when=""):
    """Document-order walk that carries each enclosing block's `when` down, so a
    `block: when: not ansible_check_mode` protects everything inside it."""
    for task in tasks or []:
        if not isinstance(task, Mapping):
            continue
        own_when = _stringify(task.get("when", ""))
        combined = f"{inherited_when} {own_when}".strip()
        for section in ("block", "rescue", "always"):
            if isinstance(task.get(section), list):
                yield from _flatten(task[section], combined)
        if any(isinstance(task.get(s), list) for s in ("block", "rescue", "always")):
            continue
        yield task, combined


def _is_tolerated(task):
    """A probe that cannot fail the play is not this class."""
    if task.get("ignore_errors") is True:
        return True
    failed_when = task.get("failed_when")
    return failed_when is False or _stringify(failed_when).strip().lower() == "false"


def _included_file_and_when(task):
    inc = task.get("ansible.builtin.include_tasks", task.get("include_tasks"))
    fname = None
    if isinstance(inc, str):
        fname = inc
    elif isinstance(inc, Mapping):
        fname = inc.get("file")
    if not isinstance(fname, str):
        return None, ""
    return os.path.basename(fname), _stringify(task.get("when", ""))


def _check_reachable_files(files):
    """Files reachable under `--check`, starting at main.yml. An include gated
    `not ansible_check_mode` prunes its subtree — that is fix (2)."""
    if "main.yml" not in files:
        return set(files)
    reachable = {"main.yml"}
    queue = ["main.yml"]
    while queue:
        for task in files.get(queue.pop(), []):
            if not isinstance(task, Mapping):
                continue
            inc_fn, when = _included_file_and_when(task)
            if not inc_fn or inc_fn not in files or _CHECK_GATE.search(when):
                continue
            if inc_fn not in reachable:
                reachable.add(inc_fn)
                queue.append(inc_fn)
    return reachable


def _violations_in_file(tasks):
    """Probes that run under --check downstream of a mutator that does not."""
    found, mutator = [], None
    for task, when in _flatten(tasks):
        module = _module_name(task)
        if module is None:
            continue
        if module in _STATE_MUTATORS and task.get("check_mode") is not False:
            mutator = mutator or task.get("name", "?")
            continue
        if (
            module in _PROBE_MODULES
            and task.get("check_mode") is False
            and mutator is not None
            and not _CHECK_GATE.search(when)
            and not _is_tolerated(task)
        ):
            found.append(f"{task.get('name', '?')!r} (after {mutator!r})")
    return found


def test_no_check_mode_probe_with_stale_precondition():
    violations = []
    for role_dir in sorted(glob(os.path.join(ROLES_DIR, "*"))):
        if not os.path.isdir(os.path.join(role_dir, "tasks")):
            continue
        files = _load_role_files(role_dir)
        if not files:
            continue
        reachable = _check_reachable_files(files)
        for fname in sorted(files):
            if fname not in reachable:
                continue
            for reason in _violations_in_file(files[fname]):
                violations.append(
                    f"{os.path.basename(role_dir)}/tasks/{fname}: {reason} runs "
                    "under `--check` but its precondition does not — gate it on "
                    "`not ansible_check_mode` or make it `failed_when: false`"
                )
    assert not violations, (
        "check-mode probes with a stale precondition "
        "(the deep-check gate will brick these):\n" + "\n".join(sorted(violations))
    )


def test_guard_catches_the_filebrowser_regression():
    """The 2026-08-04 shape, verbatim — proves the guard fails on it if the
    `not ansible_check_mode` gate is ever dropped again."""
    regressed = yaml.safe_load(
        """
        - name: Stop FileBrowser
          community.docker.docker_compose_v2:
            project_src: /x
            state: stopped
        - name: Read current FileBrowser auth method
          ansible.builtin.command:
            cmd: docker run --rm filebrowser config cat -d /config/database.db
          check_mode: false
        """
    )
    assert _violations_in_file(regressed), "guard blind to its own founding case"

    fixed = yaml.safe_load(
        """
        - name: Apply noauth to the pre-existing FileBrowser database
          when: not ansible_check_mode
          block:
            - name: Stop FileBrowser
              community.docker.docker_compose_v2:
                project_src: /x
                state: stopped
            - name: Read current FileBrowser auth method
              ansible.builtin.command:
                cmd: docker run --rm filebrowser config cat -d /config/database.db
              check_mode: false
        """
    )
    assert not _violations_in_file(fixed), "block-level gate must satisfy the guard"
