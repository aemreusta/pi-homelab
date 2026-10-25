"""End-to-end well-formedness of the split-DNS drift emitter's .prom output.

Complements ``tests/test_textfile_emitters.py``, which reads emitter templates
*statically*. That static extractor can only see literal ``echo "..."`` lines,
so it is structurally blind to a block assembled at runtime and written with
``printf '%s' "$var"`` — and that blind spot shipped a real bug on 2026-08-05:

    DOMAIN_LINES=$(... python3 ...)     # command substitution strips the
    printf '%s' "$DOMAIN_LINES"         # trailing newline -> next line collides

which produced

    tailscale_split_dns_domain_ok{domain="example.internal"} 0# HELP tailscale_...

node_exporter rejected the whole file (``node_textfile_scrape_error 1``) and the
metric never reached VictoriaMetrics — the drift watchdog was itself silently
dead, which is precisely the failure it exists to prevent.

So this test runs the **rendered script for real** against a stubbed
``tailscale`` binary and parses the file it writes. Anything that makes the
output unparseable — a missing newline, a bad label, an empty value — fails
here rather than in production as a silent scrape error.

Run:  python3 -m pytest tests/test_split_dns_emitter_output.py -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = (
    REPO / "ansible" / "roles" / "tailscale_dns" / "templates" / "tailscale-split-dns-check.sh.j2"
)
DECIDER = REPO / "ansible" / "roles" / "tailscale_dns" / "files" / "tailscale_split_dns.py"

NS = "100.64.0.10"

# Strict-ish Prometheus text grammar for a sample line.
SAMPLE_RE = re.compile(
    r'^[a-zA-Z_:][a-zA-Z0-9_:]*'
    r'(\{[a-zA-Z_][a-zA-Z0-9_]*="[^"]*"(,\s*[a-zA-Z_][a-zA-Z0-9_]*="[^"]*")*\})?'
    r"[ \t]+-?\d+(\.\d+)?(e[+-]?\d+)?$"
)

# The live drift state on 2026-08-05: route exists for the dead TLD only.
DRIFTED = json.dumps(
    {"SplitDNSRoutes": {"example": [{"Addr": NS}], "ts.net.": [{"Addr": "1.2.3.4"}]}}
)
HEALTHY = json.dumps({"SplitDNSRoutes": {"example.internal": [{"Addr": NS}]}})


def _render(textfile_dir: str, log_file: str, domains: str) -> str:
    src = TEMPLATE.read_text()
    subs = {
        "{{ tailscale_dns_split_check_decider }}": str(DECIDER),
        "{{ homelab_internal_domains | join(',') }}": domains,
        "{{ tailscale_dns_ip }}": NS,
        "{{ tailscale_dns_split_check_log }}": log_file,
        "{{ common_node_exporter_textfile_dir | default('/var/lib/node_exporter/textfile') }}": textfile_dir,
    }
    for k, v in subs.items():
        src = src.replace(k, v)
    assert "{{" not in src and "{%" not in src, "unsubstituted Jinja left in rendered script"
    return src


def _run(status_json: str, domains: str = "example.internal"):
    """Run the emitter with a stubbed `tailscale`; return (rc, prom_text)."""
    tmp = tempfile.mkdtemp()
    try:
        textfile_dir = os.path.join(tmp, "textfile")
        os.makedirs(textfile_dir)
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)

        stub = os.path.join(bindir, "tailscale")
        with open(stub, "w") as fh:
            fh.write(
                textwrap.dedent(
                    f"""\
                    #!/bin/sh
                    # stub: only answers `dns status --json`
                    cat <<'EOF'
                    {status_json}
                    EOF
                    """
                )
            )
        os.chmod(stub, 0o755)  # noqa: S103 — test stub must be executable

        script = os.path.join(tmp, "check.sh")
        Path(script).write_text(_render(textfile_dir, os.path.join(tmp, "check.log"), domains))
        os.chmod(script, 0o755)  # noqa: S103

        env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"])
        proc = subprocess.run(  # noqa: S603 — fixed argv, test harness
            ["/bin/bash", script], capture_output=True, text=True, env=env, timeout=60
        )
        prom_path = os.path.join(textfile_dir, "tailscale_split_dns.prom")
        prom = Path(prom_path).read_text() if os.path.exists(prom_path) else ""
        return proc.returncode, prom
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class EmittedFileIsWellFormed(unittest.TestCase):
    def _assert_parseable(self, prom: str):
        self.assertTrue(prom, "emitter wrote no .prom file at all")
        # The regression: a line that does not end in a newline lets the next
        # line concatenate onto it, and node_exporter rejects the whole file.
        self.assertTrue(prom.endswith("\n"), "file does not end with a newline")
        for ln in prom.splitlines():
            if not ln.strip() or ln.startswith("#"):
                continue
            self.assertRegex(ln, SAMPLE_RE, f"not valid Prometheus text: {ln!r}")

    def test_drifted_output_is_parseable(self):
        rc, prom = _run(DRIFTED)
        self.assertEqual(rc, 1, "drift must exit 1")
        self._assert_parseable(prom)

    def test_healthy_output_is_parseable(self):
        rc, prom = _run(HEALTHY)
        self.assertEqual(rc, 0, "no drift must exit 0")
        self._assert_parseable(prom)

    def test_multi_domain_output_is_parseable(self):
        rc, prom = _run(HEALTHY, domains="example.internal,lab.internal")
        self._assert_parseable(prom)
        self.assertEqual(prom.count("tailscale_split_dns_domain_ok{"), 2)

    def test_per_domain_line_is_not_glued_to_the_next_help(self):
        # Direct assertion on the exact 2026-08-05 corruption shape.
        _, prom = _run(DRIFTED)
        self.assertNotIn("0# HELP", prom)
        self.assertNotIn("1# HELP", prom)

    def test_drift_is_reported_in_the_values(self):
        _, prom = _run(DRIFTED)
        self.assertIn("tailscale_split_dns_ok 0", prom)
        self.assertIn("tailscale_split_dns_missing 1", prom)
        self.assertIn("tailscale_split_dns_stale 1", prom)
        self.assertIn('tailscale_split_dns_domain_ok{domain="example.internal"} 0', prom)

    def test_healthy_is_reported_in_the_values(self):
        _, prom = _run(HEALTHY)
        self.assertIn("tailscale_split_dns_ok 1", prom)
        self.assertIn("tailscale_split_dns_missing 0", prom)
        self.assertIn('tailscale_split_dns_domain_ok{domain="example.internal"} 1', prom)

    def test_every_declared_gauge_has_help_type_and_sample(self):
        _, prom = _run(DRIFTED)
        names = [
            "tailscale_split_dns_ok",
            "tailscale_split_dns_missing",
            "tailscale_split_dns_misrouted",
            "tailscale_split_dns_stale",
            "tailscale_split_dns_scrape_ok",
            "tailscale_split_dns_domain_ok",
            "tailscale_split_dns_last_run_unix_seconds",
        ]
        for n in names:
            self.assertIn(f"# HELP {n} ", prom, f"{n}: missing HELP")
            self.assertIn(f"# TYPE {n} gauge", prom, f"{n}: missing TYPE")
            self.assertTrue(
                re.search(rf"^{re.escape(n)}(\{{|[ \t])", prom, re.MULTILINE),
                f"{n}: no sample line",
            )


class ScrapeFailure(unittest.TestCase):
    def test_dead_tailscale_emits_scrape_ok_zero_not_a_false_drift(self):
        # A tailscaled that answers nothing must NOT read as "zero routes" —
        # that would fire the drift alert and send the operator to the console
        # to fix a route that is fine.
        rc, prom = _run("")
        self.assertEqual(rc, 2)
        self.assertIn("tailscale_split_dns_scrape_ok 0", prom)
        self.assertTrue(prom.endswith("\n"))

    def test_unparseable_status_still_writes_a_metric(self):
        # Silent-cron rule: always emit something, so "checker broken" is
        # distinguishable from "checker never deployed".
        rc, prom = _run("{not json")
        self.assertIn("tailscale_split_dns_scrape_ok 0", prom)
        self.assertIn("tailscale_split_dns_last_run_unix_seconds", prom)


if __name__ == "__main__":
    unittest.main()
