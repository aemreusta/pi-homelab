"""The hub being up must not hide missing or stale agent samples."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "ansible/roles/beszel/files/beszel_freshness.py"


@pytest.fixture
def collector():
    spec = importlib.util.spec_from_file_location("beszel_freshness", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE systems(id TEXT, name TEXT);
            CREATE TABLE system_stats(system TEXT, type TEXT, created TEXT);
            INSERT INTO systems VALUES ('a', 'homelab'), ('b', 'other');
            INSERT INTO system_stats VALUES
                ('a', '1m', '2026-09-24 20:00:00.000Z'),
                ('a', '10m', '2026-09-24 20:10:00.000Z'),
                ('b', '1m', '2026-09-24 20:12:00.000Z');
        """)
    return path


def test_only_expected_system_raw_samples_determine_freshness(collector, db):
    before = db.read_bytes()
    assert collector.latest_sample(db, "homelab") == 1790280000
    assert db.read_bytes() == before


def test_missing_system_fails_instead_of_reporting_another_system(collector, db):
    with pytest.raises(ValueError, match="exactly one"):
        collector.latest_sample(db, "missing")


def test_empty_samples_report_zero_for_staleness_alert(collector, db):
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM system_stats WHERE system='a'")
    assert collector.latest_sample(db, "homelab") == 0


def test_failure_publishes_liveness_without_fake_sample(collector, tmp_path):
    output = tmp_path / "beszel.prom"
    missing = tmp_path / "missing.db"
    assert collector.main(["--db", str(missing), "--system-name", "homelab", "--output", str(output)]) == 1
    text = output.read_text()
    assert "beszel_freshness_success 0\n" in text
    assert "beszel_freshness_last_run_timestamp_seconds " in text
    assert "beszel_last_sample_timestamp_seconds " not in text
    assert not missing.exists()
    assert output.stat().st_mode & 0o777 == 0o644


def test_invalid_timestamp_is_a_collection_failure(collector, db):
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE system_stats SET created='invalid' WHERE type='1m'")
    with pytest.raises(ValueError):
        collector.latest_sample(db, "homelab")
