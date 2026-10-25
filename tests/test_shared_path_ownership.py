"""Repo-wide guard against the multi-role directory "mode war".

Two roles that both declare `state: directory` on the SAME path with DIFFERENT
`owner`/`group` do not converge — they fight, and whichever deployed last wins
until the next deploy of the other one. Nothing fails; the directory simply has
the wrong owner for whoever needs it, and the damage shows up somewhere else
entirely.

Instance 2 (2026-08-04) is why this exists. `/opt/homelab/control` is created by
`homelab_control` as `{{ docker_user }}`, but `gdrive_archive` listed it among
the directories it "owns outright" and chowned it `root:root`. Six state files
belonging to pi-crontab jobs froze at the moment that role first deployed,
because their atomic write (`mkstemp` in the directory, then `os.replace`) needs
write permission on the DIRECTORY. The scripts caught the `PermissionError` and
logged "treating cycle as baseline", so every watcher re-notified the same items
forever — 74 identical Tdarr pushes in 24h, plus mac/sprite watcher flapping.
Instance 1 was the `alert-audit.log` mode war (ops-discipline §2).

**Invariant enforced here:** for any directory path declared by more than one
role, every role must declare the same owner and group. The fix is never "make
them both root" — it is *one role owns the directory, the others own their files
inside it*. Root-owned files in a docker_user-owned directory are fine.

Only paths that resolve to a literal are compared; a path this cannot resolve is
skipped rather than guessed at, so the guard is quiet but never wrong.
"""

import os
import re
from collections.abc import Mapping
from glob import glob

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROLES_DIR = os.path.join(REPO_ROOT, "ansible", "roles")
GROUP_VARS = os.path.join(REPO_ROOT, "ansible", "inventory", "group_vars", "all", "main.yml")

_FILE_MODULES = ("ansible.builtin.file", "file")
_VAR = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_DIRNAME = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\|\s*dirname\s*\}\}")


def _load_yaml(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, Mapping) else {}
    except (OSError, yaml.YAMLError):
        return {}


def _global_vars():
    """Only the plain scalars we need; vault-encrypted values are irrelevant here.

    `docker_user` is seeded from the `docker` role's defaults, which is where it
    is actually declared — not hardcoded. Roles without a `docker` dependency
    have to spell the value out (see `gdrive_archive_bin_dir_owner`), and reading
    the canonical source is what makes this guard notice if those drift apart.
    """
    out = {}
    for key, val in _load_yaml(GROUP_VARS).items():
        if isinstance(val, (str, int)):
            out[key] = str(val)
    docker_defaults = _load_yaml(
        os.path.join(ROLES_DIR, "docker", "defaults", "main.yml")
    )
    if "docker_user" in docker_defaults:
        out.setdefault("docker_user", str(docker_defaults["docker_user"]))
    return out


def _resolve(value, scope, depth=6):
    """Substitute `{{ var }}` from the role's own defaults + group_vars. Returns
    None if anything is still unresolved — we compare literals or nothing."""
    if not isinstance(value, str):
        return None
    for _ in range(depth):
        if "{{" not in value:
            return value.strip() or None
        def dirname(match):
            base = _resolve(scope.get(match.group(1)), scope, depth-1)
            return os.path.dirname(base) if base else match.group(0)

        replaced = _DIRNAME.sub(dirname, value)
        replaced = _VAR.sub(lambda m: str(scope.get(m.group(1), m.group(0))), replaced)
        if replaced == value:
            return None  # fixed point with a var still in it — unresolvable
        value = replaced
    return None


