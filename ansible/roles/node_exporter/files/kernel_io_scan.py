#!/usr/bin/env python3
"""Classify kernel-log lines into storage-fault classes and render Prometheus text.

Why this exists
---------------
2026-08-01: a second bus-powered USB3 SSD was attached to the Pi. The *root*
SanDisk SSD dropped off the USB bus, re-enumerated as a new device node
(/dev/sdb -> /dev/sdc), and ext4 responded to the vanished block device with
`errors=remount-ro`. The homelab ran read-only for ~15 minutes: sshd reset
every connection, 40 of 41 containers were dead, and — because vmalert,
Alertmanager and ntfy all live on the disk that failed — NOT ONE alert fired.
The outage was discovered by hand. See
docs/incidents/2026-08-01-usb-ssd-root-dropout-readonly.md.

The kernel is loud before it is fatal: `uas_eh_abort_handler`, `reset
SuperSpeed USB device`, and `Buffer I/O error` all land in the ring buffer
*before* ext4 gives up. Nothing in this homelab was reading them, so a
recoverable warning window went by unobserved.

Contract
--------
Per CLAUDE.md, comparison/classification logic lives in a plain-Python module
under `<role>/files/` with a matching `tests/test_<name>.py` — NOT in bash or
Jinja2, where the pytest harness cannot reach it. The shell wrapper only does
orchestration (flock, journalctl, atomic textfile emit).

Counts are per-boot: the caller feeds `dmesg`, the kernel's IN-RAM ring
buffer. journalctl was rejected as the source because it is disk-backed —
verified against this incident, `journalctl -k -b -1` recorded the USB
disconnect but NOT the subsequent ext4 failure, since journald could no
longer write to the disk that had just died. Values are therefore GAUGES,
not counters: they reset to 0 on reboot by design, and alerts must not use
rate()/increase() on them.

Known limitation, stated plainly: while the root filesystem is read-only,
NOTHING host-side can publish a textfile metric — the textfile directory is
on that filesystem. So the fatal classes here are for the window *before*
the filesystem gives up, and for post-mortem. The authoritative live signal
for the terminal state is node_exporter's own in-memory
`node_filesystem_readonly{mountpoint="/"}`, which is what the
RootFilesystemReadOnly alert uses.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Ordered most-specific first: classify() returns the FIRST match, so a line
# like "EXT4-fs error ... Remounting filesystem read-only" is attributed to
# ext4_remount_ro (the terminal event) rather than the generic ext4_error.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # ── Terminal: the filesystem gave up and went read-only ──────────
    ("ext4_remount_ro", re.compile(r"remounting filesystem read-only", re.I)),
    # ── The block device vanished / went offline underneath us ───────
    (
        "scsi_offline",
        re.compile(
            r"rejecting i/o to offline device"
            r"|synchronizing scsi cache failed"
            r"|device offline"
            r"|failed to (?:get|set) sense data",
            re.I,
        ),
    ),
    # ── A disk enumerated but reported no usable geometry ────────────
    # The 2026-08-01 signature: the root SSD browned out mid spin-up, so
    # Read Capacity failed and the kernel registered it as a ZERO-BYTE
    # disk. Unambiguous and impossible in healthy operation.
    #   sd 0:0:0:0: [sdb] Read Capacity(16) failed: Result: hostbyte=0x07
    #   sd 0:0:0:0: [sdb] 0 512-byte logical blocks: (0 B/0 B)
    (
        "disk_zero_capacity",
        re.compile(
            r"read capacity\(\d+\) failed"
            r"|\b0 \d+-byte logical blocks\b"
            r"|0-byte physical blocks",
            re.I,
        ),
    ),
    # ── ext4 metadata/journal complaints ─────────────────────────────
    ("ext4_error", re.compile(r"ext4-fs (?:error|warning \(device)", re.I)),
    ("journal_aborted", re.compile(r"journal has aborted|aborting journal", re.I)),
    # ── Raw block-layer I/O failures ─────────────────────────────────
    (
        "block_io_error",
        re.compile(
            r"buffer i/o error"
            r"|blk_update_request"
            r"|critical (?:medium|target) error"
            r"|\bi/o error\b",
            re.I,
        ),
    ),
    # ── UAS/SCSI transport faults — the loudest precursor class ──────
    (
        "uas_fault",
        re.compile(
            r"uas_eh_(?:abort|device_reset|bus_reset)_handler"
            r"|uas: (?:reset|cmd cmplt err)"
            r"|scsi host \d+: uas_eh",
            re.I,
        ),
    ),
    # ── USB link instability (power brownout / cable / controller) ───
    ("usb_disconnect", re.compile(r"usb \S+: usb disconnect", re.I)),
    (
        "usb_reset",
        re.compile(
            # "SuperSpeed" is one word; "high-speed"/"full-speed" are
            # hyphenated — hence the optional separator.
            r"reset (?:super|high|full|low)[- ]?speed usb device"
            r"|device descriptor read/\d+, error"
            r"|device not accepting address",
            re.I,
        ),
    ),
    # ── Controller-level over-current (Pi USB power budget exceeded) ──
    ("usb_over_current", re.compile(r"over-?current (?:change|condition)", re.I)),
)

#: Every class we emit. Emitted ZERO-FILLED so the series always exists —
#: an absent series makes a vmalert rule silently un-fireable, which is the
#: exact failure this repo has shipped before (see CLAUDE.md textfile rule).
CLASSES: tuple[str, ...] = tuple(name for name, _ in _PATTERNS)

#: Classes that mean "the storage stack is actively failing right now".
#: Kept here (not in the alert template) so the test can pin the split.
FATAL_CLASSES: frozenset[str] = frozenset(
    {
        "ext4_remount_ro",
        "scsi_offline",
        "ext4_error",
        "journal_aborted",
        "block_io_error",
        "disk_zero_capacity",
    }
)

#: Classes that are recoverable-but-predictive: the link flapped and the
#: kernel retried successfully. These are the 2026-08-01 warning window.
PRECURSOR_CLASSES: frozenset[str] = frozenset(
    {"uas_fault", "usb_disconnect", "usb_reset", "usb_over_current"}
)

#: Precursor classes that are NEVER normal on this host, because every
#: storage device is permanently attached. A single occurrence is signal.
#: Split out from the noisier reset/abort classes so the alert threshold can
#: differ: on 2026-08-01 the ONLY logged precursor was a single
#: `usb 2-1: USB disconnect, device number 2`, so a >=2 threshold would have
#: missed the entire incident.
NEVER_NORMAL_CLASSES: frozenset[str] = frozenset(
    {"usb_disconnect", "usb_over_current"}
)


def severity_of(name: str) -> str:
    """Map a fault class to the `severity` label used by the vmalert rules.

    Three tiers, each with its own alert threshold:
      fatal     — the storage stack is failing now (>=1 fires, crit)
      dropout   — a link event that is never normal here (>=1 fires, warn)
      precursor — resets/aborts that can occur benignly at boot (>=N fires)
    """
    if name in FATAL_CLASSES:
        return "fatal"
    if name in NEVER_NORMAL_CLASSES:
        return "dropout"
    return "precursor"


def classify(line: str) -> str | None:
    """Return the fault class for a single kernel-log line, or None."""
    for name, pattern in _PATTERNS:
        if pattern.search(line):
            return name
    return None


def scan(lines) -> dict[str, int]:
    """Count fault classes across kernel-log lines.

    Returns a dict with EVERY class in CLASSES present, zero-filled.
    """
    counts = dict.fromkeys(CLASSES, 0)
    for line in lines:
        name = classify(line)
        if name is not None:
            counts[name] += 1
    return counts


#: Matches one exposition line of either events series, capturing the series
#: suffix (empty for the live one, `_last_boot` for the snapshot), the class
#: and the value. Tolerant of extra labels so adding one later is not a
#: silent parse break.
_EVENT_LINE = re.compile(
    r"^homelab_kernel_io_events(?P<suffix>_last_boot)?\{[^}]*"
    r'class="(?P<cls>[a-z0-9_]+)"[^}]*\}\s+(?P<value>-?\d+(?:\.\d+)?)\s*$'
)
_BOOT_ID_LINE = re.compile(r'^homelab_kernel_io_boot_id_info\{boot_id="(?P<id>[^"]*)"\}')


def parse_prom(text: str) -> dict:
    """Read back a previously emitted .prom file.

    Returns ``{"boot_id": str|None, "counts": {...}, "last_boot": {...}}``,
    both count dicts zero-filled across every class.

    Deliberately total: a missing, truncated or garbage file yields zeros
    rather than raising. This runs on a host whose root filesystem sits near
    full, where a half-written predecessor is a realistic input, and a
    crashing scanner would take out the live series too — trading a working
    detector for a lost snapshot is the wrong way round.
    """
    counts = dict.fromkeys(CLASSES, 0)
    last_boot = dict.fromkeys(CLASSES, 0)
    boot_id: str | None = None

    for line in (text or "").splitlines():
        boot_match = _BOOT_ID_LINE.match(line)
        if boot_match:
            boot_id = boot_match.group("id")
            continue
        event = _EVENT_LINE.match(line)
        if not event:
            continue
        cls = event.group("cls")
        if cls not in counts:  # a class removed from a later version
            continue
        target = last_boot if event.group("suffix") else counts
        try:
            target[cls] = int(float(event.group("value")))
        except ValueError:  # pragma: no cover - regex already constrains this
            continue

    return {"boot_id": boot_id, "counts": counts, "last_boot": last_boot}


def carry_forward(previous: dict, current_boot_id: str | None) -> dict[str, int]:
    """Decide the `_last_boot` counts to emit for this scan.

    The whole point of this function, stated plainly: **a reboot erases the
    per-boot gauges, and the failure these gauges exist to catch is precisely
    one that ends in a reboot.** Verified 2026-08-02 — a usb_disconnect ended
    a boot at 02:58 and every class read 0 afterwards.

    dmesg cannot recover it (in-RAM ring buffer, empty after reboot), but the
    .prom file is on disk and survives. So on the first scan of a NEW boot it
    still holds the dead boot's counts — promote those. Within the SAME boot,
    keep whatever was already promoted and never touch the live counts, or
    `_last_boot` would just mirror the live series and observe nothing.

    A missing/unknown boot id is treated as "same boot" — the conservative
    direction, since a spurious promotion would overwrite a real snapshot
    with the current boot's values and destroy the very evidence we are here
    to keep.
    """
    if not current_boot_id or not previous.get("boot_id"):
        return dict(previous.get("last_boot") or dict.fromkeys(CLASSES, 0))
    if previous["boot_id"] != current_boot_id:
        return dict(previous["counts"])
    return dict(previous["last_boot"])


def render_prom(
    counts: dict[str, int],
    now: float,
    boot_id: str | None = None,
    last_boot: dict[str, int] | None = None,
) -> str:
    """Render the textfile-collector exposition for `counts`.

    `now` is injected rather than read from the clock so the output is
    deterministic under test. `boot_id` and `last_boot` are optional so the
    previously deployed 2-arg wrapper keeps working during a partial rollout
    (the shell script and this module ship as separate files).
    """
    out: list[str] = [
        "# HELP homelab_kernel_io_events Kernel storage-fault log lines seen this boot.",
        "# TYPE homelab_kernel_io_events gauge",
    ]
    # Sorted for stable output — a churning textfile makes diffs unreadable.
    for name in sorted(CLASSES):
        severity = severity_of(name)
        out.append(
            f'homelab_kernel_io_events{{class="{name}",severity="{severity}"}} '
            f"{counts.get(name, 0)}"
        )

    if last_boot is not None:
        out += [
            "# HELP homelab_kernel_io_events_last_boot Kernel storage-fault log "
            "lines seen during the PREVIOUS boot (survives the reboot that "
            "resets the live series).",
            "# TYPE homelab_kernel_io_events_last_boot gauge",
        ]
        for name in sorted(CLASSES):
            out.append(
                f'homelab_kernel_io_events_last_boot{{class="{name}",'
                f'severity="{severity_of(name)}"}} {last_boot.get(name, 0)}'
            )

    if boot_id is not None:
        out += [
            "# HELP homelab_kernel_io_boot_id_info Current boot id; a change "
            "marks a reboot and promotes the live counts to _last_boot.",
            "# TYPE homelab_kernel_io_boot_id_info gauge",
            f'homelab_kernel_io_boot_id_info{{boot_id="{boot_id}"}} 1',
        ]

    out += [
        "# HELP homelab_kernel_io_scan_timestamp_seconds Unix time of the last successful scan.",
        "# TYPE homelab_kernel_io_scan_timestamp_seconds gauge",
        f"homelab_kernel_io_scan_timestamp_seconds {now:.0f}",
    ]
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    """Read kernel log lines on stdin, write exposition text on stdout.

    argv[1] = unix time, argv[2] = current boot id, argv[3] = path to the
    previous .prom. The shell wrapper owns the clock, the boot id and the
    filesystem so this stays pure and testable. All three are optional: with
    only argv[1] the output is byte-identical to the pre-2026-08-02 format.
    """
    if len(argv) > 1:
        now = float(argv[1])
    else:  # pragma: no cover - wrapper always passes it
        import time

        now = time.time()

    boot_id = argv[2] if len(argv) > 2 and argv[2] else None
    last_boot = None
    if boot_id is not None:
        previous_text = ""
        if len(argv) > 3 and argv[3]:
            try:
                previous_text = Path(argv[3]).read_text(errors="replace")
            except OSError:
                # No previous file (first run) or unreadable. Zeros, not a
                # crash — see parse_prom's docstring.
                previous_text = ""
        last_boot = carry_forward(parse_prom(previous_text), boot_id)

    sys.stdout.write(render_prom(scan(sys.stdin), now, boot_id, last_boot))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
