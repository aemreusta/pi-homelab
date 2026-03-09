"""Tests for Homelab Control API.

These tests verify the API's security and routing behaviour WITHOUT
executing any real system commands.  The dangerous functions
(os.system, subprocess.run) are patched out so that nothing is
actually shut-down, rebooted or pruned during testing.

Run:
    python3 -m pytest tests/test_control_api.py -v
"""

import http.client
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Build a *real* API module from the Jinja2 template by substituting the
# template variables with safe test values, then exec() the result into a
# fresh module namespace.
# ---------------------------------------------------------------------------
TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "ansible",
    "roles",
    "homelab_control",
    "templates",
    "control-api.py.j2",
)

TEST_TOKEN = "test-secret-token-12345"
TEST_PORT = 19099  # high port so it doesn't clash with a running instance


def _render_template():
    """Read the Jinja2 template and replace placeholders with test values."""
    with open(TEMPLATE_PATH) as fh:
        src = fh.read()
    src = src.replace("{{ homelab_control_token }}", TEST_TOKEN)
    src = src.replace("{{ homelab_control_port }}", str(TEST_PORT))
    src = src.replace("{{ docker_user }}", "testuser")
    src = src.replace("{{ homelab_control_dir }}", "/opt/homelab/control")
    src = src.replace(
        "{{ ansible_default_ipv4.address | default('192.168.1.100') }}",
        "127.0.0.1",
    )
    return src


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start_server(handler_cls, port):
    """Start the HTTP server in a daemon thread and return it."""
    import http.server as hs

    srv = hs.HTTPServer(("127.0.0.1", port), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    # Give the server a moment to bind
    time.sleep(0.3)
    return srv


def _request(method, path, port=TEST_PORT, headers=None):
    """Send a request and return (status, body_dict)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, headers=headers or {})
    resp = conn.getresponse()
    body = json.loads(resp.read().decode())
    conn.close()
    return resp.status, body


def _raw_request(method, path, port=TEST_PORT, headers=None):
    """Send a request and return (status, content_type, raw_body)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, headers=headers or {})
    resp = conn.getresponse()
    content_type = resp.getheader("Content-Type", "")
    body = resp.read().decode()
    conn.close()
    return resp.status, content_type, body


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------

class TestControlAPI(unittest.TestCase):
    """Test the Control API HTTP server."""

    server = None

    @classmethod
    def setUpClass(cls):
        """Compile the template and start the server (with os.system mocked)."""
        src = _render_template()
        # Execute the template source in an isolated namespace
        ns = {}
        exec(compile(src, "control-api.py", "exec"), ns)  # noqa: S102
        cls.Handler = ns["Handler"]
        cls.server = _start_server(cls.Handler, TEST_PORT)

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.shutdown()

    # -- Health endpoint (no auth) ------------------------------------------

    def test_health_returns_ok(self):
        status, body = _request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok"})

    def test_health_no_auth_required(self):
        """Health endpoint must work without any Authorization header."""
        status, _ = _request("GET", "/health")
        self.assertEqual(status, 200)

    # -- Auth enforcement ---------------------------------------------------

    def test_post_without_token_returns_401(self):
        status, body = _request("POST", "/shutdown")
        self.assertEqual(status, 401)
        self.assertEqual(body, {"error": "unauthorized"})

    def test_post_with_wrong_token_returns_401(self):
        headers = {"Authorization": "Bearer wrong-token"}
        status, body = _request("POST", "/shutdown", headers=headers)
        self.assertEqual(status, 401)

    def test_post_with_empty_bearer_returns_401(self):
        headers = {"Authorization": "Bearer "}
        status, body = _request("POST", "/restart", headers=headers)
        self.assertEqual(status, 401)

    def test_post_with_no_bearer_prefix_returns_401(self):
        headers = {"Authorization": TEST_TOKEN}
        status, body = _request("POST", "/shutdown", headers=headers)
        self.assertEqual(status, 401)

    # -- Valid auth ---------------------------------------------------------

    @patch("os.system")
    def test_shutdown_with_valid_token(self, mock_system):
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("POST", "/shutdown", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "shutting down")
        # Verify the shutdown script was called (backgrounded)
        mock_system.assert_called_once()
        call_arg = mock_system.call_args[0][0]
        self.assertIn("shutdown.sh", call_arg)

    @patch("os.system")
    def test_restart_with_valid_token(self, mock_system):
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("POST", "/restart", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "restarting")
        mock_system.assert_called_once()
        call_arg = mock_system.call_args[0][0]
        self.assertIn("restart.sh", call_arg)

    @patch("subprocess.run")
    def test_update_with_valid_token(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="pulled", stderr="")
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("POST", "/update", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "updated")

    @patch("subprocess.run")
    def test_clean_with_valid_token(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Total reclaimed space: 1.2GB", stderr=""
        )
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("POST", "/clean", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "cleaned")

    # -- Unknown routes -----------------------------------------------------

    def test_get_unknown_path_returns_404(self):
        status, body = _request("GET", "/nonexistent")
        self.assertEqual(status, 404)

    def test_post_unknown_path_returns_404(self):
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("POST", "/nonexistent", headers=headers)
        self.assertEqual(status, 404)

    # -- GET on action endpoints should fail --------------------------------

    def test_get_shutdown_returns_404(self):
        """GET /shutdown must NOT trigger shutdown — only POST is allowed."""
        status, body = _request("GET", "/shutdown")
        self.assertEqual(status, 404)

    def test_get_restart_returns_404(self):
        status, body = _request("GET", "/restart")
        self.assertEqual(status, 404)

    def test_get_update_returns_404(self):
        status, body = _request("GET", "/update")
        self.assertEqual(status, 404)

    def test_get_clean_returns_404(self):
        status, body = _request("GET", "/clean")
        self.assertEqual(status, 404)

    # -- Web panel endpoint -------------------------------------------------

    def test_panel_without_token_returns_401(self):
        """GET / without token must return 401."""
        status, body = _request("GET", "/")
        self.assertEqual(status, 401)
        self.assertEqual(body, {"error": "unauthorized"})

    def test_panel_with_wrong_token_returns_401(self):
        status, body = _request("GET", "/?token=wrong-token")
        self.assertEqual(status, 401)

    def test_panel_with_valid_token_returns_html(self):
        """GET /?token=<valid> must return 200 with HTML."""
        status, ctype, body = _raw_request("GET", f"/?token={TEST_TOKEN}")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("Homelab Control", body)
        self.assertIn("Restart", body)
        self.assertIn("Shutdown", body)
        self.assertIn("Update", body)
        self.assertIn("Clean", body)

    def test_panel_token_not_leaked_in_html(self):
        """The bearer token should not appear in the HTML source."""
        status, _, body = _raw_request("GET", f"/?token={TEST_TOKEN}")
        self.assertEqual(status, 200)
        # The token used in JS comes from the URL query param, not hardcoded
        # Ensure the server-side TOKEN value is not embedded in the HTML
        self.assertNotIn(f"'{TEST_TOKEN}'", body)
        self.assertNotIn(f'"{TEST_TOKEN}"', body)


if __name__ == "__main__":
    unittest.main()
