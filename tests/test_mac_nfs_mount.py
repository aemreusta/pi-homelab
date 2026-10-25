"""Exercise NFS failures without mounting or touching the operator's shares."""

import importlib.util
import json
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "mac-worker/nfs_mount.py"


@pytest.fixture
def nfs(monkeypatch):
    spec = importlib.util.spec_from_file_location("nfs_mount", MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    # The recovery path resolves the worker account with pwd.getpwnam(). That
    # account exists on the Mac this runs on and does NOT exist on a CI runner,
    # so without this the suite passes locally and fails in CI with
    # `KeyError: getpwnam(): name not found` — which is exactly what happened on
    # 2026-09-26 and blocked a release. Pin the lookup so the tests exercise the
    # launchctl logic rather than the host's user database.
    monkeypatch.setattr(
        module.pwd, "getpwnam",
        lambda name: types.SimpleNamespace(pw_uid=501, pw_name=name),
    )
    return module


def report(target, flags="0x0"):
    return f"{target} from 192.0.2.1:/srv\n  Parameters: v3\n     Status flags: {flags}\n\n"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (report("/srv"), "healthy"),
        (report("/srv", "0x2,not responding"), "hung"),
        (report("/srv-other", "0x2,not responding") + report("/srv"), "healthy"),
        (report("/srv-other"), "unknown"),
        ("/srv from host:/srv\n", "unknown"),
        (report("/srv", "unparseable"), "unknown"),
        (report("/srv", "0x4"), "unknown"),
    ],
)
def test_exact_mount_status(nfs, text, expected):
    assert nfs.mount_status(text, "/srv") == expected


def test_missing_nfsstat_is_an_observable_probe_failure(nfs):
    result = nfs.run(["/definitely/not/installed/nfsstat", "-m"], timeout=0.1)
    assert result.returncode == 127
    assert result.stderr
    assert nfs.health("/srv", ("/srv/probe",), result)[0] == "unknown"


def test_command_timeout_is_bounded(nfs):
    before = time.monotonic()
    result = nfs.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.05)
    assert result.returncode == 124
    assert time.monotonic() - before < 2


def test_healthy_flags_do_not_mask_failed_scene_read(nfs, monkeypatch):
    monkeypatch.setattr(nfs, "read_probe", lambda path: (False, "timeout"))
    result = subprocess.CompletedProcess([], 0, report("/srv"), "")
    assert nfs.health("/srv", ("/srv/media/probe",), result) == ("hung", "timeout")


def test_missing_sentinel_does_not_trigger_remount_storm(nfs, monkeypatch):
    monkeypatch.setattr(nfs, "read_probe", lambda path: (False, "missing_probe"))
    result = subprocess.CompletedProcess([], 0, report("/srv"), "")
    assert nfs.health("/srv", ("/srv/probe",), result) == ("unknown", "missing_probe")


@pytest.mark.parametrize(
    ("state", "failures", "elapsed", "action"),
    [
        ("healthy", 0, 999, "none"),
        ("unknown", 4, 999, "none"),
        ("hung", 1, 999, "none"),
        ("hung", 2, 999, "recover"),
        ("hung", 8, 30, "none"),
        ("unmounted", 0, 999, "mount"),
        ("unmounted", 0, 30, "none"),
    ],
)
def test_recovery_is_confirmed_and_rate_limited(nfs, state, failures, elapsed, action):
    assert nfs.decide(state, failures, elapsed, reachable=True) == action
    assert nfs.decide(state, failures, elapsed, reachable=False) == "none"


def test_blind_supervisor_never_remounts_a_share_it_has_never_read(nfs):
    """A monitor that has never completed one successful read must not act.

    Regression for 2026-09-25. As a LaunchDaemon the supervisor does not
    inherit Terminal's Full Disk Access grant, so every read probe returned
    `read_denied` while the identical probe from a shell returned rc=0 and the
    kernel logged ZERO nfs "not responding" events in 24h. The share was fine.
    When the blocked probe timed out rather than being denied it scored as
    `hung`, and two of those bought a real unmount/remount — surfacing as macOS
    "Server connections interrupted: homelab-srv". The supervisor was
    manufacturing the symptom it exists to detect (remount storm #2 after
    2026-06-07).

    It cannot distinguish "hung" from "I am not allowed to look", so it must
    prove it can read the share at least once before it may disrupt it.
    """
    assert nfs.decide("hung", 2, 999, reachable=True, ever_read_ok=False) == "none"
    assert nfs.decide("hung", 99, 99999, reachable=True, ever_read_ok=False) == "none"


def test_a_share_read_once_may_still_be_recovered(nfs):
    # The guard must not disable recovery outright — a share that WAS readable
    # and then hung is the real fault this supervisor exists for.
    assert nfs.decide("hung", 2, 999, reachable=True, ever_read_ok=True) == "recover"


