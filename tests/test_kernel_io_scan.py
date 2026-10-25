"""Tests for the kernel storage-fault scanner (node_exporter role).

Fixtures are VERBATIM kernel lines captured from the Pi during the
2026-08-01 USB SSD root-dropout incident plus a clean boot, so this suite
proves the scanner would have flagged that outage rather than merely
hoping it would.

This is the Python-emitter analogue of tests/test_textfile_emitters.py.
That harness scrapes `echo`/`printf` out of *shell* templates, so it cannot
see this emitter — the exposition is built in kernel_io_scan.render_prom().
The HELP/TYPE/numeric-value guarantees it enforces are covered by
TestRender below instead. Do NOT add this role to that file's SPECS.

Run: pytest tests/test_kernel_io_scan.py -v
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "ansible" / "roles" / "node_exporter" / "files" / "kernel_io_scan.py"

_spec = importlib.util.spec_from_file_location("kernel_io_scan", MODULE_PATH)
assert _spec and _spec.loader, f"cannot load {MODULE_PATH}"
kio = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kio)


# Real lines from the Pi's clean 2026-08-01 21:45 boot (dmesg -T), which must
# NOT be classified as faults. "new SuperSpeed USB device" is enumeration;
# only "reset SuperSpeed USB device" is a fault.
CLEAN_BOOT = [
    "usb 2-1: new SuperSpeed USB device number 4 using xhci_hcd",
    "usb 2-1: New USB device found, idVendor=0781, idProduct=55bb, bcdDevice=60.04",
    "usb 2-1: Product: Portable SSD",
    "usb 2-1: Manufacturer: SanDisk",
    "scsi host0: uas",
    "sd 0:0:0:0: [sdb] 3907029168 512-byte logical blocks: (2.00 TB/1.82 TiB)",
    "sd 0:0:0:0: [sdb] Write cache: enabled, read cache: enabled, supports DPO and FUA",
    "sd 0:0:0:0: [sdb] Attached SCSI disk",
    "EXT4-fs (sdb): orphan cleanup on readonly fs",
    "EXT4-fs (sdb): mounted filesystem 78ea8134 ro with ordered data mode. Quota mode: none.",
    "EXT4-fs (sdb): re-mounted 78ea8134 r/w.",
    "usbcore: registered new interface driver brcmfmac",
]

# VERBATIM from `journalctl -k -b -1` on the Pi — the actual 2026-08-01
# incident. Both SSDs enumerate, then the root SanDisk (usb 2-1) drops
# mid spin-up because the two drives together exceeded the Pi 4B's USB
# power budget, and comes back registered as a ZERO-BYTE disk.
#
# NOTE what is absent: no ext4 error, no "Remounting filesystem read-only".
# The root fs went read-only ~8 minutes later, and journald could not write
# the evidence to the disk that had just failed. This is why the fatal
# classes cannot be relied on for THIS failure mode, and why the dropout
# tier exists. See the incident doc.
INCIDENT_REAL = [
    "usb 2-1: new SuperSpeed USB device number 2 using xhci_hcd",
    "usb 2-1: Product: Portable SSD",
    "usb 2-1: Manufacturer: SanDisk",
    "scsi host0: uas",
    "usb 2-2: new SuperSpeed USB device number 3 using xhci_hcd",
    "usb 2-2: Product: XS1000",
    "usb 2-2: Manufacturer: Kingston",
    "scsi host1: uas",
    "sd 1:0:0:0: [sda] 1953525168 512-byte logical blocks: (1.00 TB/932 GiB)",
    "sd 1:0:0:0: [sda] Attached SCSI disk",
    "sd 0:0:0:0: [sdb] Spinning up disk...",
    "usb 2-1: USB disconnect, device number 2",
    "sd 0:0:0:0: [sdb] Read Capacity(16) failed: Result: hostbyte=0x07 driverbyte=DRIVER_OK",
    "sd 0:0:0:0: [sdb] Sense not available.",
    "sd 0:0:0:0: [sdb] Read Capacity(10) failed: Result: hostbyte=0x07 driverbyte=DRIVER_OK",
    "sd 0:0:0:0: [sdb] 0 512-byte logical blocks: (0 B/0 B)",
    "sd 0:0:0:0: [sdb] 0-byte physical blocks",
    "sd 0:0:0:0: [sdb] Attached SCSI disk",
    "usb 2-1: new SuperSpeed USB device number 4 using xhci_hcd",
]

# Synthetic continuation: what the kernel logs when a live filesystem loses
# its device. These lines never reached disk on 2026-08-01 (journald was
# already read-only), but dmesg's in-RAM ring buffer would carry them.
DROPOUT = [
    "sd 1:0:0:0: [sdb] tag#0 uas_eh_abort_handler 0 uas-tag 1 inflight: IN",
    "scsi host1: uas_eh_device_reset_handler start",
    "usb 2-1: reset SuperSpeed USB device number 4 using xhci_hcd",
    "usb 2-1: USB disconnect, device number 4",
    "blk_update_request: I/O error, dev sdb, sector 1234567 op 0x1:(WRITE)",
    "Buffer I/O error on dev sdb, logical block 154321, lost async page write",
    "sd 1:0:0:0: rejecting I/O to offline device",
    "EXT4-fs error (device sdb): ext4_journal_check_start:83: Detected aborted journal",
    "EXT4-fs (sdb): Remounting filesystem read-only",
]


class TestClassify(unittest.TestCase):
    def test_clean_boot_produces_no_faults(self):
        """A healthy boot must classify as entirely fault-free.

        This is the false-positive guard: if enumeration lines counted as
        resets, the alert would fire on every reboot and be muted within a
        week — the alert-fatigue failure mode this repo has hit before.
        """
        counts = kio.scan(CLEAN_BOOT)
        self.assertEqual(
            {k: v for k, v in counts.items() if v},
            {},
            f"clean boot misclassified: {counts}",
        )

    def test_incident_lines_are_all_classified(self):
        for line in DROPOUT:
            with self.subTest(line=line):
                self.assertIsNotNone(
                    kio.classify(line), f"unclassified incident line: {line!r}"
                )

    def test_incident_hits_both_precursor_and_fatal(self):
        counts = kio.scan(DROPOUT)
        precursor = sum(counts[c] for c in kio.PRECURSOR_CLASSES)
        fatal = sum(counts[c] for c in kio.FATAL_CLASSES)
        self.assertGreater(precursor, 0, f"no precursor signal: {counts}")
        self.assertGreater(fatal, 0, f"no fatal signal: {counts}")

    def test_remount_ro_wins_over_generic_ext4_error(self):
        """Ordering matters: the terminal event must not be swallowed."""
        line = "EXT4-fs error (device sdb): Remounting filesystem read-only"
        self.assertEqual(kio.classify(line), "ext4_remount_ro")

    def test_real_incident_is_detected(self):
        """THE regression guard: the real 2026-08-01 log must alert.

        The only precursor logged was a SINGLE `USB disconnect`, so this
        pins that a >=1 dropout threshold (not the >=2 reset threshold)
        is what makes the incident visible.
        """
        counts = kio.scan(INCIDENT_REAL)
        dropout = sum(
            counts[c] for c in kio.CLASSES if kio.severity_of(c) == "dropout"
        )
        self.assertEqual(counts["usb_disconnect"], 1)
        self.assertEqual(dropout, 1, f"dropout tier must fire on 1 event: {counts}")
        self.assertGreater(
            counts["disk_zero_capacity"], 0, f"zero-capacity signature missed: {counts}"
        )

    def test_real_incident_logs_no_ext4_fault(self):
        """Documents the hard limitation this design must live with.

        journald could not persist the ext4 failure — it was on the disk
        that failed. If this ever starts failing, the incident doc's
        reasoning needs revisiting.
        """
        counts = kio.scan(INCIDENT_REAL)
        self.assertEqual(counts["ext4_remount_ro"], 0)
        self.assertEqual(counts["ext4_error"], 0)

    def test_healthy_enumeration_is_not_zero_capacity(self):
        """A real disk reporting real geometry must not trip the guard."""
        self.assertIsNone(
            kio.classify("sd 1:0:0:0: [sda] 1953525168 512-byte logical blocks: (1.00 TB/932 GiB)")
        )

    def test_over_current_detected(self):
        """Pi USB power-budget exhaustion — the suspected 2026-08-01 root cause."""
        line = "usb 2-2: over-current condition"
        self.assertEqual(kio.classify(line), "usb_over_current")

    def test_new_device_is_not_a_reset(self):
        self.assertIsNone(
            kio.classify("usb 2-2: new SuperSpeed USB device number 3 using xhci_hcd")
        )


class TestRender(unittest.TestCase):
    def test_all_classes_zero_filled(self):
        """Absent series make vmalert rules silently un-fireable.

        Emitting every class at 0 means the rule always has a series to
        evaluate. This is the textfile-collector rule in CLAUDE.md.
        """
        out = kio.render_prom(kio.scan([]), now=1785610812)
        for name in kio.CLASSES:
            self.assertIn(f'class="{name}"', out, f"{name} missing from exposition")

    def test_help_and_type_lines_present(self):
        out = kio.render_prom(kio.scan([]), now=1785610812)
        self.assertIn("# HELP homelab_kernel_io_events", out)
        self.assertIn("# TYPE homelab_kernel_io_events gauge", out)
        self.assertIn("# TYPE homelab_kernel_io_scan_timestamp_seconds gauge", out)

    def test_timestamp_is_integer_formatted(self):
        """node_exporter rejects the file if any value is non-numeric."""
        out = kio.render_prom(kio.scan([]), now=1785610812.987)
        self.assertIn("homelab_kernel_io_scan_timestamp_seconds 1785610813", out)

    def test_every_value_is_numeric(self):
        out = kio.render_prom(kio.scan(DROPOUT), now=1785610812)
        for line in out.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            value = line.rsplit(" ", 1)[1]
            with self.subTest(line=line):
                float(value)  # raises if node_exporter would choke

    def test_severity_label_matches_class_split(self):
        out = kio.render_prom(kio.scan([]), now=1)
        for line in out.splitlines():
            if not line.startswith("homelab_kernel_io_events"):
                continue
            name = line.split('class="', 1)[1].split('"', 1)[0]
            self.assertIn(f'severity="{kio.severity_of(name)}"', line)

    def test_class_split_is_exhaustive(self):
        """Every class must be exactly one of fatal/precursor, no overlap.

        A class that fell out of both sets would still be emitted but no
        alert rule would select it — silently unmonitored.
        """
        self.assertEqual(set(kio.CLASSES), kio.FATAL_CLASSES | kio.PRECURSOR_CLASSES)
        self.assertEqual(kio.FATAL_CLASSES & kio.PRECURSOR_CLASSES, frozenset())
        self.assertTrue(kio.NEVER_NORMAL_CLASSES <= kio.PRECURSOR_CLASSES)

    def test_every_severity_tier_is_covered_by_a_rule(self):
        """The three tiers must match what host-saturation.yml.j2 selects."""
        emitted = {kio.severity_of(c) for c in kio.CLASSES}
        self.assertEqual(emitted, {"fatal", "dropout", "precursor"})
        rules = (
            REPO
            / "ansible/roles/vmalert/templates/rules/host-saturation.yml.j2"
        ).read_text()
        for tier in sorted(emitted):
            with self.subTest(tier=tier):
                self.assertIn(
                    f'severity="{tier}"', rules, f"no vmalert rule selects {tier}"
                )


class TestLastBootSnapshot(unittest.TestCase):
    """The per-boot gauges are erased by the reboot they exist to report.

    Found 2026-08-02. The scanner is correct in steady state, but its primary
    target failure is *dropout -> host death -> reboot*, and a reboot resets
    every counter to 0 before the vmalert `for:` window can elapse. Verified
    live: a usb_disconnect ended a boot at 02:58 and every class in
    kernel_io.prom then read 0. The evidence for the outage is destroyed by
    the outage.

    dmesg cannot help here — it is an in-RAM ring buffer, so after a reboot it
    holds nothing from the previous boot. But the .prom FILE is on disk and
    survives, so on the first scan of a new boot it still contains the dead
    boot's counts. Snapshot them into `*_last_boot` before overwriting.

    Boot identity comes from /proc/sys/kernel/random/boot_id (stable for the
    life of a boot, regenerated on every boot), passed in by the wrapper so
    this stays pure and testable.
    """

    BOOT_A = "aaaaaaaa-1111-2222-3333-444444444444"
    BOOT_B = "bbbbbbbb-5555-6666-7777-888888888888"

    def _counts(self, **kw):
        c = dict.fromkeys(kio.CLASSES, 0)
        c.update(kw)
        return c

    def test_render_emits_boot_id(self):
        """Without a persisted boot id, 'is this a new boot' is unanswerable."""
        out = kio.render_prom(self._counts(), 1785635101, boot_id=self.BOOT_A)
        self.assertIn(f'boot_id="{self.BOOT_A}"', out)

    def test_parse_prom_roundtrips_boot_id_and_counts(self):
        out = kio.render_prom(
            self._counts(usb_disconnect=2, ext4_remount_ro=1),
            1785635101,
            boot_id=self.BOOT_A,
        )
        prev = kio.parse_prom(out)
        self.assertEqual(prev["boot_id"], self.BOOT_A)
        self.assertEqual(prev["counts"]["usb_disconnect"], 2)
        self.assertEqual(prev["counts"]["ext4_remount_ro"], 1)
        self.assertEqual(prev["counts"]["usb_reset"], 0)

    def test_new_boot_promotes_previous_counts_to_last_boot(self):
        """THE regression this class exists for.

        Replays 2026-08-02: boot A ends having logged a usb_disconnect, the
        host dies, boot B starts clean. The disconnect must still be visible.
        """
        dead_boot = kio.render_prom(
            self._counts(usb_disconnect=1, scsi_offline=1),
            1785600000,
            boot_id=self.BOOT_A,
        )
        last = kio.carry_forward(kio.parse_prom(dead_boot), self.BOOT_B)
        self.assertEqual(last["usb_disconnect"], 1, "the dropout that killed boot A was lost")
        self.assertEqual(last["scsi_offline"], 1)
        self.assertEqual(last["usb_reset"], 0)

    def test_same_boot_does_not_promote_current_counts(self):
        """A 5-minute rescan within one boot must not shift live counts.

        Otherwise every scan would 'promote' this boot's faults and
        _last_boot would just mirror the live series, which observes nothing.
        """
        prev_text = kio.render_prom(
            self._counts(usb_reset=3), 1785600000, boot_id=self.BOOT_A,
            last_boot=self._counts(usb_disconnect=1),
        )
        last = kio.carry_forward(kio.parse_prom(prev_text), self.BOOT_A)
        self.assertEqual(last["usb_reset"], 0, "live counts leaked into _last_boot")
        self.assertEqual(last["usb_disconnect"], 1, "real last-boot value was dropped")

    def test_missing_previous_file_yields_zeros_not_a_crash(self):
        """First-ever run, or a wiped textfile dir."""
        last = kio.carry_forward(kio.parse_prom(""), self.BOOT_A)
        self.assertEqual(set(last), set(kio.CLASSES))
        self.assertEqual(sum(last.values()), 0)

    def test_garbage_previous_file_yields_zeros_not_a_crash(self):
        """A truncated .prom (ENOSPC on a 97%-full root) must not crash.

        The wrapper already guards against emitting a partial file, but the
        reader must survive one that predates that guard.
        """
        last = kio.carry_forward(kio.parse_prom("homelab_kernel_io_ev\x00garbage"), self.BOOT_A)
        self.assertEqual(sum(last.values()), 0)

    def test_last_boot_series_is_rendered_with_all_classes(self):
        out = kio.render_prom(
            self._counts(), 1785635101, boot_id=self.BOOT_A,
            last_boot=self._counts(usb_disconnect=1),
        )
        for cls in kio.CLASSES:
            with self.subTest(cls=cls):
                self.assertIn(f'homelab_kernel_io_events_last_boot{{class="{cls}"', out)
        self.assertIn(
            'homelab_kernel_io_events_last_boot{class="usb_disconnect",'
            'severity="dropout"} 1', out,
        )

    def test_last_boot_values_are_numeric(self):
        """Same guarantee TestRender enforces for the live series."""
        out = kio.render_prom(
            self._counts(), 1785635101, boot_id=self.BOOT_A,
            last_boot=self._counts(usb_disconnect=2),
        )
        for line in out.splitlines():
            if line.startswith("homelab_kernel_io_events_last_boot{"):
                with self.subTest(line=line):
                    float(line.rsplit(" ", 1)[1])

    def test_backward_compatible_when_boot_id_omitted(self):
        """render_prom's original 2-arg call must keep working.

        The wrapper is deployed separately from the module; during a partial
        rollout the old shell may still call the new module.
        """
        out = kio.render_prom(self._counts(usb_reset=1), 1785635101)
        self.assertIn('homelab_kernel_io_events{class="usb_reset"', out)
        self.assertIn("homelab_kernel_io_scan_timestamp_seconds 1785635101", out)

    def test_last_boot_is_covered_by_a_vmalert_rule(self):
        """A snapshot nothing selects is a snapshot that observes nothing.

        Same failure shape as an emitted-but-unwatched textfile metric, which
        this repo has shipped before (node_exporter_textfile memory).

        Asserted against `expr:` lines only — a mention in a description is
        prose, not coverage, and would let this pass while nothing evaluates
        the series.
        """
        rules = (
            REPO / "ansible/roles/vmalert/templates/rules/host-saturation.yml.j2"
        ).read_text()
        in_expr, found = False, False
        for line in rules.splitlines():
            stripped = line.strip()
            if stripped.startswith("expr:"):
                in_expr = True
            elif stripped.startswith(("for:", "labels:", "annotations:", "- alert:")):
                in_expr = False
            if in_expr and "homelab_kernel_io_events_last_boot" in line:
                found = True
                break
        self.assertTrue(
            found,
            "no vmalert rule EVALUATES homelab_kernel_io_events_last_boot — "
            "the snapshot would be emitted but unobserved",
        )


if __name__ == "__main__":
    unittest.main()
