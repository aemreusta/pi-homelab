# 2026-08-02 — Declared the USB 2.0 workaround "resolved" one boot early; it wasn't

- **What happened:** After two USB3 failures, the Kingston was moved to a USB 2.0 port. Two clean cold boots plus a passing 91-second concurrent-read load test looked conclusive, so it was documented as **"THE WORKING ANSWER"**, committed (`f18dd94`), and the four-phase capacity plan was written on top of it. The operator then asked to start the rollout with a reboot. That third boot went **read-only ~10 minutes in**, with the drive physically confirmed on the black port and `lsusb -t` showing `480M / usb-storage`. Fourth outage of the night.
- **Why:** Two independent failures, one technical and one procedural. **Technical:** the USB 2.0 current cap (500 mA vs 900 mA) lowers the spin-up transient but leaves the ~1.2 A budget marginal in steady state. The failing boot's persisted kernel log is completely clean — Kingston enumerated normally, the SanDisk reported its full `3907029168 512-byte logical blocks`, ext4 mounted `r/w` — and the journal simply stops with no storage error, because the device vanished ~10 minutes after a healthy spin-up and journald could no longer write. So this failure mode produces **no warning window at all**, unlike the USB3 ones. **Procedural:** the doc that declared success had itself set the bar at "three clean boots, spin-up is a race, one clean boot proves nothing" — and was published at two.
- **Class:** Premature generalisation from a passing test. A load test and two samples were treated as proof of stability for a *marginal-power* failure whose defining property is that it fires on an unpredictable transient. Sibling of the "verify plan premises against live state" rule in [ops-discipline §4](../operations/ops-discipline.md), but the inverted form: not an unverified claim inherited from elsewhere, a claim **we generated ourselves** and then built a plan on.
- **Occurrence:** 3rd of the USB power-budget class in ~6 hours ([boot](2026-08-01-usb-ssd-root-dropout-readonly.md), [hot-plug](2026-08-01-usb-ssd-hotplug-readonly-recurrence.md), this). 1st of the premature-resolution class in this repo.
- **Structural fix:** Documentation corrected in place rather than rewritten — [design 042 §1b](../design/042-second-ssd-integration-plan.md) and [the convention doc](../conventions/pi4-usb-power-budget.md) both carry an explicit CORRECTION block, so a future reader sees the wrong conclusion *and* why it was wrong. Status moved to **BLOCKED on a powered USB3 hub**; the drive stays physically unplugged until then, since it is unformatted and unused and therefore all risk and no benefit. The capacity plan (§2–§5) is unchanged and starts the day the hub arrives.

## The rule this earns

**A passing load test does not establish stability for a marginal-power fault.**
Only elapsed time does. When the failure mode is "some transient tips the budget",
the sample that matters is *hours of uptime under normal operation*, not a
synthetic burst that happens to fit inside a good window.

Corollary, and the part that actually went wrong here: **when you write down an
acceptance criterion, meeting it is not optional.** The three-boot bar was correct,
was recorded in the same document, and was then skipped because the first two
results were encouraging. Encouraging results are exactly when the bar matters.

## Also worth keeping

Every prior failure announced itself in the kernel log — `USB disconnect`, then
`Read Capacity failed`, then a 0-byte disk. This one announced nothing. The
`disk_zero_capacity` / `usb_disconnect` detection shipped in `60d8dee` is real and
still valuable, but it covers the *spin-up* failure shape only. A steady-state
dropout is caught solely by `RootFilesystemReadOnly` (node_exporter's in-memory
gauge) and by the off-Pi watchdog — which remains an open P0 and was, once again, the
only thing that actually noticed.
