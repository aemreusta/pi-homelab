"""Well-formedness tests for node_exporter *textfile* gauges emitted by shell
templates.

A broken Prometheus line in a `.prom` emitter is a silent failure: it doesn't
crash the cron, it just sets `node_textfile_scrape_error{file="..."}=1` and the
metric never appears — exactly the silent-cron blind spot CLAUDE.md hard-rule #1
guards against. No existing test covered an emitter; this one does.

Rather than execute the scripts (which would need sqlite3 + a live qBittorrent
API), this statically renders each Jinja2 template and verifies, for every gauge
we care about, that it has a `# HELP` line, a `# TYPE ... gauge` line, and at
least one sample line — and that each sample line is valid Prometheus grammar
(`name{labels}? <number>`) once the shell variable is substituted with a value.
This catches the real failure modes: a metric-name typo between HELP/TYPE/sample,
a missing HELP/TYPE, or a label-quoting bug.

Run:
    python3 -m pytest tests/test_textfile_emitters.py -v
"""

import os
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

ROLES = os.path.join(os.path.dirname(__file__), "..", "ansible", "roles")

# Prometheus sample line: metric name, optional {labels}, whitespace, a number.
# We substitute every ${var}/$var with a stand-in number first, so a well-formed
# template line like  speedtest_last_result_timestamp ${ts}  becomes valid.
_SAMPLE_RE = re.compile(
    r'^[a-zA-Z_:][a-zA-Z0-9_:]*'        # metric name
    r'(\{[a-zA-Z_][a-zA-Z0-9_]*="[^"]*"(,[a-zA-Z_][a-zA-Z0-9_]*="[^"]*")*\})?'  # optional labels
    r'\s+-?\d+(\.\d+)?(e[+-]?\d+)?$'     # value
)

# template (relative to roles/) -> Jinja2 var substitutions -> metric names to check.
SPECS = [
    {
        "template": "speedtest_tracker/templates/speedtest-result-liveness.sh.j2",
        "subs": {"{{ speedtest_tracker_config_dir }}": "/home/testuser/docker/speedtest-tracker/config"},
        "metrics": [
            "speedtest_result_liveness_last_run_unix_seconds",
            "speedtest_last_result_timestamp",
            "speedtest_last_result_ok",
            "speedtest_failed_last_24h",
        ],
    },
    {
        "template": "vmalert/templates/vmalert-liveness.sh.j2",
        "subs": {"{{ vmalert_metrics_port }}": "8880"},
        "metrics": [
            "vmalert_liveness_last_run_unix_seconds",
            "vmalert_health_ok",
            "vmalert_api_ok",
            "vmalert_rules_loaded",
        ],
    },
    {
        # 2026-08-05 — tailnet split-DNS drift check. The per-domain gauge is
        # emitted via a printf of a pre-built block rather than a literal echo,
        # so it is asserted through its HELP/TYPE pair here; the sample-line
        # grammar for that series is covered directly in
        # tests/test_tailscale_split_dns.py.
        "template": "tailscale_dns/templates/tailscale-split-dns-check.sh.j2",
        "subs": {
            "{{ tailscale_dns_split_check_decider }}": "/usr/local/bin/tailscale_split_dns.py",
            "{{ homelab_internal_domains | join(',') }}": "example.internal",
            "{{ tailscale_dns_ip }}": "100.64.0.10",
            "{{ tailscale_dns_split_check_log }}": "/var/log/tailscale-split-dns-check.log",
        },
        "metrics": [
            "tailscale_split_dns_ok",
            "tailscale_split_dns_missing",
            "tailscale_split_dns_misrouted",
            "tailscale_split_dns_stale",
            "tailscale_split_dns_scrape_ok",
            "tailscale_split_dns_last_run_unix_seconds",
        ],
    },
]


def _render(template, subs):
    with open(os.path.join(ROLES, template)) as fh:
        src = fh.read()
    for k, v in subs.items():
        src = src.replace(k, v)
    return src


