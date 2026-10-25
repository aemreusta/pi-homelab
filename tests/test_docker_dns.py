"""Container DNS must not depend on the Tailscale control plane.

Guards ``ansible/roles/docker/templates/daemon.json.j2``.

Context (2026-08-05). Docker containers inherit the host's resolver by default.
On this Pi the host's ``/etc/resolv.conf`` is written by tailscaled and points
at ``100.100.100.100``, so the real chain for every one of the 40 containers was
``127.0.0.11 -> 100.100.100.100 (tailscaled) -> tailnet split-DNS route``. When
the split-DNS route went stale at the 2026-07-15 domain cut, MagicDNS answered
NXDOMAIN and every container lost the ability to resolve ``*.example.internal``
— taking out the Uptime Kuma Filebrowser monitor and dropping ntfy-bridge
alerts for three weeks.

AdGuard is authoritative for those names, runs on this very host, and answers
correctly. Routing container DNS through a cloud coordination server to reach a
resolver one hop away is the actual defect; the stale route was only what
exposed it. Pinning ``dns`` in ``daemon.json`` removes the control plane from
the path entirely, and mirrors what NetworkManager already does for the host
(AdGuard first, public resolvers as fallback for the boot chicken-and-egg).

Invariants locked here:
  1. The rendered file is valid JSON (a syntax error here refuses dockerd start).
  2. AdGuard is FIRST — order is preference, and internal names only exist there.
  3. At least one public fallback follows, so a down AdGuard degrades to
     "external names work, internal ones don't" instead of total DNS loss.
  4. No literal IP in the template — the addresses come from vars, so a LAN
     renumber is one edit. This is the rule the backup JSON broke.

Run:  python3 -m pytest tests/test_docker_dns.py -v
"""

from __future__ import annotations

import ipaddress
import json
import re
import unittest
from pathlib import Path

try:
    import jinja2
except ImportError as exc:  # pragma: no cover
    raise SystemExit("jinja2 required for test_docker_dns.py — uv pip install jinja2") from exc

REPO = Path(__file__).resolve().parents[1]
ROLE = REPO / "ansible" / "roles" / "docker"
TEMPLATE_DIR = ROLE / "templates"
TEMPLATE = "daemon.json.j2"

LAN_IP = "192.0.2.100"
FALLBACKS = ["1.1.1.1", "9.9.9.9"]

VARS = {
    "docker_data_root": "/srv/docker",
    "docker_log_max_size": "10m",
    "docker_log_max_file": "3",
    "docker_dns_servers": [LAN_IP, *FALLBACKS],
}


def _render(**overrides) -> str:
    env = jinja2.Environment(  # noqa: S701 - JSON, not HTML
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    # `to_json` ships with Ansible, not stock Jinja2. Register an equivalent so
    # this renders the template the way Ansible will, rather than forcing the
    # template into a less idiomatic form just to suit the test harness.
    env.filters["to_json"] = json.dumps
    return env.get_template(TEMPLATE).render({**VARS, **overrides})


class RenderedDaemonJson(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(_render())

    def test_is_valid_json(self):
        # dockerd refuses to start on a malformed daemon.json, and it does so
        # AFTER the Ansible task reports changed — a template typo here is a
        # host-level outage, not a failed play.
        self.assertIsInstance(self.doc, dict)

    def test_preserves_existing_settings(self):
        self.assertEqual(self.doc["data-root"], "/srv/docker")
        self.assertEqual(self.doc["log-opts"]["max-size"], "10m")

    def test_dns_key_present(self):
        self.assertIn("dns", self.doc)
        self.assertIsInstance(self.doc["dns"], list)
        self.assertTrue(self.doc["dns"])

    def test_adguard_is_first(self):
        # Order is preference. Any public resolver ahead of AdGuard returns
        # NXDOMAIN for *.example.internal and Docker accepts that as a final
        # answer -- reintroducing the exact bug, but permanently.
        self.assertEqual(self.doc["dns"][0], LAN_IP)

    def test_has_a_public_fallback_after_adguard(self):
        self.assertGreaterEqual(len(self.doc["dns"]), 2)
        for addr in self.doc["dns"][1:]:
            ipaddress.ip_address(addr)  # raises if not a valid IP
        self.assertTrue(set(FALLBACKS) & set(self.doc["dns"][1:]))

    def test_every_entry_is_a_bare_ip(self):
        # Docker's `dns` takes addresses, not hostnames. A hostname here would
        # need DNS to resolve DNS.
        for addr in self.doc["dns"]:
            ipaddress.ip_address(addr)

    def test_no_duplicate_resolvers(self):
        self.assertEqual(len(self.doc["dns"]), len(set(self.doc["dns"])))


class TemplateHygiene(unittest.TestCase):
    def test_template_hardcodes_no_ip_literal(self):
        # The 2026-07-15 migration cost ~15 files of stragglers because values
        # like this were pasted rather than referenced. Keep the resolver list
        # a variable so a renumber stays a one-line change.
        raw = (TEMPLATE_DIR / TEMPLATE).read_text()
        found = re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", raw)
        self.assertEqual(found, [], f"hardcoded IP(s) in {TEMPLATE}: {found}")

    def test_dns_list_is_driven_by_the_variable(self):
        doc = json.loads(_render(docker_dns_servers=["10.0.0.53", "8.8.8.8"]))
        self.assertEqual(doc["dns"], ["10.0.0.53", "8.8.8.8"])

    def test_empty_dns_list_omits_the_key_entirely(self):
        # An empty `"dns": []` is not the same as absent: it tells dockerd to
        # use no resolvers at all. Setting the var to [] must mean "leave
        # Docker's default behaviour alone", so the escape hatch is safe.
        doc = json.loads(_render(docker_dns_servers=[]))
        self.assertNotIn("dns", doc)


if __name__ == "__main__":
    unittest.main()
