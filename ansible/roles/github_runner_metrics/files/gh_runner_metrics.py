#!/usr/bin/env python3
"""Render gh_runner.prom from the Mac supervisor's NFS status.json + heartbeat.

Pure, parameter-free logic (CLAUDE.md: cron diff/threshold logic lives in a
tested Python module under <role>/files/, not in bash/Jinja2). The bash wrapper
only orchestrates flock + atomic mv. This module:
  • reads the supervisor heartbeat epoch + status.json (both written over NFS),
  • decides heartbeat staleness against a threshold,
  • prints Prometheus text-format metrics to stdout — ALWAYS valid output, even
    when the status file is missing/garbage (emit `stale=1`, zeros — state, not
    silence). It raises only on truly unexpected internal errors.

Usage: gh_runner_metrics.py <status.json> <heartbeat> <stale_seconds> <now_epoch>
(now_epoch is injected so the renderer is deterministic + unit-testable.)
"""

from __future__ import annotations

import json
import sys


def _num(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _read_heartbeat(path: str) -> int:
    try:
        with open(path) as fh:
            return _num(fh.read().strip())
    except OSError:
        return 0


def _read_status(path: str) -> dict:
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def render(status_path: str, heartbeat_path: str, stale_seconds: int, now: int,
           rebuild_status_path: str | None = None) -> str:
    hb = _read_heartbeat(heartbeat_path)
    status = _read_status(status_path)

    # Prefer the heartbeat file; fall back to the ts inside status.json.
    supervisor_hb = hb or _num(status.get("supervisor_heartbeat_ts"))
    stale = 1 if (supervisor_hb == 0 or (now - supervisor_hb) > stale_seconds) else 0

    lines: list[str] = []

    def g(name: str, help_text: str, value: object, labels: str = "") -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name}{labels} {value}")

    def c(name: str, help_text: str, value: object, labels: str = "") -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name}{labels} {value}")

    g("gh_runner_metrics_last_run_unix_seconds", "Unix time of the last Pi emitter run.", now)
    g("gh_runner_supervisor_heartbeat_timestamp_seconds",
      "Unix ts of the last Mac supervisor heartbeat (0 if missing).", supervisor_hb)
    g("gh_runner_heartbeat_stale",
      "1 when the supervisor heartbeat is missing or older than the threshold.", stale)
    g("gh_runner_idle_count", "Idle ephemeral runner containers the supervisor believes are up.",
      _num(status.get("idle_count")))
    g("gh_runner_orphan_containers", "Exited job containers awaiting cleanup.",
      _num(status.get("orphan_containers")))
    g("gh_runner_last_job_exit_code", "Exit code of the most recent job container.",
      _num(status.get("last_job_exit_code")))
    c("gh_runner_jobs_total", "Jobs completed since supervisor start.",
      _num(status.get("jobs_total")))
    c("gh_runner_jobs_failed_total", "Jobs that exited non-zero since supervisor start.",
      _num(status.get("jobs_failed_total")))
    c("gh_runner_github_api_auth_failures_total", "GitHub API 401/403 responses (PAT problem).",
      _num(status.get("github_api_auth_failures_total")))
    g("gh_runner_jit_last_success_timestamp_seconds", "Unix ts of the last successful JIT mint.",
      _num(status.get("jit_last_success_ts")))
    g("gh_runner_github_api_rate_remaining", "GitHub REST rate-limit headroom.",
      _num(status.get("github_api_rate_remaining")))
    g("gh_runner_image_build_timestamp_seconds", "When the runner image was built (age -> staleness).",
      _num(status.get("image_build_timestamp")))

    version = str(status.get("runner_version", "unknown")).replace('"', "")
    g("gh_runner_version_info", "Constant 1; labels the pinned runner version.", 1,
      f'{{version="{version}"}}')

    # Automated-rebuild signal (Mac launchd rebuild-runner-image.sh writes
    # rebuild-status.json alongside status.json over NFS). Distinct from the
    # image-age gauge: this catches a rebuild cron that RAN but failed to push,
    # before the 21d image-age cliff. Zeros when the file is absent (rebuild
    # automation not installed) — state, not silence.
    rebuild = _read_status(rebuild_status_path) if rebuild_status_path else {}
    g("gh_runner_rebuild_last_attempt_timestamp_seconds",
      "Unix ts of the last runner-image rebuild attempt (0 if never).",
      _num(rebuild.get("last_attempt_ts")))
    g("gh_runner_rebuild_last_success_timestamp_seconds",
      "Unix ts of the last SUCCESSFUL runner-image rebuild (0 if never).",
      _num(rebuild.get("last_success_ts")))
    g("gh_runner_rebuild_last_exit_code",
      "Exit code of the last rebuild attempt (non-zero => failed push/build).",
      _num(rebuild.get("last_exit_code")))

    # jit_config_failures is a {status_code: count} map → one counter series each.
    jit_fail = status.get("jit_config_failures") or {}
    lines.append("# HELP gh_runner_jit_config_failures_total JIT config mint failures by HTTP status.")
    lines.append("# TYPE gh_runner_jit_config_failures_total counter")
    if isinstance(jit_fail, dict) and jit_fail:
        for code, count in jit_fail.items():
            safe = str(code).replace('"', "")
            lines.append(f'gh_runner_jit_config_failures_total{{status="{safe}"}} {_num(count)}')
    else:
        lines.append('gh_runner_jit_config_failures_total{status="none"} 0')

    # Per-repo GitHub-visible counts.
    repos = status.get("repos") or {}
    repo_metrics = [
        ("gh_runner_registered_count", "Runners GitHub lists for the repo.", "registered"),
        ("gh_runner_online_count", "Online runners for the repo.", "online"),
        ("gh_runner_busy_count", "Busy runners (running a job) for the repo.", "busy"),
        ("gh_runner_offline_count", "Offline runners (orphans pending reap) for the repo.", "offline"),
        ("gh_runner_queued_jobs", "Queued homelab-build jobs awaiting a runner.", "queued_jobs"),
    ]
    for name, help_text, key in repo_metrics:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        if isinstance(repos, dict) and repos:
            for repo, info in repos.items():
                safe = str(repo).replace('"', "")
                val = _num(info.get(key)) if isinstance(info, dict) else 0
                lines.append(f'{name}{{repo="{safe}"}} {val}')
        else:
            lines.append(f'{name}{{repo="none"}} 0')

    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    # 5 args (legacy) or 6 (with the optional rebuild-status.json path).
    if len(argv) not in (5, 6):
        sys.stderr.write(
            "usage: gh_runner_metrics.py <status.json> <heartbeat> <stale_seconds> "
            "<now_epoch> [rebuild_status.json]\n"
        )
        return 2
    status_path, heartbeat_path, stale_raw, now_raw = argv[1:5]
    rebuild_path = argv[5] if len(argv) == 6 else None
    sys.stdout.write(
        render(status_path, heartbeat_path, _num(stale_raw), _num(now_raw), rebuild_path)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
