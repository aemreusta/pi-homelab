"""Tests for ansible/roles/alertmanager/templates/ntfy-bridge.py.j2.

Sprint 18 fix-up #7. The bridge is the highest-risk new code in Sprint 18:
JSON parse + ntfy POST + audit-log append, all in stdlib. shellcheck doesn't
cover Python; ansible-lint doesn't render Jinja2-Python templates.

Strategy:
  1. Render the Jinja2 template with stub vars → tempfile.
  2. import the rendered module via importlib.util.
  3. Monkey-patch urllib.request.urlopen + redirect AUDIT_LOG to a tempfile.
  4. Drive handle_alert() with synthetic AM payloads; assert side effects.

We do NOT use the HTTP server directly — handle_alert() is the unit; the
ThreadingHTTPServer wrapper is glue.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    import jinja2
except ImportError as exc:  # pragma: no cover
    raise SystemExit("jinja2 required for test_ntfy_bridge.py") from exc

REPO = Path(__file__).resolve().parents[1]
TPL = REPO / "ansible" / "roles" / "alertmanager" / "templates" / "ntfy-bridge.py.j2"

STUB_CTX = {
    "ntfy_external_topic_url": "http://ntfy:80/homelab",
    "alertmanager_bridge_port": 9097,
}


def render_bridge_module(tmpdir: Path) -> object:
    """Render the Jinja2 template into a .py file and import it as a module."""
    # We're rendering a Python script template (not HTML), so autoescape
    # would corrupt quotes and ampersands. The Jinja2 input is trusted
    # (vendored Ansible template), not user input — no XSS surface.
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TPL.parent)),
        undefined=jinja2.StrictUndefined,
        autoescape=False,  # noqa: S701 — Python source template, not HTML
    )
    rendered = env.get_template(TPL.name).render(**STUB_CTX)
    out = tmpdir / "ntfy_bridge_rendered.py"
    out.write_text(rendered)
    spec = importlib.util.spec_from_file_location("ntfy_bridge_rendered", out)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class NtfyBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.audit = self.tmp / "audit.log"
        self.audit.touch()
        self.mod = render_bridge_module(self.tmp)
        # Redirect AUDIT_LOG used by audit().
        self.mod.AUDIT_LOG = str(self.audit)

    def _stub_response(self, status: int):
        """Return a MagicMock that .__enter__()s to a response with .status=status."""
        ctx = MagicMock()
        resp = MagicMock()
        resp.status = status
        ctx.__enter__.return_value = resp
        ctx.__exit__.return_value = False
        return ctx

    # ── send_ntfy ─────────────────────────────────────────────────────

    def test_send_ntfy_returns_http_code_on_success(self):
        with patch.object(self.mod.urllib.request, "urlopen",
                          return_value=self._stub_response(200)):
            code = self.mod.send_ntfy("title", "body", "high", "warning")
        self.assertEqual(code, 200)

    def test_send_ntfy_returns_http_code_on_4xx(self):
        # HTTPError surfaces as e.code — the bridge must return it verbatim
        # so the audit log records the real failure.
        import urllib.error
        err = urllib.error.HTTPError(
            url="http://ntfy/", code=404, msg="Not Found", hdrs=None, fp=None,
        )
        with patch.object(self.mod.urllib.request, "urlopen", side_effect=err):
            code = self.mod.send_ntfy("title", "body", "default", "tag")
        self.assertEqual(code, 404)

    def test_send_ntfy_returns_0_on_transport_error(self):
        # URLError (DNS, refused) → bridge returns 0 (audit status=fail).
        # time.sleep is patched because the retry schedule spans ~62s of real
        # backoff (widened 2026-08-02 to cover the AdGuard startup window);
        # without this the unit test would actually sleep for a minute.
        import urllib.error
        with patch.object(self.mod.urllib.request, "urlopen",
                          side_effect=urllib.error.URLError("refused")), \
             patch.object(self.mod.time, "sleep"):
            code = self.mod.send_ntfy("t", "b", "default", "tag")
        self.assertEqual(code, 0)

    def test_send_ntfy_passes_priority_and_tags_in_headers(self):
        captured = {}

        def fake_urlopen(req, timeout=10):
            captured["headers"] = dict(req.headers)
            captured["url"] = req.full_url
            captured["data"] = req.data
            return self._stub_response(200)

        with patch.object(self.mod.urllib.request, "urlopen", side_effect=fake_urlopen):
            self.mod.send_ntfy("My title", "My body", "urgent", "rotating_light,fire")

        # urllib.request.Request title-cases header keys.
        self.assertEqual(captured["headers"]["Title"], "My title")
        self.assertEqual(captured["headers"]["Priority"], "urgent")
        self.assertEqual(captured["headers"]["Tags"], "rotating_light,fire")
        self.assertEqual(captured["headers"]["Markdown"], "yes")
        self.assertEqual(captured["data"], b"My body")
        self.assertEqual(captured["url"], "http://ntfy:80/homelab")

    def test_send_ntfy_encodes_non_latin1_title_rfc2047(self):
        # Regression 2026-07-16: an em-dash (U+2014) in the alert summary
        # crashed http.client's latin-1 header encode → notification never
        # sent, no audit line. The Title header must always be
        # latin-1-safe; non-latin-1 titles go out RFC 2047 (ntfy decodes).
        captured = {}

        def fake_urlopen(req, timeout=10):
            captured.update(req.headers)
            return self._stub_response(200)

        title = "VictoriaLogs is down — log-based alerting is blind"
        with patch.object(self.mod.urllib.request, "urlopen", side_effect=fake_urlopen):
            code = self.mod.send_ntfy(title, "body", "urgent", "rotating_light")

        self.assertEqual(code, 200)
        sent = captured["Title"]
        sent.encode("latin-1")  # must never raise
        self.assertTrue(sent.startswith("=?UTF-8?B?") and sent.endswith("?="))
        import base64
        self.assertEqual(
            base64.b64decode(sent[len("=?UTF-8?B?"):-len("?=")]).decode("utf-8"),
            title,
        )

    def test_send_ntfy_ascii_title_left_untouched(self):
        captured = {}

        def fake_urlopen(req, timeout=10):
            captured.update(req.headers)
            return self._stub_response(200)

        with patch.object(self.mod.urllib.request, "urlopen", side_effect=fake_urlopen):
            self.mod.send_ntfy("Plain title", "b", "high", "warning")
        self.assertEqual(captured["Title"], "Plain title")

    def test_send_ntfy_retries_once_on_transient_transport_error(self):
        # Docker embedded-DNS blips ("Name does not resolve") are transient;
        # first attempt URLError → one retry → success must return 200.
        import urllib.error
        calls = {"n": 0}

        def flaky_urlopen(req, timeout=10):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.URLError("Name does not resolve")
            return self._stub_response(200)

        with patch.object(self.mod.urllib.request, "urlopen", side_effect=flaky_urlopen), \
             patch.object(self.mod.time, "sleep"):
            code = self.mod.send_ntfy("t", "b", "default", "tag")
        self.assertEqual(code, 200)
        self.assertEqual(calls["n"], 2)

    def test_handle_alert_emdash_summary_still_audits(self):
        # End-to-end: the exact payload shape that crashed the handler
        # thread on 2026-07-16 must now deliver and write an audit line.
        alert = {
            "status": "firing",
            "labels": {"alertname": "VictoriaLogsDown", "severity": "crit"},
            "annotations": {
                "summary": "VictoriaLogs is down — log-based alerting is blind",
                "description": "details — with dashes",
            },
        }
        with patch.object(self.mod.urllib.request, "urlopen",
                          return_value=self._stub_response(200)):
            self.mod.handle_alert(alert, "firing")
        line = self.audit.read_text().strip().splitlines()[-1]
        self.assertIn("status=ok", line)
        self.assertIn("alertname=VictoriaLogsDown", line)

    # ── audit ─────────────────────────────────────────────────────────

    def test_audit_writes_logfmt_line(self):
        self.mod.audit("ok", alertname="CPULoadHigh", severity="warn", http="200")
        line = self.audit.read_text().strip().splitlines()[-1]
        self.assertIn("script=vmalert", line)
        self.assertIn("step=alert", line)
        self.assertIn("status=ok", line)
        self.assertIn("alertname=CPULoadHigh", line)
        self.assertIn("severity=warn", line)
        self.assertIn("http=200", line)
        # ISO8601 UTC timestamp prefix.
        self.assertRegex(line, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z ")

    def test_audit_skips_none_fields(self):
        self.mod.audit("fail", alertname="X", severity=None, http="500")
        line = self.audit.read_text().strip().splitlines()[-1]
        self.assertIn("alertname=X", line)
        self.assertIn("http=500", line)
        self.assertNotIn("severity=None", line)
        self.assertNotIn("severity=", line)

    # ── handle_alert end-to-end (the unit AM webhook calls) ───────────

    def test_chronic_alert_shows_age_without_changing_priority(self):
        alert = {"status": "firing", "startsAt": "2026-09-21T00:00:00Z",
                 "labels": {"alertname": "PendingWork", "severity": "warn"},
                 "annotations": {"summary": "Pending work", "description": "Review the held items."}}
        with patch.object(self.mod.time, "time", return_value=1790280000), \
                patch.object(self.mod, "send_ntfy", return_value=200) as send:
            self.assertTrue(self.mod.handle_alert(alert, "firing"))
        title, body, priority, _ = send.call_args.args
        self.assertEqual(title, "[ONGOING] Pending work")
        self.assertIn("Active for 3d 20h", body)
        self.assertIn("Review the held items.", body)
        self.assertEqual(priority, "high")

    def test_resolved_duration_uses_end_time_and_invalid_dates_keep_delivery(self):
        alert = {"status": "resolved", "startsAt": "2026-09-21T00:00:00Z",
                 "endsAt": "2026-09-21T02:00:00Z", "labels": {"severity": "crit"},
                 "annotations": {"summary": "Recovered", "description": "Restored."}}
        with patch.object(self.mod, "send_ntfy", return_value=200) as send:
            self.assertTrue(self.mod.handle_alert(alert, "resolved"))
            self.assertIn("Duration: 2h 0m", send.call_args.args[1])
            self.assertEqual(send.call_args.args[2], "low")
            alert["startsAt"] = "invalid"
            self.assertTrue(self.mod.handle_alert(alert, "resolved"))
            self.assertEqual(send.call_args.args[1], "Restored.")

    def test_handle_alert_firing_writes_audit_and_calls_ntfy(self):
        alert = {
            "status": "firing",
            "labels": {
                "alertname": "CPULoadCritical",
                "severity": "crit",
                "service": "host",
            },
            "annotations": {
                "summary": "CPU load CRIT",
                "description": "load5=14.30",
            },
        }
        captured_headers = {}

        def fake_urlopen(req, timeout=10):
            captured_headers.update(req.headers)
            return self._stub_response(200)

        with patch.object(self.mod.urllib.request, "urlopen", side_effect=fake_urlopen):
            self.mod.handle_alert(alert, "firing")

        # ntfy was called with urgent priority for crit severity.
        self.assertEqual(captured_headers["Priority"], "urgent")
        self.assertEqual(captured_headers["Tags"], "rotating_light")
        self.assertEqual(captured_headers["Title"], "CPU load CRIT")

        line = self.audit.read_text().strip().splitlines()[-1]
        self.assertIn("status=ok", line)
        self.assertIn("alertname=CPULoadCritical", line)
        self.assertIn("severity=crit", line)
        self.assertIn("firing=firing", line)
        self.assertIn("http=200", line)

    def test_handle_alert_resolved_uses_low_priority(self):
        alert = {
            "status": "resolved",
            "labels": {"alertname": "CPULoadHigh", "severity": "warn"},
            "annotations": {"summary": "CPU recovered", "description": ""},
        }
        captured = {}

        def fake_urlopen(req, timeout=10):
            captured.update(req.headers)
            return self._stub_response(200)

        with patch.object(self.mod.urllib.request, "urlopen", side_effect=fake_urlopen):
            self.mod.handle_alert(alert, "resolved")

        self.assertEqual(captured["Priority"], "low")
        self.assertEqual(captured["Tags"], "white_check_mark")
        self.assertTrue(captured["Title"].startswith("[RESOLVED]"))

    def test_handle_alert_audit_records_fail_on_ntfy_4xx(self):
        import urllib.error
        alert = {
            "status": "firing",
            "labels": {"alertname": "SyntheticTest", "severity": "warn"},
            "annotations": {"summary": "x", "description": "y"},
        }
        err = urllib.error.HTTPError(
            url="http://ntfy/", code=503, msg="Down", hdrs=None, fp=None,
        )
        with patch.object(self.mod.urllib.request, "urlopen", side_effect=err):
            self.mod.handle_alert(alert, "firing")
        line = self.audit.read_text().strip().splitlines()[-1]
        self.assertIn("status=fail", line)
        self.assertIn("http=503", line)


class PostBootDnsLossTests(unittest.TestCase):
    """Silent-delivery class #3 (2026-08-02).

    24h of logs showed 10× `ntfy POST failed: <urlopen error [Errno -2] Name
    does not resolve>`. It resolves fine in steady state — this is a startup
    race: the bridge comes up before AdGuard is serving DNS, and every alert
    fired in that ~60 s window is lost. The alerts most worth delivering are
    precisely the ones fired just after a restart.

    Two defects, and the second is the load-bearing one:

    1. The in-process retry was a single attempt after 2 s, scoped by its own
       comment to "Docker's embedded DNS transiently". The real outage is the
       ~60 s AdGuard takes to start. The mitigation was sized to an imagined
       failure, not a measured one.

    2. `do_POST` returned 204 UNCONDITIONALLY — including when the ntfy send
       had just failed. Alertmanager was told "delivered" and therefore never
       retried, converting a transient DNS blip into permanent alert loss.
       AM already has retry machinery with backoff far exceeding anything we
       would reimplement here; the bridge just had to stop lying to it.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.audit = self.tmp / "audit.log"
        self.audit.touch()
        self.mod = render_bridge_module(self.tmp)
        self.mod.AUDIT_LOG = str(self.audit)

    def _stub_response(self, status: int):
        ctx = MagicMock()
        resp = MagicMock()
        resp.status = status
        ctx.__enter__.return_value = resp
        ctx.__exit__.return_value = False
        return ctx

    FIRING = {
        "status": "firing",
        "labels": {"alertname": "HostUnexpectedReboot", "severity": "warn"},
        "annotations": {"summary": "homelab restarted", "description": "d"},
    }

    # ── defect 2: never tell Alertmanager a failed send succeeded ──────

    def test_process_payload_reports_failure(self):
        import urllib.error
        with patch.object(self.mod.urllib.request, "urlopen",
                          side_effect=urllib.error.URLError("Name does not resolve")), \
             patch.object(self.mod.time, "sleep"):
            ok = self.mod.process_payload({"status": "firing", "alerts": [self.FIRING]})
        self.assertFalse(
            ok,
            "a failed ntfy send was reported as success — Alertmanager will "
            "not retry and the alert is lost permanently",
        )

    def test_process_payload_reports_success(self):
        with patch.object(self.mod.urllib.request, "urlopen",
                          return_value=self._stub_response(200)):
            ok = self.mod.process_payload({"status": "firing", "alerts": [self.FIRING]})
        self.assertTrue(ok)

    def test_partial_failure_is_a_failure(self):
        """One bad send in a group must make the whole POST retryable.

        AM re-sends the whole group, so a duplicate for the alert that did
        succeed is the cost. A duplicate notification is recoverable; a
        dropped one is not.
        """
        import urllib.error
        state = {"first": True}

        def flaky(req, timeout=10):
            """First alert delivers; the second fails on every attempt."""
            if state["first"]:
                state["first"] = False
                return self._stub_response(200)
            raise urllib.error.URLError("Name does not resolve")

        with patch.object(self.mod.urllib.request, "urlopen", side_effect=flaky), \
             patch.object(self.mod.time, "sleep"):
            ok = self.mod.process_payload(
                {"status": "firing", "alerts": [self.FIRING, self.FIRING]}
            )
        self.assertFalse(ok)

    def test_empty_payload_is_success_not_failure(self):
        """An alert-less POST must not trigger an AM retry storm."""
        self.assertTrue(self.mod.process_payload({"status": "firing", "alerts": []}))

    # ── defect 1: retry must cover the real boot-DNS window ───────────

    def test_retry_schedule_covers_the_adguard_startup_window(self):
        import urllib.error
        slept: list[float] = []
        with patch.object(self.mod.urllib.request, "urlopen",
                          side_effect=urllib.error.URLError("Name does not resolve")), \
             patch.object(self.mod.time, "sleep", side_effect=slept.append):
            code = self.mod.send_ntfy("t", "b", "default", "tag")
        self.assertEqual(code, 0)
        self.assertGreaterEqual(
            sum(slept), 30,
            f"total retry window {sum(slept)}s is too short for the ~60s "
            f"AdGuard startup that caused the 2026-08-02 losses",
        )
        self.assertTrue(
            # strict=False: pairwise walk, so the shorter tail is intended.
            all(b >= a for a, b in zip(slept, slept[1:], strict=False)),
            f"retry delays must back off, got {slept}",
        )

    def test_retry_gives_up_rather_than_blocking_forever(self):
        """Bounded: AM's own retry is the outer loop, not this one.

        An unbounded in-process retry would pin the handler thread and stall
        every later alert behind a dead ntfy.
        """
        import urllib.error
        slept: list[float] = []
        with patch.object(self.mod.urllib.request, "urlopen",
                          side_effect=urllib.error.URLError("dns")), \
             patch.object(self.mod.time, "sleep", side_effect=slept.append):
            self.mod.send_ntfy("t", "b", "default", "tag")
        self.assertLessEqual(
            sum(slept), 180,
            f"retry window {sum(slept)}s would pin the handler thread",
        )

    def test_success_on_a_later_attempt_still_returns_200(self):
        import urllib.error
        calls = {"n": 0}

        def flaky(req, timeout=10):
            calls["n"] += 1
            if calls["n"] < 4:
                raise urllib.error.URLError("Name does not resolve")
            return self._stub_response(200)

        with patch.object(self.mod.urllib.request, "urlopen", side_effect=flaky), \
             patch.object(self.mod.time, "sleep"):
            self.assertEqual(self.mod.send_ntfy("t", "b", "default", "tag"), 200)

    def test_http_error_does_not_retry(self):
        """A 4xx is a real answer from a reachable ntfy — retrying is wrong.

        Only transport failures (DNS, refused, timeout) are worth retrying.
        """
        import urllib.error
        err = urllib.error.HTTPError(
            url="http://ntfy/", code=400, msg="Bad", hdrs=None, fp=None,
        )
        slept: list[float] = []
        with patch.object(self.mod.urllib.request, "urlopen", side_effect=err), \
             patch.object(self.mod.time, "sleep", side_effect=slept.append):
            self.assertEqual(self.mod.send_ntfy("t", "b", "default", "tag"), 400)
        self.assertEqual(slept, [], "retried a definitive HTTP error")

    # ── observability: the failure must not stay log-only ─────────────

    def test_send_failures_are_counted(self):
        import urllib.error
        before = self.mod.SEND_FAILURES
        with patch.object(self.mod.urllib.request, "urlopen",
                          side_effect=urllib.error.URLError("dns")), \
             patch.object(self.mod.time, "sleep"):
            self.mod.send_ntfy("t", "b", "default", "tag")
        self.assertEqual(
            self.mod.SEND_FAILURES, before + 1,
            "no counter moved — the 2026-08-02 losses were visible only in "
            "container stderr, which nothing scrapes",
        )

    def test_metrics_endpoint_exposes_counter(self):
        body = self.mod.render_metrics()
        self.assertIn("ntfy_bridge_send_failures_total", body)
        self.assertIn("# TYPE ntfy_bridge_send_failures_total counter", body)
        for line in body.splitlines():
            if line and not line.startswith("#"):
                float(line.rsplit(" ", 1)[1])

    def test_metrics_counter_is_scraped_by_vmagent(self):
        """A counter nothing scrapes is the silent-zero class again."""
        cfg = REPO / "ansible/roles/vmagent/defaults/main.yml"
        self.assertIn(
            "name: ntfy-bridge", cfg.read_text(),
            "ntfy-bridge is not a vmagent scrape job, so "
            "ntfy_bridge_send_failures_total is emitted but never collected",
        )

    def test_scrape_target_is_actually_reachable_from_vmagent(self):
        """A scrape job pointing at an unpublished port is worse than none.

        vmagent runs `network_mode: host`, so a 127.0.0.1 target only works
        if the container publishes that port on loopback. ntfy-bridge did
        not — it was reachable only over the compose network by container
        name. Adding the job without the port mapping would have pinned
        up{job="ntfy-bridge"}==0 forever, so NtfyBridgeDown would fire
        permanently and get muted, taking NtfyBridgeSendFailures' credibility
        with it. Caught in review 2026-08-02, before deploy.
        """
        port = "9097"
        defaults = (REPO / "ansible/roles/alertmanager/defaults/main.yml").read_text()
        for line in defaults.splitlines():
            if line.startswith("alertmanager_bridge_port:"):
                port = line.split(":", 1)[1].strip()
                break

        jobs = (REPO / "ansible/roles/vmagent/defaults/main.yml").read_text()
        self.assertIn(
            f"target: 127.0.0.1:{{{{ alertmanager_bridge_port | default({port}) }}}}",
            jobs,
            "the ntfy-bridge scrape target drifted from the bridge port",
        )

        compose = (
            REPO / "ansible/roles/alertmanager/templates/docker-compose.yml.j2"
        ).read_text()
        self.assertIn(
            '"127.0.0.1:{{ alertmanager_bridge_port }}:{{ alertmanager_bridge_port }}"',
            compose,
            "ntfy-bridge does not publish its port on loopback, so "
            "host-networked vmagent cannot scrape it",
        )
        self.assertNotIn(
            f'"0.0.0.0:{port}', compose,
            "the bridge accepts unauthenticated webhook POSTs — it must "
            "never be published beyond loopback",
        )

    def test_send_failures_are_covered_by_a_vmalert_rule(self):
        """Scraped but unwatched is the same silence, one layer along."""
        rules_dir = REPO / "ansible/roles/vmalert/templates/rules"
        self.assertTrue(
            any(
                "ntfy_bridge_send_failures_total" in p.read_text()
                for p in rules_dir.glob("*.yml.j2")
            ),
            "no vmalert rule reads ntfy_bridge_send_failures_total",
        )


if __name__ == "__main__":
    unittest.main()
