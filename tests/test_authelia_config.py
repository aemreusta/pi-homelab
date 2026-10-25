"""Render-and-parse guard for the Authelia configuration template.

Why this exists
---------------
On 2026-07-22 the Authelia log was found emitting a low but steady stream of:

    level=error msg="Request timeout occurred while handling request from
    client." error="read tcp 192.168.80.3:9091->192.168.80.1:56378: i/o
    timeout" method=GET path=/ status_code=408

These are host→container GET `/` requests to the Authelia portal (the source is
the mlstack_net gateway, NOT the forward-auth path — host-networked Traefik
reaches Authelia over loopback) hitting Authelia's *default* server read timeout
of 6s. On a disk-saturated Pi (root fs at 95%) the portal SPA at `/` can take
longer than 6s to serve, and an idle keepalive connection past the 30s default
idle timeout is logged the same way. The fix raises `server.timeouts` above the
defaults so a loaded Pi stops logging these as errors.

This test is the fast pre-deploy CI catch (no Docker / `authelia
validate-config` needed): render the Jinja2 surface, parse it as YAML, and
assert the raised timeouts survive — plus the security invariants (deny-by-
default, loopback bind) that must never silently regress.

The deploy still gates on the authoritative `authelia validate-config`
(deploy.yml); this test guards the intent that validate-config cannot see (a
6s timeout is *valid* config — it just reintroduces the 408 noise).
"""
from __future__ import annotations

import unittest
from pathlib import Path

try:
    import jinja2
except ImportError as exc:  # pragma: no cover
    raise SystemExit("jinja2 required for test_authelia_config.py — pip install jinja2") from exc

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("pyyaml required for test_authelia_config.py — pip install pyyaml") from exc

REPO = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO / "ansible" / "roles" / "authelia" / "templates"
TEMPLATE = "configuration.yml.j2"

# The Authelia default read/write timeout is 6s and idle is 30s. The 408 noise
# fix raises them; if anyone reverts below these floors the portal-timeout class
# returns, so the test pins a minimum, not an exact value.
_MIN_READ_SECONDS = 20
_MIN_IDLE_SECONDS = 45


def _duration_seconds(value) -> int:
    """Parse an Authelia duration ('30s', '1m', 90) into whole seconds."""
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    units = {"s": 1, "m": 60, "h": 3600}
    if text[-1] in units:
        return int(float(text[:-1]) * units[text[-1]])
    return int(float(text))


def _render() -> str:
    """Render with a realistic context so the YAML is fully valid and parseable."""
    ctx = {
        "authelia_log_level": "info",
        "authelia_metrics_port": 9959,
        "authelia_jwt_secret": "x" * 40,
        "authelia_forward_auth_services": ["sonarr", "radarr"],
        "authelia_domains": ["example.internal"],
        "authelia_session_secret": "y" * 40,
        "authelia_default_redirect_service": "homepage",
        "authelia_storage_encryption_key": "z" * 40,
        "authelia_db_host": "postgresql",
        "authelia_db_port": 5432,
        "authelia_db_name": "authelia",
        "authelia_db_user": "authelia",
        "authelia_db_password": "pw",
        "authelia_oidc_clients": [],
        "authelia_oidc_hmac_secret": "h" * 40,
        # Unused (oidc_clients is empty); a placeholder that is deliberately NOT a
        # real PEM header so the detect-private-key pre-commit hook stays quiet.
        "authelia_oidc_issuer_private_key": "TEST-OIDC-KEY-PLACEHOLDER",
    }
    env = jinja2.Environment(  # noqa: S701 - YAML, not HTML
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    return env.get_template(TEMPLATE).render(**ctx)


class TestAutheliaConfig(unittest.TestCase):
    def setUp(self):
        self.rendered = _render()
        self.doc = yaml.safe_load(self.rendered)

    def test_renders_to_valid_yaml_mapping(self):
        self.assertIsInstance(self.doc, dict, "configuration.yml top level is not a mapping")
        self.assertIn("server", self.doc)
        self.assertIn("access_control", self.doc)

    def test_server_timeouts_raised_above_defaults(self):
        """The 408-portal-timeout fix: read/idle timeouts must exceed the 6s/30s
        Authelia defaults so a disk-saturated Pi stops logging portal GETs as
        408 errors."""
        server = self.doc.get("server", {})
        timeouts = server.get("timeouts")
        self.assertIsInstance(
            timeouts,
            dict,
            "server.timeouts block missing — reintroduces the 2026-07-22 "
            "portal-408 log-error noise on a loaded Pi",
        )
        self.assertGreaterEqual(
            _duration_seconds(timeouts.get("read", "6s")),
            _MIN_READ_SECONDS,
            f"server.timeouts.read must be >= {_MIN_READ_SECONDS}s (default 6s 408s the portal)",
        )
        self.assertGreaterEqual(
            _duration_seconds(timeouts.get("idle", "30s")),
            _MIN_IDLE_SECONDS,
            f"server.timeouts.idle must be >= {_MIN_IDLE_SECONDS}s (default 30s)",
        )

    def test_security_invariants_hold(self):
        """Guard the two properties that must never silently regress."""
        self.assertEqual(
            self.doc["access_control"]["default_policy"],
            "deny",
            "access_control.default_policy must stay 'deny' (fail-closed)",
        )
        # Authelia binds all interfaces INSIDE the container; the host publish is
        # loopback-only (defaults). The listen port must remain 9091.
        self.assertIn("9091", str(self.doc["server"].get("address", "")))


if __name__ == "__main__":
    unittest.main()
