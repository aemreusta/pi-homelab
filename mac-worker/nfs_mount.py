#!/usr/bin/python3
"""Supervise native macOS NFS mounts; health requires bounded reads, not a PID.

No third-party dependencies. The shell entrypoint execs this long-running process
so launchd retains one stable PID. Docker NFS volumes are managed separately.
"""

import argparse
import errno
import json
import logging
import os
import pwd
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROBE_CONTENT = b"homelab-nfs-v1\n"
MOUNT_OPTS = "rw,nolock,noowners,resvport,soft,timeo=30,intr,noac,vers=3"
EXPORTS = {
    "srv": ("/Volumes/homelab-srv", "/srv", (".homelab-nfs-probe",)),
    "generated": ("/Volumes/homelab-generated", "/home/pi/docker/mac-worker/shared", (".homelab-nfs-probe",)),
}
SOURCES = ("192.0.2.100", "100.64.0.10")
STATUS_FILE = Path("/var/run/homelab-nfs.json")
SHARED_STATUS = Path("/Volumes/homelab-generated/mac-nfs-status.json")
WORKER_USER = "operator"
NODE_PLIST = Path("/Users/operator/Library/LaunchAgents/io.tdarr.node.plist")
# A kernel-blocked process can survive SIGKILL until its I/O returns. Never
# accumulate another child for the same operation while that one still exists.
PENDING = {}


def run(command, timeout=10):
    key = tuple(command)
    for pending_key, proc in list(PENDING.items()):
        if proc.poll() is not None:
            del PENDING[pending_key]
    if key in PENDING:
        return subprocess.CompletedProcess(command, 124, "", "previous operation still blocked")
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)  # noqa: S603 - fixed argv, no shell
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))
    try:
        out, err = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(command, proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=0.5)
        except subprocess.TimeoutExpired:
            PENDING[key] = proc
            proc.stdout.close()
            proc.stderr.close()
        return subprocess.CompletedProcess(command, 124, "", "operation timed out")


def mount_status(output, target):
    in_target = False
    for line in output.splitlines():
        if " from " in line and not line.startswith(" "):
            in_target = line.partition(" from ")[0] == target
        elif in_target and "Status flags:" in line:
            flags = line.partition("Status flags:")[2].strip()
            if "not responding" in flags:
                return "hung"
            return "healthy" if flags == "0x0" else "unknown"
    return "unknown"


def probe_file(path):
    """Runs only in a disposable child: a stuck open must not stall supervision."""
    try:
        with open(path, "rb") as handle:
            return 0 if handle.read(len(PROBE_CONTENT)) == PROBE_CONTENT else 3
    except FileNotFoundError:
        return 2
    except OSError as exc:
        return 4 if exc.errno in (errno.EIO, errno.ESTALE, errno.ETIMEDOUT) else 5


def read_probe(path):
    result = run([sys.executable, str(Path(__file__).resolve()), "--read-probe", path], timeout=8)
    reason = {0: "read_ok", 2: "missing_probe", 3: "invalid_probe", 4: "io_error", 5: "read_denied", 124: "timeout"}
    return result.returncode == 0, reason.get(result.returncode, "probe_failed")


def health(target, probes, status):
    if status.returncode:
        return "unknown", "nfsstat_failed"
    state = mount_status(status.stdout, target)
    if state != "healthy":
        return state, "nfsstat_" + state
    for path in probes:
        ok, reason = read_probe(path)
        if not ok:
            return ("hung" if reason in ("timeout", "io_error") else "unknown"), reason
    return "healthy", "read_ok"


def mounted_sources():
    result = run(["/sbin/mount"])
    if result.returncode:
        return None
    sources = {}
    for line in result.stdout.splitlines():
        match = re.match(r"(\S+:\S+) on (.+) \(nfs[,)]", line)
        if match:
            sources[match[2]] = match[1]
    return sources


