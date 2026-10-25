#!/usr/bin/env python3
"""Read-only Beszel sample freshness, independent of the hub health endpoint."""

import argparse
import os
import sqlite3
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path


def latest_sample(db: Path, system_name: str) -> int:
    conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        conn.execute("BEGIN")
        systems = conn.execute("SELECT id FROM systems WHERE name=?", (system_name,)).fetchall()
        if len(systems) != 1:
            raise ValueError("expected exactly one configured Beszel system")
        row = conn.execute(
            "SELECT MAX(created) FROM system_stats WHERE system=? AND type='1m'",
            (systems[0][0],),
        ).fetchone()
        if not row[0]:
            return 0
        timestamp = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return int(timestamp.timestamp())
    finally:
        conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--system-name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    now = int(time.time())
    metrics = {"beszel_freshness_last_run_timestamp_seconds": now}
    success = 0
    try:
        sample = latest_sample(args.db, args.system_name)
        if sample > now + 60:
            raise ValueError("Beszel sample timestamp is in the future")
        metrics["beszel_last_sample_timestamp_seconds"] = sample
        success = 1
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Beszel freshness collection failed: {exc}", file=sys.stderr)
    metrics["beszel_freshness_success"] = success
    fd, temp = tempfile.mkstemp(prefix=".beszel-", dir=args.output.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            for name, value in metrics.items():
                stream.write(f"# TYPE {name} gauge\n{name} {value}\n")
            os.fchmod(stream.fileno(), 0o644)
        os.replace(temp, args.output)
    finally:
        Path(temp).unlink(missing_ok=True)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
