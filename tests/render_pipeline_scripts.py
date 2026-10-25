#!/usr/bin/env python3
"""Render pipeline shell templates with placeholder values so shellcheck
can analyze them.

We do NOT use Ansible for rendering — it would require inventory + vault
unlock. Instead we do a coarse Jinja2-strip: replace {{ ... }} with a
shell-safe literal and drop {% ... %} blocks entirely. This is good enough
for shellcheck (which only cares about shell syntax, not values) and keeps
the tool offline + zero-cost.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Discover every shell template; tests also consume the original output names.
TEMPLATES = [
    (str(path.relative_to(REPO)), path.name.removesuffix(".j2"))
    for path in sorted((REPO / "ansible/roles").glob("*/templates/*.sh.j2"))
]


# {% raw %} ... {% endraw %} → strip the tags, keep inner content
RAW_BLOCK = re.compile(r"\{%\s*raw\s*%\}(.*?)\{%\s*endraw\s*%\}", re.DOTALL)
# {% ... %} → drop entirely (control flow doesn't affect shell syntax).
#
# The trailing \n is consumed for a statement that sits alone on its line,
# because that is what Ansible does: the template module renders with
# trim_blocks=True, which strips the newline after a block tag. Without
# this, a conditional flag inside a backslash-continued command renders as
# a BLANK LINE here but not in production, and shellcheck reports SC2215
# against a script that is actually fine. That false positive is expensive:
# it makes the lint gate look wrong and discourages registering templates,
# which is how 24 of them ended up unlinted in the first place.
JINJA_STMT = re.compile(r"^[ \t]*\{%[^%]*%\}[ \t]*\n|\{%[^%]*%\}", re.MULTILINE)
# {{ ... }} → replace with a benign placeholder ("PLACEHOLDER") that's safe
# in any shell context (quoted strings, integer math via 0, etc.)
# Quoted Go-template delimiters inside a Jinja expression are not its terminator.
JINJA_EXPR = re.compile(r"""\{\{(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|(?!\}\}).)*?\}\}""", re.DOTALL)


def strip_jinja(text: str) -> str:
    text = RAW_BLOCK.sub(lambda m: m.group(1), text)
    text = JINJA_STMT.sub("", text)
    # Pick placeholder by surrounding context: if inside arithmetic, use 0;
    # otherwise use a shell-safe word. For simplicity, always use 0 — it's
    # valid in $(( )), as a port number, and as a string literal.
    text = JINJA_EXPR.sub("0", text)
    return text


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <output_dir>", file=sys.stderr)
        return 2
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    for src_rel, name in TEMPLATES:
        src = REPO / src_rel
        if not src.exists():
            print(f"missing template: {src}", file=sys.stderr)
            return 1
        rendered = strip_jinja(src.read_text())
        (out_dir / name).write_text(rendered)
    print(f"rendered {len(TEMPLATES)} templates → {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
