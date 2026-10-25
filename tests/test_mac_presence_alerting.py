"""Mac-away inhibition: one physical cause (the laptop sleeping) must not page N times.

The Mac worker is a MacBook on battery + Wi-Fi. On 2026-09-28/29 a 1%-battery
Low Power Sleep fanned out into ~20 ntfy pushes (NFS read failing/resolved
flapping, heartbeat stale, runner supervisor down). The fix is a single
presence signal (`homelab-agent mac-presence` → MacWorkerAway, notify="none")
that Alertmanager uses to inhibit every alert labelled mac_dependent="true".

These tests guard the wiring end to end, and — most importantly — that a
FUTURE alert reading Mac-produced metrics cannot quietly bypass the
inhibition by forgetting the label.
"""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
RULES = REPO / "ansible/roles/vmalert/templates/rules"
AM_TEMPLATES = REPO / "ansible/roles/alertmanager/templates"

# Metrics whose value is produced BY the Mac (heartbeats/status it writes) or
# only meaningful while the Mac is awake. Pi-side collector liveness
# (node_textfile_mtime_seconds, *_collector_timestamp_seconds, coverage run by
# a Pi cron) is deliberately excluded: a dead Pi cron is not "Mac asleep".
MAC_SOURCED = re.compile(
    r"\b(mac_nfs_read_ok|mac_nfs_status_valid|mac_nfs_check_timestamp_seconds"
    r"|gh_runner_heartbeat_stale|gh_runner_idle_count|gh_runner_queued_jobs"
    r"|mac_offload_heartbeat_timestamp_seconds|mac_offload_run_skipped|mac_offload_days_missed"
    r"|mac_offload_run_success|mac_offload_missing_objects|tdarr_node_relay_errors)\b"
)

# Reads a Mac-driven metric but cannot be made spuriously TRUE by the Mac
# sleeping: it fires on a converged (small) backlog sustained for 7 days.
EXEMPT = {"MacOffloadSprintCGateReady"}


class _Undef(jinja2.ChainableUndefined):
    def __str__(self) -> str:
        return "__UNDEF__"


def _render(directory: Path, name: str) -> dict:
    env = jinja2.Environment(  # noqa: S701 - YAML, not HTML
        loader=jinja2.FileSystemLoader(str(directory)), undefined=_Undef
    )
    return yaml.safe_load(env.get_template(name).render())


def _rules():
    for path in sorted(RULES.glob("*.yml.j2")):
        doc = _render(RULES, path.name) or {}
        for group in doc.get("groups", []):
            for rule in group.get("rules", []):
                if "alert" in rule:
                    yield path.name, rule


def _rule(name: str) -> dict:
    matches = [r for _, r in _rules() if r["alert"] == name]
    assert len(matches) == 1, f"{name} must be defined exactly once"
    return matches[0]


def test_mac_worker_away_is_an_inhibition_source_only():
    rule = _rule("MacWorkerAway")
    assert rule["labels"]["notify"] == "none"
    assert rule["labels"]["severity"] == "info"
    assert "mac_dependent" not in rule["labels"], "the source must not inhibit itself"
    # Away OR settling after wake — the settle window is what stops stale
    # heartbeats paging in the minutes after the lid opens.
    assert "mac_worker_reachable == 0" in rule["expr"]
    assert "mac_worker_reachable_since_timestamp_seconds" in rule["expr"]


def test_long_absence_still_pages_once():
    rule = _rule("MacWorkerAwayLong")
    assert rule["labels"]["severity"] == "warn"
    assert "notify" not in rule["labels"]
    assert "mac_dependent" not in rule["labels"], "must not be inhibited by the condition it reports"


def test_collector_staleness_is_not_inhibited():
    rule = _rule("MacPresenceCollectorStale")
    assert "mac_dependent" not in rule["labels"]
    assert "absent(mac_worker_probe_timestamp_seconds)" in rule["expr"]


def test_every_alert_on_mac_sourced_metrics_is_mac_dependent():
    missing = [
        f"{fname}:{rule['alert']}"
        for fname, rule in _rules()
        if MAC_SOURCED.search(str(rule.get("expr", "")))
        and rule["alert"] not in EXEMPT
        and rule.get("labels", {}).get("mac_dependent") != "true"
    ]
    assert not missing, (
        "alerts reading Mac-produced metrics must carry mac_dependent: \"true\" "
        f"or they page every time the laptop sleeps: {missing}"
    )


@pytest.mark.parametrize(
    "name",
    ["MacNfsCollectorStale", "GHRunnerMetricsStale"],
)
def test_pi_side_collector_liveness_is_never_inhibited(name):
    assert _rule(name).get("labels", {}).get("mac_dependent") is None


def test_guard_detects_an_unlabelled_mac_alert():
    fake = {"alert": "X", "expr": "gh_runner_heartbeat_stale == 1", "labels": {"severity": "warn"}}
    assert MAC_SOURCED.search(fake["expr"]) and "mac_dependent" not in fake["labels"]


@pytest.fixture(scope="module")
def am():
    return _render(AM_TEMPLATES, "alertmanager.yml.j2")


def test_blackhole_receiver_has_no_delivery(am):
    blackhole = [r for r in am["receivers"] if r["name"] == "blackhole"]
    assert blackhole == [{"name": "blackhole"}]


def test_notify_none_routes_to_blackhole_first(am):
    first = am["route"]["routes"][0]
    assert first["matchers"] == ['notify="none"']
    assert first["receiver"] == "blackhole"
    assert first.get("continue", False) is False


def test_mac_worker_away_inhibits_mac_dependent(am):
    rules = [
        r for r in am["inhibit_rules"]
        if r.get("source_matchers") == ['alertname="MacWorkerAway"']
    ]
    assert len(rules) == 1
    assert rules[0]["target_matchers"] == ['mac_dependent="true"']
    assert rules[0]["equal"] == []


def test_away_long_repeats_at_most_daily():
    defaults = yaml.safe_load((REPO / "ansible/roles/alertmanager/defaults/main.yml").read_text())
    assert "MacWorkerAwayLong" in defaults["alertmanager_sustained_alert_pattern"].split("|")


def test_mac_worker_rule_file_is_registered():
    defaults = yaml.safe_load((REPO / "ansible/roles/vmalert/defaults/main.yml").read_text())
    assert "mac-worker.yml" in defaults["vmalert_metric_rule_files"]


def test_operator_decision_states_repage_daily_not_every_4h():
    """Sustained states that wait on a human decision must not nag every 4h.

    2026-09-29: LibraryFilesTruncated (1 file, needs re-acquire) and
    QbitCompletedNotImported (3 blocked imports, need a UI decision) each sent
    an [ONGOING] push every 4h for days — the state could not change until the
    operator acted, so repeats carried no new information.
    """
    defaults = yaml.safe_load((REPO / "ansible/roles/alertmanager/defaults/main.yml").read_text())
    pattern = defaults["alertmanager_sustained_alert_pattern"].split("|")
    for name in ("LibraryFilesTruncated", "QbitCompletedNotImported"):
        assert name in pattern
