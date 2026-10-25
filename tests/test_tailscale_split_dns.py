"""Tests for tailscale_split_dns — Tailscale split-DNS route drift detection.

Module under test:
``ansible/roles/tailscale_dns/files/tailscale_split_dns.py``.

Why this exists (2026-08-05). The 2026-07-15 ``.example`` -> ``.example.internal``
cut updated AdGuard, dnsmasq, Traefik, Authelia and every var in the repo — but
the Tailscale **split-DNS route** is tailnet control-plane state that lives in
the admin console, so Ansible could not carry it and nobody noticed it still
said ``example``. Result: MagicDNS answered NXDOMAIN for every
``*.example.internal`` name for three weeks. Every container resolves through
``127.0.0.11 -> 100.100.100.100 (tailscaled)``, so this silently broke the
Uptime Kuma Filebrowser monitor and dropped ntfy-bridge alerts, while
``tailscale dns status`` looked plausible to a human skimming it.

The load-bearing rule is :func:`covers`: a split-DNS route for ``X`` covers
``X`` and ``*.X`` — it does **not** cover ``X.suffix``. ``example`` does not
cover ``example.internal``. That single fact is the whole bug, so it gets a
dedicated test.

Run:  python3 -m pytest tests/test_tailscale_split_dns.py -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest

MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "ansible",
    "roles",
    "tailscale_dns",
    "files",
    "tailscale_split_dns.py",
)

NS = "100.64.0.10"


def _load():
    spec = importlib.util.spec_from_file_location("tailscale_split_dns", MODULE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tailscale_split_dns"] = mod
    spec.loader.exec_module(mod)
    return mod


TS = _load()


class Covers(unittest.TestCase):
    """Suffix semantics of a split-DNS route."""

    def test_exact_match(self):
        self.assertTrue(TS.covers("example.internal", "example.internal"))

    def test_subdomain_is_covered(self):
        self.assertTrue(TS.covers("example.internal", "auth.example.internal"))
        self.assertTrue(TS.covers("example.internal", "a.b.example.internal"))

    def test_THE_BUG_parent_label_does_not_cover_longer_domain(self):
        # The entire 2026-08-05 incident in one assertion: a route for
        # `example` does NOT cover `example.internal`. If this ever passes as
        # True, the drift check goes blind exactly the way the console did.
        self.assertFalse(TS.covers("example", "example.internal"))

    def test_sibling_domains_do_not_match(self):
        self.assertFalse(TS.covers("example.internal", "homelab.example"))
        self.assertFalse(TS.covers("lab.internal", "example.internal"))

    def test_partial_label_is_not_a_suffix_match(self):
        # "example.internal" must not be matched by "lab.internal" via a naive
        # str.endswith — the boundary has to be a full label.
        self.assertFalse(TS.covers("ab.internal", "example.internal"))

    def test_trailing_dot_is_normalised(self):
        # Tailscale reports some routes fully-qualified ("ts.net.").
        self.assertTrue(TS.covers("example.internal.", "auth.example.internal"))
        self.assertTrue(TS.covers("example.internal", "auth.example.internal."))

    def test_case_insensitive(self):
        self.assertTrue(TS.covers("Example.Internal", "AUTH.example.internal"))


class DecideProductionBug(unittest.TestCase):
    """The exact live state on 2026-08-05."""

    ACTUAL = {
        "example": [NS],
        "ts.net.": ["199.247.155.53", "2620:111:8007::53"],
    }

    def test_required_domain_is_reported_missing(self):
        v = TS.decide(self.ACTUAL, ["example.internal"], NS)
        self.assertEqual(v.missing, ["example.internal"])
        self.assertFalse(v.ok)

    def test_dead_route_to_our_nameserver_is_reported_stale(self):
        v = TS.decide(self.ACTUAL, ["example.internal"], NS)
        self.assertEqual(v.stale, ["example"])

    def test_third_party_routes_are_left_alone(self):
        # ts.net. does not point at our nameserver, so it is none of our
        # business. Flagging it would make the alert unactionable and would
        # make a naive "replace whole config" fix delete someone else's route.
        v = TS.decide(self.ACTUAL, ["example.internal"], NS)
        self.assertNotIn("ts.net.", v.stale)
        self.assertNotIn("ts.net.", v.desired_patch)

    def test_patch_adds_required_and_clears_stale(self):
        v = TS.decide(self.ACTUAL, ["example.internal"], NS)
        self.assertEqual(v.desired_patch, {"example.internal": [NS], "example": None})


class DecideHealthy(unittest.TestCase):
    def test_correct_config_is_ok_and_needs_no_patch(self):
        actual = {"example.internal": [NS], "ts.net.": ["199.247.155.53"]}
        v = TS.decide(actual, ["example.internal"], NS)
        self.assertTrue(v.ok)
        self.assertEqual(v.missing, [])
        self.assertEqual(v.stale, [])
        self.assertEqual(v.misrouted, [])
        self.assertEqual(v.desired_patch, {})

    def test_subdomain_route_satisfies_requirement(self):
        # A route on the apex covers children; requiring the apex is satisfied
        # by the apex only.
        actual = {"example.internal": [NS]}
        v = TS.decide(actual, ["example.internal"], NS)
        self.assertTrue(v.ok)

    def test_extra_nameservers_on_a_required_domain_are_acceptable(self):
        # Ours is present; a secondary resolver alongside it is not drift.
        actual = {"example.internal": [NS, "10.0.0.53"]}
        v = TS.decide(actual, ["example.internal"], NS)
        self.assertTrue(v.ok)
        self.assertEqual(v.misrouted, [])


class DecideMisrouted(unittest.TestCase):
    def test_route_exists_but_points_elsewhere(self):
        actual = {"example.internal": ["10.9.9.9"]}
        v = TS.decide(actual, ["example.internal"], NS)
        self.assertEqual(v.misrouted, ["example.internal"])
        self.assertEqual(v.missing, [])
        self.assertFalse(v.ok)

    def test_misrouted_domain_is_repointed_by_the_patch(self):
        actual = {"example.internal": ["10.9.9.9"]}
        v = TS.decide(actual, ["example.internal"], NS)
        self.assertEqual(v.desired_patch["example.internal"], [NS])


class DecideMultiDomain(unittest.TestCase):
    def test_partial_coverage_reports_only_the_gap(self):
        actual = {"example.internal": [NS]}
        v = TS.decide(actual, ["example.internal", "lab.internal"], NS)
        self.assertEqual(v.missing, ["lab.internal"])
        self.assertFalse(v.ok)
        self.assertEqual(v.desired_patch, {"lab.internal": [NS]})

    def test_all_domains_present(self):
        actual = {"example.internal": [NS], "lab.internal": [NS]}
        v = TS.decide(actual, ["example.internal", "lab.internal"], NS)
        self.assertTrue(v.ok)

    def test_output_order_is_deterministic(self):
        actual: dict[str, list[str]] = {}
        v = TS.decide(actual, ["b.internal", "a.internal"], NS)
        # Preserves the caller's declared order (homelab_internal_domains is
        # ordered — element 0 is the canonical domain).
        self.assertEqual(v.missing, ["b.internal", "a.internal"])


class DecideDegenerate(unittest.TestCase):
    def test_no_routes_at_all(self):
        v = TS.decide({}, ["example.internal"], NS)
        self.assertEqual(v.missing, ["example.internal"])
        self.assertEqual(v.stale, [])
        self.assertFalse(v.ok)

    def test_no_required_domains_is_vacuously_ok(self):
        v = TS.decide({"example": [NS]}, [], NS)
        # Nothing required, but the dead route still points at us -> stale.
        self.assertEqual(v.missing, [])
        self.assertEqual(v.stale, ["example"])
        # stale alone does not make it not-ok: nothing is broken, just untidy.
        self.assertTrue(v.ok)

    def test_blank_domains_are_ignored(self):
        v = TS.decide({}, ["", "  ", "example.internal"], NS)
        self.assertEqual(v.missing, ["example.internal"])


class ParseStatus(unittest.TestCase):
    """Parsing `tailscale dns status --json`."""

    REAL = json.dumps(
        {
            "TailscaleDNS": True,
            "SplitDNSRoutes": {
                "example": [{"Addr": NS}],
                "ts.net.": [{"Addr": "199.247.155.53"}, {"Addr": "2620:111:8007::53"}],
            },
            "SearchDomains": ["tail0000.ts.net"],
        }
    )

    def test_extracts_domain_to_addr_map(self):
        self.assertEqual(
            TS.parse_status(self.REAL),
            {"example": [NS], "ts.net.": ["199.247.155.53", "2620:111:8007::53"]},
        )

    def test_missing_split_dns_key_is_empty_not_an_error(self):
        # A tailnet with no split DNS configured is a legitimate state, and it
        # must read as "nothing routed" (-> missing), never as a parse crash.
        self.assertEqual(TS.parse_status(json.dumps({"TailscaleDNS": True})), {})

    def test_null_split_dns_is_empty(self):
        self.assertEqual(TS.parse_status(json.dumps({"SplitDNSRoutes": None})), {})

    def test_malformed_json_raises(self):
        with self.assertRaises(ValueError):
            TS.parse_status("{not json")

    def test_tolerates_entry_without_addr(self):
        raw = json.dumps({"SplitDNSRoutes": {"x.internal": [{}, {"Addr": NS}]}})
        self.assertEqual(TS.parse_status(raw), {"x.internal": [NS]})


class Cli(unittest.TestCase):
    def _run(self, stdin, *args):
        return subprocess.run(  # noqa: S603 — fixed argv, test invoking the module
            [sys.executable, MODULE_PATH, *args],
            input=stdin,
            capture_output=True,
            text=True,
        )

    def test_reports_drift_and_exits_nonzero(self):
        raw = json.dumps({"SplitDNSRoutes": {"example": [{"Addr": NS}]}})
        p = self._run(raw, "--domains", "example.internal", "--nameserver", NS)
        self.assertEqual(p.returncode, 1, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(out["missing"], ["example.internal"])
        self.assertEqual(out["stale"], ["example"])
        self.assertEqual(out["ok"], 0)

    def test_healthy_exits_zero(self):
        raw = json.dumps({"SplitDNSRoutes": {"example.internal": [{"Addr": NS}]}})
        p = self._run(raw, "--domains", "example.internal", "--nameserver", NS)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["ok"], 1)

    def test_comma_separated_domains(self):
        raw = json.dumps({"SplitDNSRoutes": {"example.internal": [{"Addr": NS}]}})
        p = self._run(raw, "--domains", "example.internal,lab.internal", "--nameserver", NS)
        self.assertEqual(json.loads(p.stdout)["missing"], ["lab.internal"])

    def test_unparseable_input_exits_2_and_still_emits_json(self):
        # The caller is a cron emitting a metric; it must always get a verdict
        # object back so it can emit scrape_ok=0 rather than write nothing.
        p = self._run("{not json", "--domains", "example.internal", "--nameserver", NS)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(json.loads(p.stdout)["scrape_ok"], 0)

    def test_patch_body_is_emitted_for_operator_use(self):
        raw = json.dumps({"SplitDNSRoutes": {"example": [{"Addr": NS}]}})
        p = self._run(raw, "--domains", "example.internal", "--nameserver", NS)
        out = json.loads(p.stdout)
        self.assertEqual(out["desired_patch"], {"example.internal": [NS], "example": None})


if __name__ == "__main__":
    unittest.main()
