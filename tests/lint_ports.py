#!/usr/bin/env python3
"""Lint: fail if two services publish the same host port.

Scans every docker-compose.yml.j2 under ansible/roles/ and extracts host
ports from `ports:` blocks. Literal numeric ports are compared across all
roles; Jinja-templated ports are resolved from the role's defaults/main.yml
when the variable is defined there.

Exits non-zero on duplicates (or on an unresolved templated port).
"""
from __future__ import annotations

import pathlib
import re
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
ROLES = ROOT / "ansible" / "roles"

PORT_LINE = re.compile(r'^\s*-\s*"([^":]+):[0-9]+(?:/(\w+))?"\s*(#.*)?$')
JINJA_VAR = re.compile(r"^\{\{\s*(\w+)\s*\}\}$")
DEFAULT_LINE = re.compile(r"^\s*(\w+)\s*:\s*[\"']?(\d+)[\"']?\s*(#.*)?$")


def load_defaults(role_dir: pathlib.Path) -> dict[str, int]:
    out: dict[str, int] = {}
    # Inventory overrides role defaults, matching Ansible's precedence.
    for defaults_file in (role_dir / "defaults/main.yml", ROOT / "ansible/inventory/group_vars/all/main.yml"):
        if not defaults_file.is_file():
            continue
        for line in defaults_file.read_text().splitlines():
            m = DEFAULT_LINE.match(line)
            if m:
                out[m.group(1)] = int(m.group(2))
    return out


def extract_ports(compose_file: pathlib.Path, defaults: dict[str, int]):
    ports = []  # (resolved_int|None, proto, lineno, raw_line, host_spec)
    in_ports = False
    block_indent = -1
    for lineno, line in enumerate(compose_file.read_text().splitlines(), 1):
        stripped = line.rstrip()
        if stripped.endswith("ports:"):
            in_ports = True
            block_indent = len(stripped) - len(stripped.lstrip())
            continue
        if not in_ports:
            continue
        if not line.strip():
            continue
        this_indent = len(line) - len(line.lstrip())
        if this_indent <= block_indent and not line.lstrip().startswith("-"):
            in_ports = False
            continue
        m = PORT_LINE.match(line)
        if not m:
            continue
        host_spec = m.group(1)
        proto = m.group(2) or "tcp"
        resolved: int | None = None
        if host_spec.isdigit():
            resolved = int(host_spec)
        else:
            jm = JINJA_VAR.match(host_spec)
            if jm and jm.group(1) in defaults:
                resolved = defaults[jm.group(1)]
        ports.append((resolved, proto, lineno, line.strip(), host_spec))
    return ports


def main() -> int:
    port_to_source: dict[tuple[int, str], list[str]] = defaultdict(list)
    unresolved: list[str] = []
    role_count = 0
    compose_count = 0

    for role_dir in sorted(ROLES.iterdir()):
        if not role_dir.is_dir():
            continue
        role_count += 1
        defaults = load_defaults(role_dir)
        for compose in role_dir.rglob("docker-compose.yml.j2"):
            compose_count += 1
            rel = compose.relative_to(ROOT)
            for resolved, proto, lineno, raw, host in extract_ports(compose, defaults):
                source = f"{rel}:{lineno}"
                if resolved is None:
                    unresolved.append(f"  {source}  {raw}  (host={host})")
                else:
                    port_to_source[(resolved, proto)].append(source)

    duplicates = {p: s for p, s in port_to_source.items() if len(s) > 1}

    print(f"Port uniqueness lint ({role_count} roles, {compose_count} compose files)")
    print(f"  Literal host ports discovered: {len(port_to_source)}")

    exit_code = 0
    if duplicates:
        print("\nDUPLICATE HOST PORTS:")
        for (port, proto), sources in sorted(duplicates.items()):
            print(f"  :{port}/{proto} published by:")
            for src in sources:
                print(f"    - {src}")
        exit_code = 1
    else:
        print("  No duplicates.")

    if unresolved:
        exit_code = 1
        print(f"\nUnresolved templated ports ({len(unresolved)}):")
        for u in unresolved:
            print(u)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
