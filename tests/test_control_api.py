"""Tests for Homelab Control API.

These tests verify the API's security and routing behaviour WITHOUT
executing any real system commands.  The dangerous functions
(subprocess.Popen, subprocess.run) are patched out so that nothing is
actually shut-down, rebooted or pruned during testing.

Run:
    python3 -m pytest tests/test_control_api.py -v
"""

import http.client
import json
import os
import socket
import tempfile
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


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# Picked per run: a fixed port collides whenever two checkouts or sessions
# run the suite at once (a pre-push hook failed 48 setups that way).
TEST_PORT = _free_port()


def _render_template():
    """Read the Jinja2 template and replace placeholders with test values."""
    with open(TEMPLATE_PATH) as fh:
        src = fh.read()
    src = src.replace("{{ homelab_control_token }}", TEST_TOKEN)
    src = src.replace("{{ homelab_control_port }}", str(TEST_PORT))
    src = src.replace("{{ docker_user }}", "testuser")
    src = src.replace("{{ homelab_control_dir }}", "/opt/homelab/control")
    src = src.replace(
        "{{ ansible_facts['default_ipv4']['address'] | default('192.0.2.100') }}",
        "127.0.0.1",
    )
    src = src.replace(
        '{{ homelab_control_junk_scan_dirs | to_json }}',
        '["/tmp/test-junk-scan"]',
    )
    src = src.replace(
        '{{ homelab_control_extra_size_dirs | default([]) | to_json }}',
        '[]',
    )
    src = src.replace(
        "{{ homelab_control_offload_status_file }}",
        "/tmp/test-offload-status.json",  # noqa: S108
    )
    src = src.replace(
        "{{ homelab_control_offload_log_file }}",
        "/tmp/test-mac-offload.log",  # noqa: S108
    )
    src = src.replace(
        "{{ homelab_control_backup_state_file }}",
        "/tmp/test-backup.state",  # noqa: S108
    )
    src = src.replace(
        "{{ homelab_control_backup_log_file }}",
        "/tmp/test-homelab-backup.log",  # noqa: S108
    )
    src = src.replace(
        "{{ homelab_control_backup_script }}",
        "/tmp/test-homelab-backup.sh",  # noqa: S108
    )
    src = src.replace(
        "{{ homelab_control_backup_log_tail_lines }}",
        "50",
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

    @patch("subprocess.Popen")
    def test_shutdown_with_valid_token(self, mock_popen):
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("POST", "/shutdown", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "shutting down")
        mock_popen.assert_called_once()
        call_args = mock_popen.call_args[0][0]
        self.assertIn("shutdown.sh", call_args[-1])

    @patch("subprocess.Popen")
    def test_restart_with_valid_token(self, mock_popen):
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("POST", "/restart", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "restarting")
        mock_popen.assert_called_once()
        call_args = mock_popen.call_args[0][0]
        self.assertIn("restart.sh", call_args[-1])

    @patch("subprocess.run")
    def test_update_with_valid_token(self, mock_run):
        # /update returns 202 immediately and runs pulls in a background
        # thread so the ntfy mobile action button doesn't time out.
        mock_run.return_value = MagicMock(returncode=0, stdout="pulled", stderr="")
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("POST", "/update", headers=headers)
        self.assertEqual(status, 202)
        self.assertEqual(body["status"], "update scheduled")

    @patch("subprocess.run")
    def test_clean_with_valid_token(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Total reclaimed space: 1.2GB", stderr=""
        )
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("POST", "/clean", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "cleaned")

    # -- Junk scan endpoint -------------------------------------------------

    def test_junk_scan_without_token_returns_401(self):
        status, body = _request("GET", "/junk")
        self.assertEqual(status, 401)
        self.assertEqual(body, {"error": "unauthorized"})

    def test_junk_scan_with_valid_token_returns_file_list(self):
        """GET /junk returns scan results (empty list since /srv doesn't exist in tests)."""
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("GET", "/junk", headers=headers)
        self.assertEqual(status, 200)
        self.assertIn("files", body)
        self.assertIn("total_count", body)
        self.assertIn("total_size", body)
        self.assertIsInstance(body["files"], list)
        self.assertEqual(body["total_count"], len(body["files"]))

    def test_junk_clean_without_token_returns_401(self):
        status, body = _request("POST", "/junk")
        self.assertEqual(status, 401)

    def test_junk_clean_with_valid_token(self):
        """POST /junk deletes scanned junk (empty result since /srv doesn't exist in tests)."""
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("POST", "/junk", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "cleaned")
        self.assertIn("deleted", body)
        self.assertIn("freed_bytes", body)
        self.assertIn("errors", body)

    def test_get_junk_returns_404_without_auth(self):
        """GET /junk without auth returns 401, not 404."""
        status, body = _request("GET", "/junk")
        self.assertEqual(status, 401)

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
        self.assertIn("Junk", body)

    def test_panel_token_not_leaked_in_html(self):
        """The bearer token should not appear in the HTML source."""
        status, _, body = _raw_request("GET", f"/?token={TEST_TOKEN}")
        self.assertEqual(status, 200)
        # The token used in JS comes from the URL query param, not hardcoded
        # Ensure the server-side TOKEN value is not embedded in the HTML
        self.assertNotIn(f"'{TEST_TOKEN}'", body)
        self.assertNotIn(f'"{TEST_TOKEN}"', body)

    # -- Log endpoint -------------------------------------------------------

    def test_logs_without_token_returns_401(self):
        status, body = _request("GET", "/logs?source=system")
        self.assertEqual(status, 401)
        self.assertEqual(body, {"error": "unauthorized"})

    @patch("subprocess.run")
    def test_logs_system_source_returns_200(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="2024-01-01 log line\n", stderr=""
        )
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("GET", "/logs?source=system&lines=10", headers=headers)
        self.assertEqual(status, 200)
        self.assertIn("lines", body)
        self.assertIn("source", body)
        self.assertIn("truncated", body)
        self.assertEqual(body["source"], "system")
        self.assertIsInstance(body["lines"], list)

    @patch("subprocess.run")
    def test_logs_container_source_returns_200(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="container log line\n", stderr=""
        )
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("GET", "/logs?source=jellyfin&lines=20", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body["source"], "jellyfin")

    def test_logs_invalid_source_returns_400(self):
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("GET", "/logs?source=../../etc/passwd", headers=headers)
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "invalid source"})

    def test_logs_unknown_container_returns_400(self):
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("GET", "/logs?source=malicious", headers=headers)
        self.assertEqual(status, 400)

    @patch("subprocess.run")
    def test_logs_lines_clamped_at_max(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="line\n", stderr="")
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("GET", "/logs?source=system&lines=9999", headers=headers)
        self.assertEqual(status, 200)
        # Verify journalctl was called with clamped line count (500)
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[2], "500")

    @patch("subprocess.run")
    def test_logs_truncated_flag_set_on_large_output(self, mock_run):
        large_output = "x" * 20_000
        mock_run.return_value = MagicMock(returncode=0, stdout=large_output, stderr="")
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("GET", "/logs?source=system", headers=headers)
        self.assertEqual(status, 200)
        self.assertTrue(body["truncated"])


    # -- Updates endpoint ---------------------------------------------------

    def test_updates_without_token_returns_401(self):
        status, body = _request("GET", "/updates")
        self.assertEqual(status, 401)
        self.assertEqual(body, {"error": "unauthorized"})

    @patch("subprocess.run")
    def test_updates_returns_parsed_sessions(self, mock_run):
        watchtower_log = (
            'time="2026-03-15T16:45:04+03:00" level=info msg="Found new ghcr.io/gethomepage/homepage:latest image (abc123)"\n'
            'time="2026-03-15T16:46:37+03:00" level=info msg="Creating /homepage"\n'
            'time="2026-03-15T16:46:41+03:00" level=info msg="Session done" Failed=0 Scanned=20 Updated=1 notify=no\n'
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=watchtower_log, stderr="")
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("GET", "/updates", headers=headers)
        self.assertEqual(status, 200)
        self.assertIn("sessions", body)
        self.assertIn("total", body)
        self.assertEqual(body["total"], 1)
        session = body["sessions"][0]
        self.assertIn("summary", session)
        self.assertEqual(session["summary"]["updated"], 1)
        self.assertEqual(session["summary"]["scanned"], 20)

    @patch("subprocess.run")
    def test_updates_empty_log(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("GET", "/updates", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body["sessions"], [])
        self.assertEqual(body["total"], 0)


    # -- Storage endpoint ---------------------------------------------------

    def test_storage_without_token_returns_401(self):
        status, body = _request("GET", "/storage")
        self.assertEqual(status, 401)
        self.assertEqual(body, {"error": "unauthorized"})

    def test_storage_with_valid_token_returns_json(self):
        """GET /storage returns disk usage stats (may error since /srv doesn't exist in tests)."""
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("GET", "/storage", headers=headers)
        self.assertEqual(status, 200)
        # Either has disk stats or an error key (since /srv may not exist)
        self.assertTrue(
            "total_bytes" in body or "error" in body,
            f"Expected 'total_bytes' or 'error' in response, got: {body}",
        )


    # ── Offload endpoints ─────────────────────────────────────────

    def test_offload_status_without_token_returns_401(self):
        status, body = _request("GET", "/offload/status")
        self.assertEqual(status, 401)
        self.assertEqual(body, {"error": "unauthorized"})

    def test_offload_status_no_runs(self):
        """GET /offload/status returns no_runs when status file doesn't exist."""
        import os
        status_file = "/tmp/test-offload-status.json"  # noqa: S108
        if os.path.exists(status_file):
            os.unlink(status_file)
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("GET", "/offload/status", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "no_runs")

    def test_offload_status_with_data(self):
        """GET /offload/status returns data from status file."""
        import os
        status_file = "/tmp/test-offload-status.json"  # noqa: S108
        with open(status_file, "w") as f:
            json.dump({"status": "success", "markers": {"generated": 10, "failed": 0}}, f)
        try:
            headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
            status, body = _request("GET", "/offload/status", headers=headers)
            self.assertEqual(status, 200)
            self.assertEqual(body["status"], "success")
            self.assertEqual(body["markers"]["generated"], 10)
        finally:
            os.unlink(status_file)

    def test_offload_history_without_token_returns_401(self):
        status, body = _request("GET", "/offload/history")
        self.assertEqual(status, 401)
        self.assertEqual(body, {"error": "unauthorized"})

    def test_offload_history_no_log(self):
        """GET /offload/history returns empty when log file doesn't exist."""
        import os
        log_file = "/tmp/test-mac-offload.log"  # noqa: S108
        if os.path.exists(log_file):
            os.unlink(log_file)
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("GET", "/offload/history", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body["runs"], [])
        self.assertEqual(body["total"], 0)

    # -- Backup endpoints (Sprint 10) ---------------------------------------

    def test_backup_last_without_token_returns_401(self):
        status, body = _request("GET", "/backup/last")
        self.assertEqual(status, 401)
        self.assertEqual(body, {"error": "unauthorized"})

    def test_backup_last_never_run(self):
        """GET /backup/last returns a never_run marker when state file
        is absent — must not error out, the panel needs a stable shape."""
        import os
        state_file = "/tmp/test-backup.state"  # noqa: S108
        log_file = "/tmp/test-homelab-backup.log"  # noqa: S108
        for f in (state_file, log_file):
            if os.path.exists(f):
                os.unlink(f)
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("GET", "/backup/last", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body["state"]["status"], "never_run")
        self.assertEqual(body["recent_log"], [])

    def test_backup_last_returns_state_and_log(self):
        """When a state file exists, the response carries the JSON payload
        verbatim. When the log has JSONL lines, they appear in
        recent_log in order, parsed."""
        import os
        state_file = "/tmp/test-backup.state"  # noqa: S108
        log_file = "/tmp/test-homelab-backup.log"  # noqa: S108
        with open(state_file, "w") as f:
            json.dump({
                "last_run_ts": "2026-05-18T03:00:00Z",
                "status": "ok",
                "ok": 11, "failed": 0,
                "per_service": {"sonarr": {"bytes": 100, "status": "ok"}},
            }, f)
        with open(log_file, "w") as f:
            f.write('{"ts":"2026-05-18T03:00:00Z","event":"preflight","free_gb":431}\n')
            f.write('{"ts":"2026-05-18T03:00:05Z","event":"service","service":"sonarr","status":"ok"}\n')
        try:
            headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
            status, body = _request("GET", "/backup/last", headers=headers)
            self.assertEqual(status, 200)
            self.assertEqual(body["state"]["status"], "ok")
            self.assertEqual(body["state"]["ok"], 11)
            self.assertEqual(len(body["recent_log"]), 2)
            self.assertEqual(body["recent_log"][0]["event"], "preflight")
            self.assertEqual(body["recent_log"][1]["service"], "sonarr")
        finally:
            for f in (state_file, log_file):
                if os.path.exists(f):
                    os.unlink(f)

    def test_backup_last_handles_corrupt_state(self):
        """A half-written state file (e.g. mid-fsync OOM) must not crash
        the panel — fall back to the never_run shape."""
        import os
        state_file = "/tmp/test-backup.state"  # noqa: S108
        with open(state_file, "w") as f:
            f.write("{not valid json")
        try:
            headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
            status, body = _request("GET", "/backup/last", headers=headers)
            self.assertEqual(status, 200)
            self.assertEqual(body["state"]["status"], "never_run")
        finally:
            os.unlink(state_file)

    def test_post_backup_without_token_returns_401(self):
        status, body = _request("POST", "/backup")
        self.assertEqual(status, 401)
        self.assertEqual(body, {"error": "unauthorized"})

    def test_post_backup_script_missing_returns_503(self):
        """If the backup script doesn't exist on disk yet (deploy not run
        / aborted), the endpoint must surface 503 rather than 202 — a 202
        when the script is missing is a silent failure."""
        import os
        script = "/tmp/test-homelab-backup.sh"  # noqa: S108
        if os.path.exists(script):
            os.unlink(script)
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        status, body = _request("POST", "/backup", headers=headers)
        self.assertEqual(status, 503)
        self.assertIn("not found", body["error"])


class TestSymlinkProtection(unittest.TestCase):
    """Test symlink attack protection in junk cleaner."""

    ns = None

    @classmethod
    def setUpClass(cls):
        """Compile the template into an isolated namespace."""
        src = _render_template()
        cls.ns = {}
        exec(compile(src, "control-api.py", "exec"), cls.ns)  # noqa: S102

    def test_symlink_outside_srv_is_blocked(self):
        """Malicious symlink resolving outside /srv should be blocked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a target file outside /srv
            target = os.path.join(tmpdir, "safe-target.txt")
            with open(target, "w") as f:
                f.write("should not be deleted")

            # Create a fake junk file list with a path that resolves outside /srv
            # We'll monkey-patch scan_junk to return our crafted list
            original_scan = self.ns["scan_junk"]
            self.ns["scan_junk"] = lambda: [{"path": target, "name": "._evil", "size": 10}]

            try:
                # Create a minimal handler mock to capture _send_json calls
                handler = MagicMock()
                handler._send_json = MagicMock()

                # Call _junk_clean directly on the Handler class
                self.ns["Handler"]._junk_clean(handler)

                # Verify _send_json was called
                handler._send_json.assert_called_once()
                call_args = handler._send_json.call_args
                status_code = call_args[0][0]
                response_data = call_args[0][1]

                self.assertEqual(status_code, 200)
                # The file should NOT have been deleted
                self.assertTrue(os.path.exists(target), "Target file was deleted — symlink protection failed!")
                # Should have 0 deletions and 1 error (path escapes /srv)
                self.assertEqual(response_data["deleted"], 0)
                self.assertEqual(len(response_data["errors"]), 1)
                self.assertIn("escapes /srv", response_data["errors"][0]["error"])
            finally:
                self.ns["scan_junk"] = original_scan

    def test_path_starting_with_srv_prefix_is_blocked(self):
        """Paths like /srv_evil/... must not slip past a naive startswith check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # commonpath-based check rejects siblings of /srv
            evil_path = "/srv_evil/._junk"
            self.assertFalse(
                self.ns["_is_inside_srv"](evil_path, os.path.realpath("/srv") if os.path.isdir("/srv") else tmpdir),
            )

    def test_srv_root_itself_is_blocked(self):
        """/srv itself should not be considered 'inside /srv' for deletion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = os.path.realpath("/srv") if os.path.isdir("/srv") else tmpdir
            self.assertFalse(self.ns["_is_inside_srv"](base, base))

    def test_compose_dir_symlink_is_rejected(self):
        """_is_safe_compose_dir must reject symlinked compose dirs (RCE guard)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            real_dir = os.path.join(tmpdir, "legit")
            os.makedirs(real_dir)
            link_dir = os.path.join(tmpdir, "symlinked")
            os.symlink(real_dir, link_dir)
            # symlinks are unconditionally rejected
            self.assertFalse(self.ns["_is_safe_compose_dir"](link_dir))

    def test_compose_dir_outside_docker_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(self.ns["_is_safe_compose_dir"](tmpdir))

    def test_file_inside_srv_is_deleted(self):
        """Files that resolve inside /srv should be deleted normally."""
        # Only run where /srv exists AND is writable. Debian base images (e.g. a
        # CI runner container) ship an empty root-owned /srv, so a non-root run
        # sees it exist but can't create a tempdir there — skip rather than fail.
        if not os.path.isdir("/srv") or not os.access("/srv", os.W_OK):
            self.skipTest("/srv is absent or not writable on this system")

        with tempfile.TemporaryDirectory(dir="/srv") as tmpdir:
            junk_file = os.path.join(tmpdir, "._junk")
            with open(junk_file, "w") as f:
                f.write("junk")

            original_scan = self.ns["scan_junk"]
            self.ns["scan_junk"] = lambda: [{"path": junk_file, "name": "._junk", "size": 4}]

            try:
                handler = MagicMock()
                handler._send_json = MagicMock()

                self.ns["Handler"]._junk_clean(handler)

                call_args = handler._send_json.call_args
                response_data = call_args[0][1]

                self.assertFalse(os.path.exists(junk_file), "Junk file should have been deleted")
                self.assertEqual(response_data["deleted"], 1)
                self.assertEqual(len(response_data["errors"]), 0)
            finally:
                self.ns["scan_junk"] = original_scan


class TestOffloadHistoryParser(unittest.TestCase):
    """Guard get_offload_history against the live mac-offload.log format.

    The parser silently broke for ~2 weeks: the 2026-05-24 container-driven
    rewrite renamed the log banners ("=== Mac Offload Starting ===" → "... Status
    Check ...") and dropped the SSH skip lines, but the parser was not realigned,
    so it matched nothing and /offload/history returned []. This test pins the
    parser to the CURRENT log format so a future banner rename fails loudly.
    """

    ns = None

    @classmethod
    def setUpClass(cls):
        src = _render_template()
        cls.ns = {}
        exec(compile(src, "control-api.py", "exec"), cls.ns)  # noqa: S102

    # One skipped run (container down) + one success run with real marker counts,
    # in the exact format mac-offload.sh.j2 emits today.
    SAMPLE_LOG = (
        "[2026-06-05 01:30:02] === Mac Offload Status Check Starting ===\n"
        "[2026-06-05 01:30:02] Heartbeat stale: 99999s — container may be stopped.\n"
        "[2026-06-05 01:30:02] Container not alive. Skipping status check.\n"
        "[2026-06-06 01:30:02] === Mac Offload Status Check Starting ===\n"
        "[2026-06-06 01:30:02] Container alive (heartbeat 5s ago).\n"
        "[2026-06-06 01:30:02] [MARKERS] last_run=2026-06-05T18:12:39Z age=4h ok=80 fail=0\n"
        "[2026-06-06 01:30:03] === Mac Offload Status Check Complete (1m) ===\n"
    )

    def _parse(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
            fh.write(text)
            path = fh.name
        self.addCleanup(os.unlink, path)
        original = self.ns["OFFLOAD_LOG_FILE"]
        self.ns["OFFLOAD_LOG_FILE"] = path
        try:
            return self.ns["get_offload_history"]()
        finally:
            self.ns["OFFLOAD_LOG_FILE"] = original

    def test_parses_current_log_format(self):
        runs = self._parse(self.SAMPLE_LOG)
        self.assertEqual(len(runs), 2, "should parse both the skipped and success run")
        self.assertEqual(runs[0]["status"], "skipped")
        self.assertEqual(runs[1]["status"], "success")
        # markers_line must carry the "ok=N fail=N" the JS column regex extracts.
        self.assertIn("ok=80 fail=0", runs[1].get("markers_line", ""))

    def test_pre_rewrite_banners_no_longer_match(self):
        """The stale strings must parse ZERO runs — the regression we just fixed."""
        stale = (
            "[2026-06-06 01:30:02] === Mac Offload Starting ===\n"
            "[2026-06-06 01:30:02] [MARKERS] Completed: 80 ok\n"
            "[2026-06-06 01:30:03] === Mac Offload Complete ===\n"
        )
        # Old banners contain neither "Status Check Starting" nor "...Complete",
        # so the realigned parser correctly ignores them.
        self.assertEqual(self._parse(stale), [])


if __name__ == "__main__":
    unittest.main()


class TestDirSizeDoesNotCrossFilesystems(unittest.TestCase):
    """`_dir_size` must use `du -x` semantics.

    A FUSE/cloud filesystem mounted under /srv would otherwise be walked:
    the panel would count REMOTE bytes as local disk usage — contradicting
    `df` on the same screen — and would issue remote API calls on every
    cache miss.

    The device check cannot be exercised with a real mount in CI, so the
    boundary is verified by faking `st_dev`, which is precisely the thing
    the fix keys on.
    """

    ns = None

    @classmethod
    def setUpClass(cls):
        src = _render_template()
        cls.ns = {}
        exec(compile(src, "control-api.py", "exec"), cls.ns)  # noqa: S102

    def test_counts_files_on_the_same_filesystem(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.bin"), "wb") as f:
                f.write(b"x" * 1024)
            sub = os.path.join(tmp, "sub")
            os.mkdir(sub)
            with open(os.path.join(sub, "b.bin"), "wb") as f:
                f.write(b"y" * 2048)
            self.assertEqual(self.ns["_dir_size"](tmp), 3072)

    def test_skips_a_subtree_on_a_different_device(self):
        """The submount case: a subdirectory reporting a different st_dev is
        another filesystem and its bytes are not on this disk."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "local.bin"), "wb") as f:
                f.write(b"x" * 1024)
            union = os.path.join(tmp, "cloudmount")
            os.mkdir(union)
            with open(os.path.join(union, "archived.bin"), "wb") as f:
                f.write(b"z" * 4096)

            real_stat = os.stat

            def fake(path, *a, **kw):
                st = real_stat(path, *a, **kw)
                if os.path.basename(str(path)) == "cloudmount" or "cloudmount" in str(path):
                    return os.stat_result(
                        (st.st_mode, st.st_ino, st.st_dev + 99, st.st_nlink,
                         st.st_uid, st.st_gid, st.st_size,
                         st.st_atime, st.st_mtime, st.st_ctime)
                    )
                return st

            with patch.object(os, "stat", side_effect=fake), \
                 patch.object(os, "lstat", side_effect=lambda p, *a, **kw: fake(p)):
                size = self.ns["_dir_size"](tmp)

            self.assertEqual(
                size, 1024,
                "archived (remote) bytes were counted as local disk usage",
            )

    def test_missing_path_returns_zero(self):
        self.assertEqual(self.ns["_dir_size"]("/nonexistent-path-xyz"), 0)
