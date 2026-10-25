"""Guard: every reference to a repository document must resolve.

Background (2026-10-03)
-----------------------
Moving 68 finished records into ``docs/archive/`` exposed how references to
documents had rotted. Role comments, templates, tests and docs pointed at
files by three unverifiable shapes: paths of documents that had since moved
(``docs/prds/...``, ``docs/sprints/archive/...``), number shorthands from two
numbering schemes (``docs/runbooks/<NNN>``, and the older flat ``docs/<NNN>``, where 024
named two different files), and paths wrapped across comment lines
(``docs/incidents/2026-08-01-usb-ssd-root-`` / ``dropout-readonly.md``). About
45 references were already dead before the move, and nothing noticed.

The contract that replaces them:

* Refer to a document by its **full path from the repository root**
  (``docs/runbooks/020-known-log-noise.md``) in code and plain text, or by a
  relative Markdown link inside Markdown. Never wrap the path across lines.
* Moving or renaming a document updates every reference in the same commit,
  even when that touches roles and therefore widens the next deploy tag. A
  stale pointer in a comment is a wrong instruction to the next reader.
* Restart-sensitive artifacts — compose files, application configs and
  Dockerfiles — keep document pointers out of their rendered output. A compose
  template change fires ``state: restarted`` handlers, so a doc move used to
  mean restarting the whole media stack. In those templates a
  pointer goes on its own column-0 Jinja comment line (``{# Docs: docs/... #}``),
  which Ansible's ``trim_blocks`` removes entirely; Dockerfiles carry none.
  Scripts and alert annotations may render pointers: they restart nothing, and
  an annotation's pointer is guidance the operator needs.
* GitHub URLs into this repository use the real owner and an existing path;
  the restore-drill nudge had sent a dead link to a wrong account.

``docs/archive/**`` and ``CHANGELOG.md`` are frozen history: their prose
mentions are exempt, but their Markdown links must still resolve because they
are clickable.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

BINARY_SUFFIXES = {".json", ".pen", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".lock", ".svg"}
FROZEN_PREFIXES = ("docs/archive/", "CHANGELOG.md")

# A path-shaped document reference: ends in .md/.json, optionally ../-prefixed.
DOC_PATH = re.compile(r"(?<![\w./-])(?:\.\./)*(docs/(?:[\w.-]+/)*[\w.-]+\.(?:md|json))\b")
# Unverifiable shapes: number shorthands and paths truncated at a line wrap.
SHORTHAND = re.compile(
    r"(?<![\w./-])docs/(?:archive/)?(?:(?:runbooks|design|research|guides|prds|sprints)/)?\d{3}[a-z]?(?![\w.-])"
)
TRUNCATED = re.compile(r"(?<![\w./-])docs/(?:[\w.-]+/)+[\w.-]+-$", re.MULTILINE)
REPO_BLOB = re.compile(r"github\.com/([\w.-]+)/raspi-homelab/blob/[\w.-]+/([^\s'\")#]+)")
ANY_DOC = re.compile(r"(?<![\w./-])(?:\.\./)*docs/[\w.-]+")
JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
RESTART_SENSITIVE = re.compile(
    r"^ansible/roles/[^/]+/(?:templates/(?:.*/)?(?:docker-compose[^/]*|[^/]*\.conf|[^/]*config[^/]*|alertmanager\.yml)\.j2"
    r"|(?:files|templates)/(?:.*/)?Dockerfile[^/]*)$"
)
MD_LINK = re.compile(r"\]\(<?([^)\s>#]+)(?:#[^)\s>]*)?>?\)")


def _tracked_text_files() -> list[str]:
    out = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)  # noqa: S607
    files = []
    for rel in out.split():
        path = ROOT / rel
        if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
            continue
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        files.append(rel)
    return files


FILES = _tracked_text_files()
# Resolve against what is committed, not the working tree: an ignored or
# untracked local file would pass here and 404 for everyone else (CI caught
# a link to the git-ignored design/images/).
TRACKED = set(subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).split())  # noqa: S607
TRACKED_DIRS = {str(p) for f in TRACKED for p in Path(f).parents}


def _exists(path: Path) -> bool:
    rel = os.path.relpath(path.resolve(), ROOT)
    return rel in TRACKED or rel in TRACKED_DIRS
LIVE_FILES = [f for f in FILES if not f.startswith(FROZEN_PREFIXES)]
MARKDOWN_FILES = [f for f in FILES if f.endswith(".md")]


def _lines(rel: str):
    yield from enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1)


def test_doc_path_references_resolve():
    missing = [
        f"{rel}:{n}: {m.group(1)}"
        for rel in LIVE_FILES
        for n, line in _lines(rel)
        for m in DOC_PATH.finditer(line)
        if not _exists(ROOT / m.group(1))
    ]
    assert not missing, "References to missing documents:\n" + "\n".join(missing)


def test_no_unverifiable_doc_shorthand():
    found = []
    for rel in LIVE_FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for n, line in _lines(rel):
            found += [f"{rel}:{n}: {m.group(0)}" for m in SHORTHAND.finditer(line)]
        found += [f"{rel}: wrapped path {m.group(0)}" for m in TRUNCATED.finditer(text)]
    assert not found, "Use the full docs/<dir>/<file>.md path on one line:\n" + "\n".join(found)


@pytest.mark.parametrize("rel", MARKDOWN_FILES)
def test_markdown_relative_links_resolve(rel):
    base = (ROOT / rel).parent
    broken = [
        target
        for _, line in _lines(rel)
        for target in MD_LINK.findall(line)
        if not re.match(r"^[a-z][a-z0-9+.-]*:", target)
        and not target.startswith("/")
        and not _exists(base / target)
    ]
    assert not broken, f"{rel}: broken relative links {broken}"


def _repo_owner() -> str | None:
    try:
        url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"], cwd=ROOT, text=True  # noqa: S607
        )
    except subprocess.CalledProcessError:
        return None
    m = re.search(r"github\.com[:/]([\w.-]+)/raspi-homelab", url)
    return m.group(1) if m else None


def test_restart_sensitive_artifacts_render_no_doc_pointers():
    offenders = []
    for rel in (f for f in LIVE_FILES if RESTART_SENSITIVE.match(f)):
        text = (ROOT / rel).read_text(encoding="utf-8")
        rendered = JINJA_COMMENT.sub("", text) if rel.endswith(".j2") else text
        offenders += [f"{rel}: {m.group(0)}" for m in ANY_DOC.finditer(rendered)]
    assert not offenders, (
        "Doc pointers in restart-sensitive artifacts must sit in a column-0 "
        "{# Docs: ... #} line (none in Dockerfiles):\n" + "\n".join(offenders)
    )


def test_repository_urls_point_at_existing_paths():
    owner = _repo_owner()
    bad = []
    for rel in LIVE_FILES:
        for n, line in _lines(rel):
            for m in REPO_BLOB.finditer(line):
                if owner and m.group(1) != owner:
                    bad.append(f"{rel}:{n}: owner {m.group(1)} != {owner}")
                if not _exists(ROOT / m.group(2)):
                    bad.append(f"{rel}:{n}: missing {m.group(2)}")
    assert not bad, "Broken repository URLs:\n" + "\n".join(bad)