def inspect():
    sources = mounted_sources()
    status = run(["/usr/bin/nfsstat", "-m"])
    result = {}
    for name, (target, _, probes) in EXPORTS.items():
        source = (sources or {}).get(target, "")
        if sources is None:
            state, reason = "unknown", "mount_list_failed"
        elif not source:
            state, reason = "unmounted", "mount_absent"
        else:
            state, reason = health(target, tuple(str(Path(target) / p) for p in probes), status)
        result[name] = {"state": state, "reason": reason, "source": source}
    return result


def all_healthy(exports):
    return all(exports.get(name, {}).get("state") == "healthy" for name in EXPORTS)


def decide(state, failures, elapsed, reachable, ever_read_ok=True):
    if not reachable or elapsed < 300:
        return "none"
    if state == "unmounted":
        return "mount"
    if state == "hung" and failures >= 2:
        # A supervisor that has NEVER completed a single successful read has no
        # baseline to judge against: it cannot tell "the mount is hung" from "I
        # am not allowed to look at it". Remounting on that evidence is acting
        # on an opinion it has no standing to hold.
        #
        # This is not hypothetical. On this Mac the daemon runs as a
        # LaunchDaemon, which does NOT inherit Terminal's Full Disk Access
        # grant, so every read probe returns `read_denied` (12x on 2026-09-25)
        # while the identical probe run from a shell returns rc=0 and the
        # kernel logs ZERO nfs "not responding" events in 24h. The mount was
        # healthy the whole time. Occasionally the blocked probe times out
        # rather than being denied, which scores as `hung`, and two of those
        # bought a real unmount/remount of a perfectly good share — surfacing
        # to the operator as macOS "Server connections interrupted:
        # homelab-srv". The monitor was manufacturing the exact symptom it
        # exists to detect, which is remount-storm #2 after 2026-06-07.
        #
        # So: prove you can read the share once, then you may act on it.
        if not ever_read_ok:
            return "none"
        return "recover"
    # Do not disrupt a healthy Tailscale mount merely because LAN returned.
    # Unknown means the probe is broken, not permission to unmount a busy share.
    return "none"


def recover(source, target, action):
    if action == "recover":
        unmount = run(["/sbin/umount", "-f", target], timeout=15)
        if unmount.returncode:
            unmount = run(["/usr/sbin/diskutil", "unmount", "force", target], timeout=15)
        if unmount.returncode:
            logging.error("unmount failed for %s: %s", target, unmount.stderr.strip())
            return False
    # Also catches an unexpected mount left behind by a failed/partial unmount.
    sources = mounted_sources()
    if sources is None or target in sources:
        logging.error("refusing to overmount %s; mount state not empty", target)
        return False
    mkdir = run(["/bin/mkdir", "-p", target])
    if mkdir.returncode:
        return False
    result = run(["/sbin/mount_nfs", "-o", MOUNT_OPTS, source, target], timeout=30)
    if result.returncode:
        logging.error("mount failed for %s: %s", target, result.stderr.strip())
    return result.returncode == 0


def pick_source():
    for ip in SOURCES:
        if run(["/usr/bin/nc", "-z", "-G", "2", "-w", "2", ip, "2049"], timeout=3).returncode == 0:
            return ip
    return ""


def recover_with_worker(source, target, action):
    """Stop the exact launchd worker during main-share recovery; preserve its loaded state."""
    domain = ""
    if target == EXPORTS["srv"][0] and action == "recover":
        # An unresolvable worker user used to raise KeyError straight out of
        # getpwnam and take down the supervise loop — an unhandled crash in the
        # one code path whose entire job is to be careful. Refuse the recovery
        # instead, consistent with every other "I cannot confirm the worker
        # state" branch below: we are about to unmount the share this worker
        # reads from, so not knowing who the worker is disqualifies us.
        try:
            worker_uid = pwd.getpwnam(WORKER_USER).pw_uid
        except KeyError:
            logging.error("worker user %r does not resolve; refusing recovery", WORKER_USER)
            return False
        candidate = "gui/" + str(worker_uid)
        label = candidate + "/io.tdarr.node"
        loaded = run(["/bin/launchctl", "print", label])
        if loaded.returncode == 0:
            if not NODE_PLIST.is_file():
                logging.error("worker plist unavailable; refusing a stop that cannot be restored")
                return False
            if run(["/bin/launchctl", "bootout", label], timeout=20).returncode:
                logging.error("worker could not stop; refusing to unmount its share")
                return False
            domain = candidate
        elif "Could not find service" not in loaded.stderr:
            logging.error("worker state could not be determined; refusing recovery")
            return False
    recovered = False
    resumed = True
    try:
        recovered = recover(source, target, action)
    finally:
        if domain:
            resumed = run(["/bin/launchctl", "bootstrap", domain, str(NODE_PLIST)], timeout=20).returncode == 0
            if not resumed:
                logging.error("worker restart failed: bootstrap %s %s", domain, NODE_PLIST)
    return recovered and resumed


