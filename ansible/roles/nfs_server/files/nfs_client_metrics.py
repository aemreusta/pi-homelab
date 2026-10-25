#!/usr/bin/env python3
"""Translate a native Mac NFS read report; missing/stale data is never success."""

import argparse
import json
import math
import time
from pathlib import Path

EXPORTS = ("srv", "generated")
STATES = {"healthy", "hung", "unknown", "unmounted"}


def valid_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def valid_status(value, now):
    if not isinstance(value, dict) or value.get("schema") != 1:
        return False
    checked = value.get("checked_at")
    if not valid_number(checked) or checked > now or checked == 0:
        return False
    exports = value.get("exports")
    if not isinstance(exports, dict):
        return False
    for name in EXPORTS:
        item = exports.get(name)
        if not isinstance(item, dict) or not isinstance(item.get("state"), str) or item["state"] not in STATES:
            return False
        if not all(valid_number(item.get(field)) for field in ("last_read_ok", "recoveries", "recovery_ok")):
            return False
        if item["last_read_ok"] > checked or item["recovery_ok"] not in (0, 1):
            return False
    return True


def render(value, now=None):
    now = int(time.time()) if now is None else now
    valid = valid_status(value, now)
    lines = []

    def metric(name, help_text, samples):
        lines.extend([f"# HELP {name} {help_text}", f"# TYPE {name} gauge"])
        lines.extend(f"{name}{labels} {number}" for labels, number in samples)

    metric("mac_nfs_collector_timestamp_seconds", "Last Pi collector run.", [("", now)])
    metric("mac_nfs_status_valid", "Mac report contains all required fields.", [("", int(valid))])
    checked = value["checked_at"] if valid else 0
    metric("mac_nfs_check_timestamp_seconds", "Last native Mac NFS read check.", [("", checked)])
    fields = {
        "read_ok": (
            "Current bounded reads succeeded and report is at most 10 minutes old.",
            lambda item: int(item["state"] == "healthy" and now - checked <= 600),
        ),
        "last_read_success_timestamp_seconds": ("Last successful bounded read.", lambda item: item["last_read_ok"]),
        "recovery_attempts": ("Recovery attempts since daemon start.", lambda item: item["recoveries"]),
        "last_recovery_ok": ("Last recovery passed a bounded read check.", lambda item: item["recovery_ok"]),
    }
    for suffix, (description, get) in fields.items():
        metric(
            "mac_nfs_" + suffix,
            description,
            [(f'{{export="{name}"}}', get(value["exports"][name]) if valid else 0) for name in EXPORTS],
        )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("status", type=Path)
    args = parser.parse_args()
    try:
        value = json.loads(args.status.read_text())
    except (OSError, ValueError):
        value = None
    print(render(value), end="")


if __name__ == "__main__":
    main()