def _normalize(text):
    """Turn an emitted format/payload into concrete Prometheus text: unescape
    shell quoting, substitute shell vars and printf conversions with stand-ins."""
    text = text.replace('\\"', '"').replace("\\t", "\t").replace("\\n", "\n")
    # $( ... ) FIRST, and before the bare-$var rule: emitters routinely write
    # `metric $(date +%s)` or `metric $(numeric "$x")`, and without this the
    # guard could only ever cover scripts that avoid command substitution —
    # silently excluding the most common emitter idiom in this repo from the
    # very check that exists to catch malformed gauges. One nesting level is
    # enough for every emitter here; a nested $( ) would fail loudly, not pass.
    text = re.sub(r"\$\([^()]*\)", "123", text)       # $(cmd ...)
    text = re.sub(r"\$\{[^}]+\}", "123", text)        # ${var}
    text = re.sub(r"\$[A-Za-z_][A-Za-z0-9_]*", "123", text)  # $var
    text = re.sub(r"%[0-9]*\.?[0-9]*f", "1", text)    # %f / %.0f
    text = re.sub(r"%[0-9]*[diu]", "123", text)       # %d / %i / %u
    text = text.replace("%s", "x")                    # label value
    return text


def _emit_lines(src):
    """Every Prometheus line a script writes — from both `echo "..."` and
    `printf "..."` (awk or shell). Multi-line payloads (\\n) are split."""
    lines = []
    for m in re.finditer(r'(?:echo|printf)\s+"((?:[^"\\]|\\.)*)"', src):
        payload = _normalize(m.group(1))
        for ln in payload.split("\n"):
            ln = ln.strip()
            if ln:
                lines.append(ln)
    return lines


class TestTextfileEmitters(unittest.TestCase):
    def test_gauges_are_well_formed_prometheus(self):
        for spec in SPECS:
            src = _render(spec["template"], spec["subs"])
            lines = _emit_lines(src)
            help_lines = [ln for ln in lines if ln.startswith("# HELP ")]
            type_lines = [ln for ln in lines if ln.startswith("# TYPE ")]
            sample_lines = [ln for ln in lines if not ln.startswith("#")]

            for metric in spec["metrics"]:
                with self.subTest(template=spec["template"], metric=metric):
                    self.assertTrue(
                        any(ln.startswith(f"# HELP {metric} ") for ln in help_lines),
                        f"{metric}: missing or malformed `# HELP` line",
                    )
                    self.assertTrue(
                        any(ln == f"# TYPE {metric} gauge" for ln in type_lines),
                        f"{metric}: missing `# TYPE {metric} gauge` line",
                    )
                    samples = [
                        ln
                        for ln in sample_lines
                        if ln == metric or ln.startswith(metric + " ") or ln.startswith(metric + "{")
                    ]
                    self.assertTrue(samples, f"{metric}: no sample line emitted")
                    for ln in samples:
                        self.assertRegex(
                            ln,
                            _SAMPLE_RE,
                            f"{metric}: sample line is not valid Prometheus: {ln!r}",
                        )


if __name__ == "__main__":
    unittest.main()


class TestTextfileScrapeErrorIsWatched(unittest.TestCase):
    """Every emitter is guarded; the collector itself was not.

    Found 2026-08-03: `node_textfile_scrape_error` was sitting at 1 and nothing
    alerted on it. Two files were being rejected — `mediacover_reap.prom` for a
    duplicate HELP line and `homepage_probe.prom` for client-side timestamps —
    so 14 MediaCover gauges were written correctly every night and never
    ingested, and the two vmalert rules reading them could never fire.

    This is the blind spot behind CLAUDE.md hard rule #1: the rule says every
    cron must emit a metric, and every emitter here obeyed it. Nothing checked
    that the metric ARRIVED.
    """

    def test_a_rule_watches_node_textfile_scrape_error(self):
        rules_dir = REPO / "ansible/roles/vmalert/templates/rules"
        joined = "\n".join(p.read_text() for p in rules_dir.glob("*.yml.j2"))
        self.assertIn(
            "node_textfile_scrape_error", joined,
            "nothing alerts on the textfile collector rejecting a file, so an "
            "unparseable .prom silences its gauges with no signal anywhere",
        )
        self.assertIn("NodeTextfileScrapeError", joined)
