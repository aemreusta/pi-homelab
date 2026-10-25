"""Render-and-parse guard for vmalert rule templates.

Why this exists
---------------
Twice in two sprints a vmalert rule shipped with broken YAML and sent the
vmalert container into a restart loop on deploy:

  - 368e10a  a pipeline-metrics rule file  (qbit-cleanup sprint)
  - 86d1c25  tdarr-watcher.yml.j2         (Tdarr-dedup sprint, hotfix)

Both are the SAME bug class: an outer **double-quoted** YAML annotation whose
rendered inner `printf "%.0f"` double-quotes collide with the outer quote.
The collision is invisible in the raw `.j2` source — it only appears *after*
Jinja2 renders the escaped Go-template block (`{{ "{{ $value ... }}" }}`),
which is exactly what Ansible ships to the Pi. So the only way to catch it in
CI is to render the Jinja2 surface and then YAML-parse the result.

This test does that for every rule template, and pins the exact pre-86d1c25
broken annotation as a regression fixture so we have *proof* the test catches
incident #2's shape — not just a hope that it would.

Canonical safe pattern + anti-pattern: docs/conventions/vmalert-rule-quoting.md
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    import jinja2
except ImportError as exc:  # pragma: no cover
    raise SystemExit("jinja2 required for test_vmalert_rules.py — pip install jinja2") from exc

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("pyyaml required for test_vmalert_rules.py — pip install pyyaml") from exc

REPO = Path(__file__).resolve().parents[1]
RULES_DIR = REPO / "ansible" / "roles" / "vmalert" / "templates" / "rules"

VALID_SEVERITIES = {"info", "warn", "crit"}
PASCAL_CASE = re.compile(r"^[A-Z][A-Za-z0-9]*$")

# Go/Prometheus template fragments AFTER Jinja2 render. These are NOT Jinja2 —
# vmalert resolves them at alert-evaluation time. They must survive rendering
# verbatim (the escaped `{{ "{{ $value }}" }}` blocks render down to these).
NAKED_GO_TEMPLATE = re.compile(r"\{\{-?\s*\$")


class _Placeholder(jinja2.ChainableUndefined):
    """Renders any undefined variable to a QUOTE-FREE token.

    Quote-free is the load-bearing property: if the placeholder contained a
    `"` it could either *introduce* a phantom collision (false fail) or *mask*
    a real one (false pass). `__UNDEF__` can do neither. Because this is still
    an Undefined subclass, `| default(...)` filters in the templates continue
    to fire, so thresholds with a default render to their real value.
    """

    def __str__(self) -> str:  # noqa: D401 - simple override
        return "__UNDEF__"


def _env() -> jinja2.Environment:
    # autoescape off by design — these are YAML rule files, not HTML.
    return jinja2.Environment(  # noqa: S701
        loader=jinja2.FileSystemLoader(str(RULES_DIR)),
        undefined=_Placeholder,
        keep_trailing_newline=True,
    )


def _render(name: str) -> str:
    return _env().get_template(name).render()


def _rule_templates() -> list[str]:
    return sorted(p.name for p in RULES_DIR.glob("*.yml.j2"))


def _iter_rules(doc: dict):
    for group in doc.get("groups", []):
        gname = group.get("name", "<unnamed>")
        for rule in group.get("rules", []):
            yield gname, rule


class TestRuleTemplatesParse(unittest.TestCase):
    """Every rendered rule template must be valid, well-formed YAML."""

    def test_alert_names_are_unique_within_each_group(self):
        for name in _rule_templates():
            doc = yaml.safe_load(_render(name))
            for group in doc.get("groups", []):
                names = [rule["alert"] for rule in group.get("rules", []) if "alert" in rule]
                with self.subTest(template=name, group=group["name"]):
                    self.assertEqual(len(names), len(set(names)))

    def test_templates_exist(self):
        names = _rule_templates()
        self.assertTrue(names, f"no *.yml.j2 found under {RULES_DIR}")

    def test_each_template_renders_to_valid_yaml(self):
        for name in _rule_templates():
            with self.subTest(template=name):
                rendered = _render(name)
                try:
                    doc = yaml.safe_load(rendered)
                except yaml.YAMLError as exc:
                    self.fail(
                        f"{name} rendered to INVALID YAML — this is the "
                        f"368e10a/86d1c25 quote-collision class.\n"
                        f"See docs/conventions/vmalert-rule-quoting.md.\n{exc}"
                    )
                self.assertIsInstance(doc, dict, f"{name}: top level is not a mapping")
                self.assertIn("groups", doc, f"{name}: missing top-level 'groups'")
                self.assertTrue(doc["groups"], f"{name}: 'groups' is empty")

    def test_go_template_fragments_survive_render(self):
        """Escaped `{{ $value }}` blocks must render verbatim, not vanish.

        A naked (un-escaped) `{{ $value }}` would be eaten by Jinja2 — the
        OTHER latent failure mode. After render, any rule that references
        $value/$labels must still contain the Go-template fragment.
        """
        for name in _rule_templates():
            with self.subTest(template=name):
                rendered = _render(name)
                # The raw template's intent: if it tried to surface a Go var,
                # the rendered output must still carry it. We assert the
                # rendered output never contains a half-eaten `{{ $` that lost
                # its closing braces (a naked-template symptom).
                for line in rendered.splitlines():
                    if NAKED_GO_TEMPLATE.search(line):
                        self.assertIn(
                            "}}", line,
                            f"{name}: line looks like a half-rendered Go "
                            f"template (naked `{{{{ $`):\n  {line.strip()}",
                        )


class TestRuleConventions(unittest.TestCase):
    """Quality bar shared across all rules (P3 of the PM review)."""

    def _all_rules(self):
        for name in _rule_templates():
            doc = yaml.safe_load(_render(name))
            for gname, rule in _iter_rules(doc):
                yield name, gname, rule

    def test_alert_names_are_pascalcase(self):
        for name, gname, rule in self._all_rules():
            alert = rule.get("alert")
            with self.subTest(template=name, group=gname, alert=alert):
                self.assertIsNotNone(alert, f"{name}/{gname}: rule without 'alert'")
                self.assertRegex(
                    alert, PASCAL_CASE,
                    f"{name}/{gname}: alert name '{alert}' is not PascalCase",
                )

    def test_every_rule_has_for_clause(self):
        for name, gname, rule in self._all_rules():
            with self.subTest(template=name, group=gname, alert=rule.get("alert")):
                self.assertIn(
                    "for", rule,
                    f"{name}/{gname}/{rule.get('alert')}: missing 'for:' — a "
                    f"rule with no soak fires on a single noisy scrape",
                )

    def test_severity_label_is_valid(self):
        for name, gname, rule in self._all_rules():
            labels = rule.get("labels", {})
            sev = labels.get("severity")
            with self.subTest(template=name, group=gname, alert=rule.get("alert")):
                self.assertIn(
                    sev, VALID_SEVERITIES,
                    f"{name}/{gname}/{rule.get('alert')}: severity '{sev}' "
                    f"not in {VALID_SEVERITIES}",
                )

    def test_summary_and_description_present(self):
        for name, gname, rule in self._all_rules():
            ann = rule.get("annotations", {})
            with self.subTest(template=name, group=gname, alert=rule.get("alert")):
                self.assertTrue(
                    ann.get("summary"),
                    f"{name}/{gname}/{rule.get('alert')}: missing summary",
                )
                self.assertTrue(
                    ann.get("description"),
                    f"{name}/{gname}/{rule.get('alert')}: missing description",
                )


class TestContainerMemoryHeadroom(unittest.TestCase):
    """The container tier had `mem_limit` on 41 containers and ZERO alerting.

    Found by the 2026-08-02 20-day sweep. Measured as
    working_set / spec_memory_limit, six containers ran at >=99% of their own
    cap for the whole window with nothing observing it (mlflow, homepage,
    tdarr, byparr 100%; minio 99.5%; jellyseerr 99.1%). They were not
    OOM-killed only because they reclaim at the boundary — luck holding, not
    a margin. This is the silent-zero shape CLAUDE.md already legislates for
    cron jobs, applied to containers.

    The metric choice is the load-bearing part, hence a dedicated guard:
    `container_memory_usage_bytes` INCLUDES reclaimable page cache, so
    measured that way 13 containers read a meaningless flat 100%. Only
    `container_memory_working_set_bytes` reflects real pressure.
    See docs/conventions/container-mem-limits.md.
    """

    def _rules_by_name(self) -> dict:
        found = {}
        for name in _rule_templates():
            doc = yaml.safe_load(_render(name))
            for gname, rule in _iter_rules(doc):
                found[rule.get("alert")] = (name, gname, rule)
        return found

    def test_container_memory_headroom_rules_exist(self):
        rules = self._rules_by_name()
        for alert in ("ContainerMemoryHeadroomLow", "ContainerMemoryHeadroomCritical"):
            self.assertIn(
                alert, rules,
                f"{alert} missing — 41 containers carry mem_limit and nothing "
                f"alerts on approaching it (2026-08-02 sweep, TODO.md P0)",
            )

    def test_headroom_rules_use_working_set_not_usage(self):
        """THE regression guard: `usage` includes page cache and reads ~100%.

        If someone 'simplifies' these to container_memory_usage_bytes the
        alert becomes permanently firing noise and gets muted — which is
        strictly worse than having no rule at all.
        """
        rules = self._rules_by_name()
        for alert in ("ContainerMemoryHeadroomLow", "ContainerMemoryHeadroomCritical"):
            with self.subTest(alert=alert):
                if alert not in rules:
                    self.fail(f"{alert} missing (see previous test)")
                expr = rules[alert][2]["expr"]
                self.assertIn(
                    "container_memory_working_set_bytes", expr,
                    f"{alert}: must measure working set, not usage",
                )
                self.assertNotIn(
                    "container_memory_usage_bytes", expr,
                    f"{alert}: uses container_memory_usage_bytes — that "
                    f"INCLUDES reclaimable page cache, so it reads a flat "
                    f"~100% for most containers and the alert is noise. "
                    f"Use container_memory_working_set_bytes. "
                    f"docs/conventions/container-mem-limits.md",
                )
                self.assertIn(
                    "container_spec_memory_limit_bytes", expr,
                    f"{alert}: must compare against the container's own "
                    f"declared limit, not a host-wide constant",
                )

    def test_headroom_rules_keep_the_container_name(self):
        """MetricsQL `a / on(id) b` keeps ONLY `id` on the result (unlike
        Prometheus), so `$labels.name` rendered empty and the 2026-10-02 page
        read " CRIT 97% of its mem_limit" with no container named. Verified
        live: `on(id) group_left()` keeps the left-hand labels incl. `name`.
        """
        rules = self._rules_by_name()
        for alert in ("ContainerMemoryHeadroomLow", "ContainerMemoryHeadroomCritical"):
            with self.subTest(alert=alert):
                expr = rules[alert][2]["expr"]
                self.assertIn("on(id) group_left()", expr)

    def test_no_rule_anywhere_uses_container_memory_usage_bytes(self):
        """Repo-wide class guard, not just for the two rules above.

        Scoped to `expr:` deliberately. The guard is about what a rule
        EVALUATES, not what its comments discuss — naming the anti-pattern in
        a comment is how the next reader learns why it is wrong, so a raw
        substring search over the template would punish good documentation.
        """
        for name in _rule_templates():
            doc = yaml.safe_load(_render(name))
            for gname, rule in _iter_rules(doc):
                with self.subTest(template=name, group=gname, alert=rule.get("alert")):
                    self.assertNotIn(
                        "container_memory_usage_bytes", str(rule.get("expr", "")),
                        f"{name}/{rule.get('alert')}: container_memory_usage_bytes "
                        f"includes reclaimable page cache — use "
                        f"container_memory_working_set_bytes",
                    )

    def test_headroom_thresholds_are_ordered_and_soaked(self):
        """crit must be stricter than warn, and both must soak.

        Working set spikes on legitimate bursts (a Tdarr scan, an mlflow run).
        Without a soak these page on noise and get muted — the alert-fatigue
        failure mode this repo has already hit on SrvDiskHigh.
        """
        rules = self._rules_by_name()
        if not {"ContainerMemoryHeadroomLow", "ContainerMemoryHeadroomCritical"} <= rules.keys():
            self.fail("headroom rules missing (see previous test)")

        def pct(alert: str) -> float:
            expr = rules[alert][2]["expr"]
            nums = re.findall(r">=\s*([0-9]+(?:\.[0-9]+)?)", expr)
            self.assertTrue(nums, f"{alert}: no `>= <threshold>` found in expr")
            return float(nums[-1])

        warn, crit = pct("ContainerMemoryHeadroomLow"), pct("ContainerMemoryHeadroomCritical")
        self.assertLess(warn, crit, f"warn ({warn}) must be below crit ({crit})")
        for alert, sev in (
            ("ContainerMemoryHeadroomLow", "warn"),
            ("ContainerMemoryHeadroomCritical", "crit"),
        ):
            rule = rules[alert][2]
            self.assertEqual(rule["labels"]["severity"], sev, f"{alert}: wrong severity")
            self.assertTrue(
                str(rule["for"]).endswith(("m", "h")),
                f"{alert}: 'for' must soak (got {rule['for']!r})",
            )


class TestHostUnexpectedReboot(unittest.TestCase):
    """Seven boots in 5.5h on 2026-08-01/02 produced zero pages.

    Those were operator-initiated, which is fine — the defect is that the
    alert stack CANNOT TELL a planned reboot from an unplanned one, so the
    next genuinely unexpected restart is equally silent. Incident-class
    detection (docs/incidents/README.md) depends on being told a restart
    happened at all.
    """

    def _rules_by_name(self) -> dict:
        found = {}
        for name in _rule_templates():
            doc = yaml.safe_load(_render(name))
            for gname, rule in _iter_rules(doc):
                found[rule.get("alert")] = (name, gname, rule)
        return found

    def test_host_unexpected_reboot_rule_exists(self):
        rules = self._rules_by_name()
        self.assertIn(
            "HostUnexpectedReboot", rules,
            "HostUnexpectedReboot missing — the host restarted 7x on "
            "2026-08-01/02 and nothing fired (TODO.md P1)",
        )

    def test_reboot_rule_watches_boot_time(self):
        rules = self._rules_by_name()
        if "HostUnexpectedReboot" not in rules:
            self.fail("HostUnexpectedReboot missing (see previous test)")
        expr = rules["HostUnexpectedReboot"][2]["expr"]
        self.assertIn(
            "node_boot_time_seconds", expr,
            "must watch node_boot_time_seconds — up/absent can't distinguish "
            "a reboot from a scrape gap",
        )
        self.assertIn(
            "changes(", expr,
            "must use changes() — node_boot_time_seconds is an absolute "
            "timestamp, so only its CHANGE marks a restart",
        )

    def test_reboot_rule_does_not_soak_longer_than_its_window(self):
        """A `for:` longer than the changes() window can never fire.

        changes(...[1h]) returns to 0 one hour after the reboot; if `for:`
        exceeded that, the condition would lapse before the soak completed
        and the rule would be structurally dead — the same shape as the
        discovery poller that could never observe `completed`.
        """
        rules = self._rules_by_name()
        if "HostUnexpectedReboot" not in rules:
            self.fail("HostUnexpectedReboot missing (see previous test)")
        rule = rules["HostUnexpectedReboot"][2]
        window = re.search(r"\[(\d+)([mh])\]", rule["expr"])
        self.assertIsNotNone(window, "no range selector found in expr")
        w_min = int(window.group(1)) * (60 if window.group(2) == "h" else 1)

        soak = re.match(r"^(\d+)([mhs])$", str(rule["for"]))
        self.assertIsNotNone(soak, f"unparseable 'for': {rule['for']!r}")
        unit = {"s": 1 / 60, "m": 1, "h": 60}[soak.group(2)]
        self.assertLess(
            int(soak.group(1)) * unit, w_min,
            f"for: {rule['for']} >= changes() window {window.group(0)} — "
            f"this rule can never fire",
        )


class TestRegressionIncident2(unittest.TestCase):
    """PROOF the guard catches the exact pre-86d1c25 broken annotation.

    Without this, the parse test could ship green while structurally unable to
    catch incident #3 (PM review B1/M1). We render the verbatim broken shape
    and assert it does NOT survive YAML parsing; then assert the fixed shape
    does. If someone "simplifies" the parser away, this fails loudly.
    """

    # Verbatim pre-86d1c25 annotation: outer DOUBLE quote, inner Jinja raw
    # block rendering down to `printf "%.0f"` → the surviving `"` collide.
    BROKEN = (
        "groups:\n"
        "  - name: regression\n"
        "    rules:\n"
        "      - alert: TdarrErrorsAccumulating\n"
        "        expr: tdarr_errors_active > 20\n"
        "        for: 48h\n"
        "        labels: {severity: warn}\n"
        "        annotations:\n"
        "          summary: \"Tdarr: {{ '{{ $value | printf \\\"%.0f\\\" }}' }} "
        "active errors\"\n"
    )

    # The 86d1c25 fix: outer SINGLE quote, inner double-quoted Jinja block.
    FIXED = (
        "groups:\n"
        "  - name: regression\n"
        "    rules:\n"
        "      - alert: TdarrErrorsAccumulating\n"
        "        expr: tdarr_errors_active > 20\n"
        "        for: 48h\n"
        "        labels: {severity: warn}\n"
        "        annotations:\n"
        "          summary: 'Tdarr: {{ \"{{ $value | printf \\\"%.0f\\\" }}\" }} "
        "active errors'\n"
    )

    def _render_str(self, template_str: str) -> str:
        return jinja2.Environment(undefined=_Placeholder).from_string(template_str).render()  # noqa: S701

    def test_broken_annotation_is_rejected(self):
        rendered = self._render_str(self.BROKEN)
        # Sanity: the rendered output really does carry the colliding quotes.
        self.assertIn('printf "%.0f"', rendered)
        with self.assertRaises(yaml.YAMLError):
            yaml.safe_load(rendered)

    def test_fixed_annotation_parses(self):
        rendered = self._render_str(self.FIXED)
        doc = yaml.safe_load(rendered)
        summary = doc["groups"][0]["rules"][0]["annotations"]["summary"]
        # Go-template fragment survives verbatim for vmalert to resolve later.
        self.assertIn('{{ $value | printf "%.0f" }}', summary)


def _render_with(name: str, **variables) -> str:
    """Render a rule template with explicit variables bound.

    `_render` deliberately binds nothing so every threshold falls back to its
    `| default(...)`. Gated rule blocks need the opposite: proving that a
    `{% if %}` gate emits the rules when the flag is ON requires actually
    setting it.
    """
    return _env().get_template(name).render(**variables)


def _alert_names(rendered: str) -> set[str]:
    doc = yaml.safe_load(rendered) or {}
    return {rule.get("alert") for _, rule in _iter_rules(doc) if rule.get("alert")}


class TestSecondDiskRulesAreGated(unittest.TestCase):
    """The Kingston/second-SSD rules must follow the mount, not outlive it.

    Regression, 2026-08-02: design 042 Phase 1 shipped DataDiskMissing together
    with the second-SSD mount. The drive was then removed, `storage_ssd2_enabled`
    went back to false -- but the ALERT stayed. `absent()` on a filesystem that
    no longer exists is true forever, so DataDiskMissing sat at severity=crit
    continuously.

    The damage is not the single alert. Both crit-severity alerts on this host
    were firing permanently, which is exactly how `crit` stops meaning anything.
    A rule that watches optional hardware must be gated on the same flag that
    creates the hardware.
    """

    TEMPLATE = "host-saturation.yml.j2"
    SECOND_DISK_ALERTS = {
        "DataDiskMissing",
        "DataDiskReadOnly",
        "DataDiskHigh",
        "DataDiskCritical",
    }

    def test_absent_when_flag_is_unset(self):
        # Unset == the _Placeholder undefined, so `| default(false)` must win.
        names = _alert_names(_render(self.TEMPLATE))
        leaked = self.SECOND_DISK_ALERTS & names
        self.assertFalse(
            leaked,
            f"{sorted(leaked)} rendered while storage_ssd2_enabled is unset. An "
            "alert for a disk that is not mounted fires forever and devalues its "
            "whole severity class.",
        )

    def test_absent_when_flag_is_false(self):
        names = _alert_names(_render_with(self.TEMPLATE, storage_ssd2_enabled=False))
        self.assertFalse(self.SECOND_DISK_ALERTS & names)

    def test_present_when_flag_is_true(self):
        # The gate must not become a permanent delete -- re-enabling the mount
        # has to bring its monitoring back with it, or the next second disk
        # silently fills.
        names = _alert_names(
            _render_with(
                self.TEMPLATE,
                storage_ssd2_enabled=True,
                storage_ssd2_mount="/mnt/ssd2",
            )
        )
        missing = self.SECOND_DISK_ALERTS - names
        self.assertFalse(
            missing,
            f"{sorted(missing)} did not render with storage_ssd2_enabled=true — "
            "the gate deleted the rules instead of gating them.",
        )


class TestDiskThresholds(unittest.TestCase):
    """Disk thresholds are an operator contract, not a tuning knob.

    Operator directive 2026-08-02: the actionable line is 90%. The crit rule had
    drifted to 95 while its own neighbouring comment still claimed 90 — the kind
    of silent drift that makes the next reader trust the wrong number.
    """

    TEMPLATE = "host-saturation.yml.j2"

    def _rule(self, alertname: str) -> dict:
        doc = yaml.safe_load(_render(self.TEMPLATE))
        for _, rule in _iter_rules(doc):
            if rule.get("alert") == alertname:
                return rule
        self.fail(f"{alertname} not found in {self.TEMPLATE}")

    def test_critical_threshold_is_90(self):
        self.assertIn(">= 90", self._rule("SrvDiskCritical")["expr"])

    def test_warn_threshold_is_below_critical(self):
        # A warn that fires at the same time as crit is not a warning, it is a
        # duplicate. Inhibition hides it downstream, but the rule should still
        # be ordered correctly at the source.
        self.assertIn(">= 85", self._rule("SrvDiskHigh")["expr"])

    def test_disk_rules_aggregate_away_the_device_label(self):
        """`device` splits the root filesystem's series when USB re-enumerates.

        Measured 2026-08-02: `node_filesystem_avail_bytes{mountpoint="/"}` had
        2,366 samples on `/dev/sda` and **13** on `/dev/sdb` over 24h, with none
        on sdb in the last 6h. The root SSD dropped off the USB bus and came back
        under a different device node — a documented, recurring event on this
        host (docs/conventions/pi4-usb-power-budget.md).

        Two consequences, both live:
          1. `predict_linear(...[24h:1h])` produced a result for the DEAD sdb
             series, extrapolating 7 days forward from samples that stopped hours
             earlier. SrvDiskFillRateHigh was firing on a ghost.
          2. The `unless` suppression could never match, because the left side
             carried device=/dev/sdb and the right side device=/dev/sda.

        `mountpoint="/"` already identifies the filesystem uniquely here, so
        `device` carries no information and is actively harmful. Aggregating it
        away makes the series continuous across a rename.
        """
        expr = self._rule("SrvDiskFillRateHigh")["expr"]
        self.assertIn(
            "by (mountpoint)",
            expr,
            "SrvDiskFillRateHigh must aggregate away `device`, or a USB "
            "re-enumeration splits the series and it predicts from a dead one.",
        )

    def test_fill_rate_rule_is_suppressed_once_critical(self):
        """A prediction of a state that has already arrived is pure noise.

        SrvDiskFillRateHigh projects 7 days ahead. Once the disk is already past
        the crit line, "it will be full within a week" adds nothing to "it is
        full now" — but it kept paging alongside it.
        """
        expr = self._rule("SrvDiskFillRateHigh")["expr"]
        self.assertIn("unless", expr)


class TestAdguardUpstreamFailures(unittest.TestCase):
    """The one measured, user-visible failure that had no rule at all.

    2026-08-02: ~12-15% of LAN DNS queries hung 20s and returned SERVFAIL for
    days. Nothing alerted, because AdGuard's own `avg_processing_time` averaged
    the tail away and read 3.04 ms. The SLI must be the failure COUNT.
    """

    TEMPLATE = "container-error-rate.yml.j2"

    def test_rule_exists(self):
        self.assertIn("AdguardUpstreamFailures", _alert_names(_render(self.TEMPLATE)))

    def _rule(self) -> dict:
        doc = yaml.safe_load(_render(self.TEMPLATE))
        return next(
            r for _, r in _iter_rules(doc) if r.get("alert") == "AdguardUpstreamFailures"
        )

    def test_rule_counts_failures_rather_than_averaging_latency(self):
        rule = self._rule()
        self.assertIn("adguard", rule["expr"])
        self.assertIn("exchange failed", rule["expr"])

    def test_rule_does_not_count_absorbed_upstream_retries(self):
        """`i/o timeout` counts upstream ATTEMPTS, not client-visible failures.

        With upstream_mode=parallel a lost packet on one upstream is absorbed by
        the other, so `i/o timeout` lines keep appearing while the client is
        served fine. Measured 2026-08-02 in the first 8 minutes after the fix
        deployed: 26 `i/o timeout` against 9 `exchange failed` (~2.9:1, from 1:1
        before). Matching the wrong string would page on a condition the fix has
        already handled.
        """
        self.assertNotIn("i/o timeout", self._rule()["expr"])


class TestNoKnownFalsePositiveRulesKeptAsBackup(unittest.TestCase):
    """A rule you have documented as firing falsely is not a "belt-and-suspenders".

    `SpeedtestStale` inferred staleness from `changes(speedtest_tracker_download_bits[3h])
    == 0`. Its replacement `SpeedtestStaleV2` reads a real last-result timestamp, and the
    template's own comment said why: *"The metric-delta approach fires false positives when
    speedtest keeps returning the same result (changes() == 0 even though tests are
    running)."* Both were then kept, explicitly labelled belt-and-suspenders.

    On 2026-08-02 the predicted false positive happened: V1 fired `warn` while V2 — the
    rule the same comment calls authoritative — was inactive, `speedtest_last_result_ok`
    was 1 and `speedtest_failed_last_24h` was 0. A stable link returning identical figures
    is the documented trigger.

    Redundant alerting is good when the copies fail independently. It is not redundancy
    when one copy is known-wrong: it just guarantees a false page whose only defence is an
    operator remembering which of two similarly-named alerts to ignore.
    """

    # The rule is inside `{% if vmalert_speedtest_freshness_metric %}`, and the
    # bare `_render` helper binds NOTHING, so the block is skipped and any
    # assertion about it passes vacuously. That is how the first version of this
    # test went green against a rule that was firing on the live box at that very
    # moment. Bind the production default explicitly — a gated block is invisible
    # to a variable-free render, so gates must be opened deliberately in tests.
    PROD_VARS = {"vmalert_speedtest_freshness_metric": "speedtest_tracker_download_bits"}

    def test_speedtest_staleness_has_one_authoritative_rule(self):
        names = _alert_names(
            _render_with("observability-services.yml.j2", **self.PROD_VARS)
        )
        # Guard the guard: if the gate ever stops rendering these rules at all,
        # this test must fail loudly rather than pass on an empty set.
        self.assertIn("SpeedtestScrapeDown", names, "gated block did not render")
        self.assertIn(
            "SpeedtestStaleV2",
            names,
            "the timestamp-based staleness rule must exist",
        )
        self.assertNotIn(
            "SpeedtestStale",
            names,
            "SpeedtestStale infers staleness from changes()==0 on a gauge and is "
            "documented in this very template as firing false positives when the "
            "link is stable. It fired falsely on 2026-08-02 while SpeedtestStaleV2 "
            "correctly stayed inactive. Delete it rather than keeping it as backup.",
        )

    def test_no_rule_infers_staleness_from_changes_on_a_gauge(self):
        """The general shape, so the next rule does not re-learn this.

        `changes(x[window]) == 0` conflates "the producer stopped" with "the value
        is genuinely constant". Freshness needs a timestamp, not a value delta.
        """
        offenders = []
        for name in _rule_templates():
            doc = yaml.safe_load(_render_with(name, **self.PROD_VARS)) or {}
            for _, rule in _iter_rules(doc):
                expr = str(rule.get("expr", ""))
                if re.search(r"changes\([^)]*\)\s*==\s*0", expr):
                    offenders.append(f"{name}:{rule.get('alert')}")
        self.assertFalse(
            offenders,
            f"{offenders} infer staleness from a value delta. A constant value is not "
            "a stopped producer — alert on a last-success timestamp instead.",
        )


class TestIndexerHealthScope(unittest.TestCase):
    def test_mac_absence_inhibits_only_mac_dependent_probes(self):
        rules = {r["alert"]: r for _, r in _iter_rules(yaml.safe_load(_render("indexer-health.yml.j2")))}
        for name in ("ByparrBrowserRequestFailed", "ProwlarrProxyUnhealthy"):
            self.assertEqual(rules[name]["labels"].get("mac_dependent"), "true")
        for name in ("IndexerHealthCollectorStale", "ProwlarrHealthUnreadable", "ProwlarrIndexerUnhealthy"):
            self.assertNotIn("mac_dependent", rules[name]["labels"])
        self.assertIn("absent(", rules["IndexerHealthCollectorStale"]["expr"])

    def test_disabled_collector_does_not_leave_absence_alerts(self):
        self.assertEqual(_alert_names(_render_with("indexer-health.yml.j2", media_indexer_health_enabled=False)), set())


if __name__ == "__main__":
    unittest.main()
