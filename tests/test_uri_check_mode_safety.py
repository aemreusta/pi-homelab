"""Repo-wide guard against the check-mode `uri`-skip `.json`-poison class.

Class write-up: ``docs/operations/ops-discipline.md`` §6.

In ``--check`` mode ``ansible.builtin.uri`` is **skipped** (it can't preview a
request): it registers ``{skipped: true}`` with no ``.json``, and ``until:``
never runs to fail-close. So any task that *runs* in check mode and resolves
``X.json`` in an arg (``url``/``method``/``body``/``headers``), a ``set_fact``
value, a ``loop``, or a ``when`` without a ``'json' in X`` short-circuit — where
``X`` is a ``uri`` register — crashes at arg finalization with
``object of type 'dict' has no attribute 'json'``.

The autodeploy ``deploy_target`` runs ``ansible-playbook --check --diff`` BEFORE
apply (deep-check gate, default-ON since 2026-06-25), so an un-exempt live-API
config phase **bricks the next deploy of its role**. This bit a media-app role on
2026-07-02 (instance 1) and lies in wait in beszel / jellyseerr / tdarr
(instances 2-4).

**Invariant enforced here:** every role task file that contains such a
check-mode-crashing ``.json`` consumer must be *unreachable in check mode* — i.e.
every include path to it from the role's ``main.yml`` passes through an include
gated ``not ansible_check_mode``. Fixing a role = add that gate to its config
include (never a poison ``default`` on the consumer).
"""

import os
import re
from collections.abc import Mapping
from glob import glob

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROLES_DIR = os.path.join(REPO_ROOT, "ansible", "roles")

_URI_MODULES = {"ansible.builtin.uri", "uri"}
# A skipped `uri` register is a bare `{skipped: true}` dict, so BOTH `.json`
# (parsed body) and `.content` (raw body, from `return_content: true`) crash
# with `dict has no attribute '<attr>'`. The 2026-07-14 adguard rollback was the
# `.content` variant (`adguard_current_rewrites.content | from_json` in a loop).
_JSON_ACCESS = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.(?:json|content)\b")
# fields a task evaluates when it RUNS (skipped tasks never reach these) — so a
# `.json` here on a check-mode-skipped producer crashes. `when:` is included:
# it's evaluated for every non-skipped task, and only a `'json' in X`
# short-circuit makes it safe.
_CONSUMER_ARG_FIELDS = ("url", "method", "body", "content", "headers")
_CONSUMER_TASK_FIELDS = ("when", "changed_when", "failed_when", "loop")


def _membership_re(var):
    # safe inline short-circuit: `'json' in X` or `'content' in X`
    return re.compile(r"""['"](?:json|content)['"]\s+in\s+""" + re.escape(var) + r"\b")


def _stringify(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    import json as _json

    return _json.dumps(value, default=str)


def _module_args(task):
    for key, val in task.items():
        if key.startswith("ansible.builtin.") and isinstance(val, Mapping):
            return key, val
    return None, {}


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


def _uri_register_vars(files):
    producers = set()
    for tasks in files.values():
        for task in tasks:
            if not isinstance(task, dict):
                continue
            module, _ = _module_args(task)
            if module in _URI_MODULES and task.get("register"):
                producers.add(task["register"])
    return producers


def _consumer_fields(task):
    _, args = _module_args(task)
    for factkey in ("ansible.builtin.set_fact", "set_fact"):
        for name, val in (task.get(factkey) or {}).items():
            yield (f"set_fact[{name}]", _stringify(val))
    for field in _CONSUMER_ARG_FIELDS:
        if field in args:
            yield (field, _stringify(args[field]))
    for field in _CONSUMER_TASK_FIELDS:
        if field in task:
            yield (field, _stringify(task[field]))


def _risky_json_consumers(files, producers):
    """{filename: [reasons]} for files with a check-mode-crashing `.json`
    consumer of a uri-register var (not `'json' in X`-guarded inline or in when)."""
    risky = {}
    for fname, tasks in files.items():
        for task in tasks:
            if not isinstance(task, dict):
                continue
            when_text = _stringify(task.get("when", ""))
            for label, text in _consumer_fields(task):
                for var in set(_JSON_ACCESS.findall(text)):
                    if var not in producers:
                        continue
                    # safe iff the task skips in check mode (when: 'json' in X)
                    # or the access is inline-guarded in the same expression.
                    if _membership_re(var).search(when_text):
                        continue
                    if _membership_re(var).search(text):
                        continue
                    risky.setdefault(fname, []).append(
                        f"{task.get('name', '?')} :: {label} → {var}.json"
                    )
    return risky


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
    """Files reachable when the role runs under `ansible-playbook --check`,
    starting at main.yml. An include gated `not ansible_check_mode` prunes its
    subtree (that's the fix)."""
    start = "main.yml" if "main.yml" in files else None
    if start is None:
        return set(files)  # no entry point known — treat all as reachable
    reachable = {start}
    queue = [start]
    while queue:
        cur = queue.pop()
        for task in files.get(cur, []):
            if not isinstance(task, dict):
                continue
            inc_fn, when = _included_file_and_when(task)
            if not inc_fn or inc_fn not in files:
                continue
            if "not ansible_check_mode" in when:
                continue  # pruned in check mode — the fix
            if inc_fn not in reachable:
                reachable.add(inc_fn)
                queue.append(inc_fn)
    return reachable


def test_no_check_mode_reachable_uri_json_crash():
    violations = []
    for role_dir in sorted(glob(os.path.join(ROLES_DIR, "*"))):
        if not os.path.isdir(os.path.join(role_dir, "tasks")):
            continue
        role = os.path.basename(role_dir)
        files = _load_role_files(role_dir)
        if not files:
            continue
        producers = _uri_register_vars(files)
        if not producers:
            continue
        risky = _risky_json_consumers(files, producers)
        if not risky:
            continue
        reachable = _check_reachable_files(files)
        for fname, reasons in risky.items():
            if fname in reachable:
                violations.append(
                    f"{role}/tasks/{fname}: reachable under `--check` with a "
                    f"uri-skip .json/.content crash — gate its include `not "
                    f"ansible_check_mode`. First: {reasons[0]}"
                )
    assert not violations, (
        "check-mode uri-skip .json crashes (deep-check will brick these):\n"
        + "\n".join(sorted(violations))
    )
