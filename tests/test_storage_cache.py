"""Storage requests must not multiply slow filesystem scans."""
import threading
import types
from unittest.mock import patch

from test_control_api import _render_template


def module():
    mod = types.ModuleType("storage_cache_test")
    exec(compile(_render_template(), "control-api.py", "exec"), mod.__dict__)
    return mod


def test_parallel_cold_requests_start_one_scan_and_return_without_waiting():
    mod = module()
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def measure():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return {"dirs": [{"name": "local", "bytes": 12}], "scan_time": "now"}

    original = mod._refresh_storage_stats

    def refresh():
        original()
        done.set()

    usage = types.SimpleNamespace(total=100, used=40, free=60)
    with patch.object(mod, "_measure_storage_stats", measure), patch.object(
        mod, "_refresh_storage_stats", refresh
    ), patch.object(mod.shutil, "disk_usage", return_value=usage):
        try:
            first = mod.get_storage_stats()
            assert entered.wait(2)
            replies = [mod.get_storage_stats() for _ in range(20)]
            assert first["dirs"] == []
            assert all(r["refreshing"] and r["free_bytes"] == 60 for r in replies)
            assert len(calls) == 1
        finally:
            release.set()
            assert done.wait(5)
        assert mod.get_storage_stats()["dirs"] == [{"name": "local", "bytes": 12}]
        assert len(calls) == 1


def test_long_scan_ttl_starts_at_completion_and_failure_preserves_snapshot():
    mod = module()
    with patch.object(mod, "_measure_storage_stats", return_value={"dirs": [1]}), patch.object(
        mod.time, "monotonic", return_value=1000
    ):
        mod._refresh_storage_stats()
    assert mod._STORAGE_CACHE["ts"] == 1000
    with patch.object(mod, "_measure_storage_stats", return_value={"error": "unavailable"}), patch.object(
        mod.time, "monotonic", return_value=1100
    ):
        mod._refresh_storage_stats()
        with patch.object(mod.threading, "Thread") as worker:
            result = mod.get_storage_stats()
        worker.assert_not_called()
    assert result["dirs"] == [1]
    assert result["scan_error"] == "unavailable"
    assert not result["refreshing"]