def write_status(path, status):
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(status, output)
            output.write("\n")
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def supervise():
    history = {
        name: {"failures": 0, "attempt_at": -300.0, "recoveries": 0, "recovery_ok": 0, "last_read_ok": 0} for name in EXPORTS
    }
    previous = {}
    logging.info("NFS supervisor started")
    while True:
        exports = inspect()
        source = pick_source() if not all_healthy(exports) else ""
        for name, item in exports.items():
            record = history[name]
            record["failures"] = record["failures"] + 1 if item["state"] == "hung" else 0
            if item["state"] == "healthy":
                record["last_read_ok"] = int(time.time())
            action = decide(
                item["state"],
                record["failures"],
                time.monotonic() - record["attempt_at"],
                bool(source),
                ever_read_ok=bool(record["last_read_ok"]),
            )
            if action != "none":
                target, remote, probes = EXPORTS[name]
                logging.warning("%s %s (%s)", action, name, item["reason"])
                record["attempt_at"] = time.monotonic()
                record["recoveries"] += 1
                mounted = recover_with_worker(source + ":" + remote, target, action)
                if mounted:
                    item["state"], item["reason"] = health(
                        target, tuple(str(Path(target) / p) for p in probes), run(["/usr/bin/nfsstat", "-m"])
                    )
                    item["source"] = source + ":" + remote
                else:
                    item["state"], item["reason"] = "unknown", "recovery_failed"
                record["recovery_ok"] = int(mounted and item["state"] == "healthy")
                if record["recovery_ok"]:
                    record["last_read_ok"] = int(time.time())
            signature = (item["state"], item["reason"])
            if previous.get(name) != signature:
                logging.info("%s state=%s reason=%s", name, *signature)
                previous[name] = signature
            item.update({key: record[key] for key in ("recoveries", "recovery_ok", "last_read_ok")})
        status = {"schema": 1, "checked_at": int(time.time()), "exports": exports}
        try:
            write_status(STATUS_FILE, status)
        except OSError:
            logging.exception("local status publish failed")
        # Publish through the independent generated export in a bounded child.
        # If it also fails, the Pi-side stale alert is the failure signal.
        published = run([sys.executable, str(Path(__file__).resolve()), "--publish", str(STATUS_FILE)], timeout=8)
        if published.returncode and previous.get("publish") != published.returncode:
            logging.error("shared status publish failed: %s", published.stderr.strip())
        previous["publish"] = published.returncode
        time.sleep(30)


def drop_file_io_privileges():
    """Mount management needs root; data reads and publication use the worker account."""
    if os.geteuid() == 0:
        account = pwd.getpwnam(WORKER_USER)
        os.initgroups(account.pw_name, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="read-only verification of ALL native exports")
    parser.add_argument("--read-probe", help=argparse.SUPPRESS)
    parser.add_argument("--publish", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.read_probe or args.publish:
        drop_file_io_privileges()
    if args.read_probe:
        return probe_file(args.read_probe)
    if args.publish:
        write_status(SHARED_STATUS, json.loads(Path(args.publish).read_text()))
        return 0
    if args.check:
        exports = inspect()
        print(json.dumps(exports, indent=2))
        return 0 if all_healthy(exports) else 1
    if os.geteuid() != 0:
        parser.error("daemon must run as root; --check is read-only")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    supervise()
    return 0


if __name__ == "__main__":
    sys.exit(main())
