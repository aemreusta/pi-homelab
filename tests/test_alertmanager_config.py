"""Render-and-parse guard for the Alertmanager config template.

Why this exists
---------------
On 2026-06-07 the Alertmanager container was found crash-looping (114 restarts,
~3h) because its rendered config failed to load:

    matcher value contains unescaped double quote:
      "warn" alertname=~"SpeedtestScrapeDown|UptimeKumaDown|NtfyDown"

The cause was a *folded matcher* in an inhibit rule — two matchers written
under a single YAML list item:

    target_matchers:
      - severity="warn"
        alertname=~"SpeedtestScrapeDown|UptimeKumaDown|NtfyDown"   # <- no "- "

YAML folds those two lines into ONE scalar string, so Alertmanager parses a
single matcher whose "value" contains a stray `"`. It ships fine through
`ansible-playbook --syntax-check` (valid YAML, valid Jinja2) and only fails at
container start — which silently takes down ALL notification delivery (the
alerts keep firing in vmalert, but nothing routes them anywhere).

This is the SAME class as the vmalert quote-collision (368e10a/86d1c25) — a
config that is valid YAML but invalid to the consuming program — so it gets the
same treatment: render the Jinja2 surface, then validate the matcher grammar.

The deploy also gates on `amtool check-config` (the authoritative parser); this
test is the fast pre-deploy CI catch that needs no Docker/amtool.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    import jinja2
except ImportError as exc:  # pragma: no cover
    raise SystemExit("jinja2 required for test_alertmanager_config.py — pip install jinja2") from exc

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("pyyaml required for test_alertmanager_config.py — pip install pyyaml") from exc

REPO = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO / "ansible" / "roles" / "alertmanager" / "templates"
TEMPLATE = "alertmanager.yml.j2"

# A single Alertmanager matcher is `label <op> value`, op ∈ {=, !=, =~, !~}.
# After stripping quoted values, a well-formed matcher has EXACTLY ONE
# `label op` token. A folded matcher (the 06-07 bug) has two or more.
_QUOTED = re.compile(r'"[^"]*"')
_LABEL_OP = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\s*(?:=~|!~|!=|=)")


class _Placeholder(jinja2.ChainableUndefined):
    """Undefined → a quote-free token so we neither mask nor invent a collision."""

    def __str__(self) -> str:  # noqa: D401
        return "__UNDEF__"


def _render() -> str:
    env = jinja2.Environment(  # noqa: S701 - YAML, not HTML
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        undefined=_Placeholder,
        keep_trailing_newline=True,
    )
    return env.get_template(TEMPLATE).render()


def _matcher_token_count(matcher: str) -> int:
    """How many `label op` tokens a matcher string contains (1 == well-formed)."""
    return len(_LABEL_OP.findall(_QUOTED.sub('""', matcher)))


def _iter_matchers(doc: dict):
    """Yield (location, matcher_string) for every matcher in route + inhibits."""
    for rule in doc.get("inhibit_rules", []) or []:
        for key in ("source_matchers", "target_matchers"):
            for m in rule.get(key, []) or []:
                yield key, m
    # route + nested routes
    stack = [doc.get("route", {})]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        for m in node.get("matchers", []) or []:
            yield "route.matchers", m
        stack.extend(node.get("routes", []) or [])


class TestAlertmanagerConfig(unittest.TestCase):
    def setUp(self):
        self.rendered = _render()
        self.doc = yaml.safe_load(self.rendered)

    def test_renders_to_valid_yaml_mapping(self):
        self.assertIsInstance(self.doc, dict, "alertmanager.yml top level is not a mapping")
        self.assertIn("route", self.doc)
        self.assertIn("receivers", self.doc)

    def test_no_folded_matchers(self):
        """Every matcher is ONE `label op value`, never two folded into a scalar."""
        offenders = []
        for where, matcher in _iter_matchers(self.doc):
            self.assertIsInstance(
                matcher, str, f"{where}: matcher is not a string: {matcher!r}"
            )
            if _matcher_token_count(matcher) != 1:
                offenders.append((where, matcher))
        self.assertEqual(
            offenders,
            [],
            "Folded matcher(s) found — the 2026-06-07 crash-loop class. Each "
            "matcher must be its own YAML list item:\n"
            + "\n".join(f"  {w}: {m!r}" for w, m in offenders),
        )

    def test_regression_fixture_is_caught(self):
        """Proof the detector catches the exact pre-fix folded matcher."""
        broken = 'severity="warn" alertname=~"SpeedtestScrapeDown|UptimeKumaDown|NtfyDown"'
        self.assertEqual(_matcher_token_count(broken), 2)
        good = 'alertname=~"SpeedtestScrapeDown|UptimeKumaDown|NtfyDown"'
        self.assertEqual(_matcher_token_count(good), 1)


if __name__ == "__main__":
    unittest.main()
