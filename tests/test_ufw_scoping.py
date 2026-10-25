"""Repo-wide guard: every LAN service UFW rule must be source-scoped.

Context: ``docs/conventions/reverse-proxy-not-an-auth-boundary.md`` +
``docs/research/036-sso-identity-provider-options.md`` (Phase 0). Traefik is not
an auth boundary — every service also answers on its own host port — so the
cheapest, highest-ROI hardening is to stop UFW opening those ports to
``Anywhere`` and scope them to the LAN (Tailscale still reaches them via the
``tailscale0`` interface allow-rule, exactly how the already-scoped media
UIs work).

**Invariant enforced here:** every ``rule: allow`` UFW task that opens a
``port:`` in a role's ``tasks/ufw.yml`` must be source-scoped — it sets
``from_ip`` (or its alias ``src``) to a non-``Anywhere`` value — UNLESS the
(role, task-name) pair is in ``ALLOWED_ANYWHERE`` below. That allowlist is the
*intentional* internet/LAN-broad surface (SSH, DNS :53, the Traefik HTTP/HTTPS
entrypoints, the Tailscale WireGuard port) and is meant to be read as the
documented exception set. Adding a new service that opens a port to Anywhere
fails this test until it is scoped or explicitly (and reviewably) exempted.
"""

import os
from collections.abc import Mapping
from glob import glob

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROLES_DIR = os.path.join(REPO_ROOT, "ansible", "roles")

_UFW_MODULES = {"community.general.ufw", "ufw"}
# Values that mean "no source restriction" — i.e. NOT scoped.
_ANYWHERE = {"", "any", "0.0.0.0/0", "::/0", "anywhere"}

# Intentional Anywhere/LAN-broad rules, keyed by role → set of task `name:`.
# This is the reviewable exception surface; keep it minimal and justified.
ALLOWED_ANYWHERE = {
    # SSH + operator-declared extra ports are the host's deliberate entrypoint.
    "security": {
        "Allow SSH through UFW",
        "Allow additional UFW ports",
    },
    # The LAN DNS resolver must answer every client on the network.
    "adguard": {
        "Allow DNS (TCP) through UFW",
        "Allow DNS (UDP) through UFW",
    },
    # Traefik's :80/:443 are the reverse-proxy entrypoints (the dashboard is NOT
    # here — it is scoped to the LAN).
    "traefik": {
        "Allow HTTP through UFW",
        "Allow HTTPS through UFW",
    },
    # WireGuard handshake port; the tunnel interface itself is a port-less rule.
    "tailscale": {
        "Allow Tailscale WireGuard through UFW",
    },
}


def _iter_tasks(tasks):
    """Yield every task dict, flattening block/rescue/always."""
    for task in tasks or []:
        if not isinstance(task, Mapping):
            continue
        yield task
        for section in ("block", "rescue", "always"):
            if section in task:
                yield from _iter_tasks(task[section])


def _ufw_args(task):
    for key, val in task.items():
        if key in _UFW_MODULES and isinstance(val, Mapping):
            return val
    return None


def _is_scoped(args):
    for field in ("from_ip", "src"):
        val = args.get(field)
        if val is not None and str(val).strip().lower() not in _ANYWHERE:
            return True
    return False


def test_all_service_ufw_ports_are_lan_scoped():
    violations = []
    for role_dir in sorted(glob(os.path.join(ROLES_DIR, "*"))):
        ufw_path = os.path.join(role_dir, "tasks", "ufw.yml")
        if not os.path.isfile(ufw_path):
            continue
        role = os.path.basename(role_dir)
        with open(ufw_path, encoding="utf-8") as fh:
            tasks = yaml.safe_load(fh)
        for task in _iter_tasks(tasks):
            args = _ufw_args(task)
            if args is None:
                continue
            if args.get("rule") != "allow" or "port" not in args:
                continue  # not a port-opening allow rule
            if args.get("delete"):
                continue  # delete:true reconciles away a legacy rule, not an open
            name = task.get("name", "?")
            if name in ALLOWED_ANYWHERE.get(role, set()):
                continue  # documented intentional exception
            if not _is_scoped(args):
                violations.append(
                    f"{role}/tasks/ufw.yml :: '{name}' (port {args['port']}) "
                    f"opens to Anywhere — add `from_ip: \"{{{{ lan_subnet }}}}\"` "
                    f"or add it to ALLOWED_ANYWHERE with justification."
                )
    assert not violations, (
        "Un-scoped UFW rules (service ports open to the whole internet):\n"
        + "\n".join(sorted(violations))
    )
