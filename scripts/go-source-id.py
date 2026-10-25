#!/usr/bin/env python3
"""Content identity for the Go build input, including removed/renamed files."""
import hashlib
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "runtime"
digest = hashlib.sha256()
for path in sorted(root.rglob("*")):
    if path.is_file() and (path.suffix == ".go" or path.name in ("go.mod", "go.sum")):
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
print(digest.hexdigest())
