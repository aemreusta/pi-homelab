"""Missing, malformed, or stale Mac status must never look healthy."""

import importlib.util
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "ansible/roles/nfs_server/files/nfs_client_metrics.py"


@pytest.fixture
def metrics():
    spec = importlib.util.spec_from_file_location("nfs_client_metrics", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def status(state="healthy", checked=995):
    return {
        "schema": 1,
        "checked_at": checked,
        "exports": {
            name: {
                "state": state,
                "last_read_ok": min(995, checked) if isinstance(checked, int) else 0,
                "recoveries": 2,
                "recovery_ok": 1,
            }
            for name in ("srv", "generated")
        },
    }


def test_live_read_health_is_export_specific(metrics):
    value = status()
    value["exports"]["srv"]["state"] = "hung"
    text = metrics.render(value, now=1000)
    assert "mac_nfs_status_valid 1\n" in text
    assert 'mac_nfs_read_ok{export="srv"} 0\n' in text
    assert 'mac_nfs_read_ok{export="generated"} 1\n' in text
    assert "mac_nfs_check_timestamp_seconds 995\n" in text


@pytest.mark.parametrize(
    "value",
    [None, {}, [], {"schema": 1}, {"schema": 2}, status(checked="oops"), status(checked=1001), status(checked=float("nan"))],
)
def test_invalid_input_emits_invalid_instead_of_crashing(metrics, value):
    text = metrics.render(value, now=1000)
    assert "mac_nfs_status_valid 0\n" in text
    assert 'mac_nfs_read_ok{export="srv"} 0\n' in text
    assert "mac_nfs_collector_timestamp_seconds 1000\n" in text


def test_stale_healthy_sample_not_current_health(metrics):
    text = metrics.render(status(checked=300), now=1000)
    assert "mac_nfs_status_valid 1\n" in text
    assert 'mac_nfs_read_ok{export="srv"} 0\n' in text


def test_missing_export_invalidates_report(metrics):
    value = status()
    del value["exports"]["generated"]
    assert "mac_nfs_status_valid 0\n" in metrics.render(value, now=1000)


def test_malformed_counter_cannot_poison_prometheus(metrics):
    value = status()
    value["exports"]["srv"]["recoveries"] = "1\ninjected 2"
    assert "mac_nfs_status_valid 0\n" in metrics.render(value, now=1000)
