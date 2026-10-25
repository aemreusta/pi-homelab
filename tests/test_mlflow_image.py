"""Guard: the MLflow image installs the driver its backend URI actually loads.

The image tracks latest with no pins (operator directive). SQLAlchemy 2.1 maps a
bare ``postgresql://`` URI to the ``psycopg`` (v3) dialect, so an image carrying
only ``psycopg2-binary`` crash-looped on ``No module named 'psycopg'`` when a
deploy rebuilt it on 2026-10-03 and the CD rolled the release back.
"""

from __future__ import annotations

import re
from pathlib import Path

ROLE = Path(__file__).resolve().parent.parent / "ansible" / "roles" / "mlflow"


def _requirements() -> set[str]:
    names = set()
    for line in (ROLE / "files" / "requirements.txt").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.add(re.split(r"[\[<>=!~ ]", line, maxsplit=1)[0].lower())
    return names


def test_backend_uri_driver_is_installed():
    compose = (ROLE / "templates" / "docker-compose.yml.j2").read_text()
    m = re.search(r"MLFLOW_BACKEND_STORE_URI=postgresql(\+(\w+))?://", compose)
    assert m, "backend URI not found in the compose template"
    driver = m.group(2) or "psycopg"
    wanted = {"psycopg": "psycopg", "psycopg2": "psycopg2-binary"}[driver]
    assert wanted in _requirements(), f"URI loads {driver}; requirements.txt lacks {wanted}"