def test_unmounted_share_is_mounted_even_if_never_read(nfs):
    # `unmounted` is read from `mount`, not from a file probe, so the
    # Full-Disk-Access blindness does not apply and mounting is still correct.
    # Without this the guard would leave a genuinely absent mount down forever.
    assert nfs.decide("unmounted", 0, 999, reachable=True, ever_read_ok=False) == "mount"


def test_healthy_tailscale_mount_does_not_switch_mid_job(nfs):
    # Best IP may change independently of storage health; it isn't a recovery cause.
    assert nfs.decide("healthy", 0, 999, reachable=True) == "none"


def test_failed_unmount_never_mounts_over_existing_share(nfs, monkeypatch, tmp_path):
    calls = []

    def failed(command, timeout=10):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, "", "busy")

    monkeypatch.setattr(nfs, "run", failed)
    assert not nfs.recover("192.0.2.1:/srv", str(tmp_path / "mount"), "recover")
    assert len(calls) == 2
    assert all("mount_nfs" not in " ".join(command) for command in calls)


def test_actual_read_checks_contents_not_just_existence(nfs, tmp_path):
    probe = tmp_path / "probe"
    probe.write_bytes(b"wrong data")
    assert nfs.read_probe(str(probe)) == (False, "invalid_probe")
    probe.write_bytes(nfs.PROBE_CONTENT)
    assert nfs.read_probe(str(probe)) == (True, "read_ok")
    probe.unlink()
    assert nfs.read_probe(str(probe)) == (False, "missing_probe")


def test_verification_requires_every_export(nfs):
    assert not nfs.all_healthy({"srv": {"state": "healthy"}})
    assert not nfs.all_healthy({"srv": {"state": "healthy"}, "generated": {"state": "hung"}})
    assert nfs.all_healthy({"srv": {"state": "healthy"}, "generated": {"state": "healthy"}})


def test_status_publish_is_atomic(nfs, tmp_path):
    path = tmp_path / "status.json"
    nfs.write_status(path, {"checked_at": 42, "exports": {}})
    assert json.loads(path.read_text())["checked_at"] == 42
    assert list(tmp_path.iterdir()) == [path]


def test_recovery_stops_and_restores_only_the_registered_worker(nfs, monkeypatch, tmp_path):
    plist = tmp_path / "node.plist"
    plist.write_text("test")
    monkeypatch.setattr(nfs, "NODE_PLIST", plist)
    calls = []

    def success(command, timeout=10):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(nfs, "run", success)
    monkeypatch.setattr(nfs, "recover", lambda *args: False)
    assert not nfs.recover_with_worker("host:/srv", "/Volumes/homelab-srv", "recover")
    assert calls[1][1] == "bootout"
    assert calls[-1][1] == "bootstrap"
    assert calls[-1][-1] == str(plist)


def test_unresolvable_worker_user_refuses_recovery_instead_of_crashing(nfs, monkeypatch):
    """An unknown worker account must not take down the supervise loop.

    pwd.getpwnam() raised KeyError straight out of recover_with_worker, so a
    renamed or missing account crashed the daemon in the one code path whose
    whole job is caution. We are about to unmount the share this worker reads
    from; not knowing who the worker is disqualifies us from acting.
    """
    def missing(name):
        raise KeyError(f"getpwnam(): name not found: {name!r}")

    monkeypatch.setattr(nfs.pwd, "getpwnam", missing)
    calls = []
    monkeypatch.setattr(nfs, "run", lambda c, timeout=10: calls.append(c))
    assert nfs.recover_with_worker("host:/srv", "/Volumes/homelab-srv", "recover") is False
    assert calls == []  # refused before touching launchctl or the mount


def test_worker_stop_failure_prevents_unmount(nfs, monkeypatch, tmp_path):
    plist = tmp_path / "node.plist"
    plist.write_text("test")
    monkeypatch.setattr(nfs, "NODE_PLIST", plist)
    calls = []

    def fail_stop(command, timeout=10):
        calls.append(command)
        return subprocess.CompletedProcess(command, int("bootout" in command), "", "failed")

    monkeypatch.setattr(nfs, "run", fail_stop)
    monkeypatch.setattr(nfs, "recover", lambda *args: pytest.fail("must not unmount"))
    assert not nfs.recover_with_worker("host:/srv", "/Volumes/homelab-srv", "recover")
    assert len(calls) == 2


def test_file_io_drops_root_privileges_to_the_worker_account(nfs, monkeypatch):
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr(nfs.os, "geteuid", lambda: 0)
    monkeypatch.setattr(nfs.pwd, "getpwnam", lambda user: SimpleNamespace(pw_name="operator", pw_uid=501, pw_gid=20))
    monkeypatch.setattr(nfs.os, "initgroups", lambda *args: calls.append(("groups", args)))
    monkeypatch.setattr(nfs.os, "setgid", lambda gid: calls.append(("gid", gid)))
    monkeypatch.setattr(nfs.os, "setuid", lambda uid: calls.append(("uid", uid)))
    nfs.drop_file_io_privileges()
    assert calls == [("groups", ("operator", 20)), ("gid", 20), ("uid", 501)]