def _dir_declarations(role_dir, globals_):
    """[(path, owner, group)] for every `file: state: directory` task in a role."""
    scope = dict(globals_)
    for defaults in glob(os.path.join(role_dir, "defaults", "*.yml")):
        for key, val in _load_yaml(defaults).items():
            if isinstance(val, (str, int)):
                scope[key] = str(val)

    found = []
    for path in glob(os.path.join(role_dir, "tasks", "*.yml")):
        try:
            with open(path, encoding="utf-8") as fh:
                tasks = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if not isinstance(task, Mapping):
                continue
            args = next(
                (task[m] for m in _FILE_MODULES if isinstance(task.get(m), Mapping)), None
            )
            if not args or args.get("state") != "directory":
                continue
            owner, group = args.get("owner"), args.get("group")
            if owner is None and group is None:
                continue  # no ownership claim — cannot conflict
            raw = args.get("path")
            candidates = [raw]
            # `path: "{{ item }}"` with a literal loop — expand it
            if isinstance(raw, str) and "item" in raw:
                loop = task.get("loop") or task.get("with_items") or []
                candidates = loop if isinstance(loop, list) else []
            for cand in candidates:
                resolved = _resolve(cand, scope)
                if resolved and resolved.startswith("/"):
                    found.append(
                        (
                            resolved.rstrip("/"),
                            _resolve(owner, scope) if owner else None,
                            _resolve(group, scope) if group else None,
                        )
                    )
    return found


def test_no_two_roles_claim_a_directory_with_different_ownership():
    claims = {}
    for role_dir in sorted(glob(os.path.join(ROLES_DIR, "*"))):
        if not os.path.isdir(os.path.join(role_dir, "tasks")):
            continue
        role = os.path.basename(role_dir)
        for path, owner, group in _dir_declarations(role_dir, _global_vars()):
            claims.setdefault(path, {})[role] = (owner, group)

    violations = []
    for path, by_role in sorted(claims.items()):
        distinct = set(by_role.values())
        if len(distinct) > 1:
            detail = ", ".join(
                f"{r}={o or '-'}:{g or '-'}" for r, (o, g) in sorted(by_role.items())
            )
            violations.append(
                f"{path}: claimed with conflicting ownership by {len(by_role)} roles "
                f"({detail}) — one role owns the DIRECTORY, the others own their "
                f"FILES inside it"
            )

    assert not violations, (
        "multi-role directory mode war (last deploy wins; breaks the other "
        "tenant's writes):\n" + "\n".join(violations)
    )


def test_the_control_dir_is_owned_by_the_cron_user():
    """The founding case, asserted directly: /opt/homelab/control hosts state
    files written atomically by pi-crontab jobs, so it must never be root."""
    globals_ = _global_vars()
    target = "/opt/homelab/control"
    owners = {}
    for role_dir in sorted(glob(os.path.join(ROLES_DIR, "*"))):
        if not os.path.isdir(os.path.join(role_dir, "tasks")):
            continue
        for path, owner, group in _dir_declarations(role_dir, globals_):
            if path == target:
                owners[os.path.basename(role_dir)] = (owner, group)

    assert owners, f"no role declares {target} — did the path move?"
    bad = {r: og for r, og in owners.items() if og[0] == "root"}
    assert not bad, (
        f"{target} declared root-owned by {sorted(bad)}; pi's cron state writes "
        "need write permission on the directory itself (mkstemp + os.replace)"
    )


def test_dirname_claims_resolve_aliases_but_do_not_guess_unknown_paths():
    scope = {"file": "{{ directory }}/collector.prom", "directory": "/shared/metrics"}
    assert _resolve("{{ file | dirname }}", scope) == "/shared/metrics"
    assert _resolve("{{ missing | dirname }}", scope) is None


def test_shared_textfile_directory_keeps_the_cron_user_as_owner():
    globals_ = _global_vars()
    target = "/var/lib/node_exporter/textfile"
    claims = {
        os.path.basename(role): (owner, group)
        for role in glob(os.path.join(ROLES_DIR, "*"))
        for path, owner, group in _dir_declarations(role, globals_)
        if path == target
    }
    assert "media" in claims, "dirname-based consumer must be covered by the guard"
    expected = (globals_["docker_user"], globals_["docker_user"])
    assert all(owner == expected for owner in claims.values()), claims
