#!/usr/bin/env python3
"""Ephemeral GitHub Actions runner supervisor (Mac-side).

Keeps one *idle* ephemeral runner container alive per repo (subject to a global
concurrency cap), minting a one-time JIT config per spawn. A runner takes a
single job, auto-deregisters, and exits; this supervisor reaps it and respawns.

It does NOT run jobs itself. The PAT lives only in this process's env and is
NEVER passed to a job container — a job container receives only the opaque
`encoded_jit_config`. See docs/design/039-mac-ci-runner.md.

Observability: every tick it writes a heartbeat epoch + a status.json (Mac-local
*and* GitHub-visible state) to the NFS `gh-runner/` dir; a Pi cron turns those
into node_exporter textfile metrics. This file computes no Prometheus text — it
only emits JSON state (the diff/threshold logic lives Pi-side + in vmalert).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

LOG = logging.getLogger("gh-runner-supervisor")

API = "https://api.github.com"
JOB_LABEL = "gh-runner-job"  # docker label marking our ephemeral runner containers


def env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default if default is not None else "")
    if required and not val:
        LOG.error("missing required env %s", name)
        sys.exit(2)
    return val


class Config:
    def __init__(self) -> None:
        self.owner = env("GH_OWNER", required=True)
        self.repos = [r.strip() for r in env("GH_REPOS", required=True).split(",") if r.strip()]
        self.pat = env("GITHUB_PAT", required=True)
        self.runner_image = env("RUNNER_IMAGE", required=True)
        self.labels = env("RUNNER_LABELS", "self-hosted,linux,ARM64,homelab-build")
        self.max_concurrent_global = int(env("MAX_CONCURRENT_GLOBAL", "1"))
        self.idle_per_repo = int(env("IDLE_RUNNERS_PER_REPO", "1"))
        self.poll_interval = int(env("POLL_INTERVAL", "60"))
        self.orphan_reap_seconds = int(env("ORPHAN_REAP_SECONDS", "900"))
        self.generated_dir = Path(env("NFS_GENERATED_DIR", "/generated"))
        # Image provenance is read from the runner image's LABELs (the supervisor
        # is a different container, so it can't see the runner image's ENV).
        # Populated by inspect_runner_image() once the runner image is pulled.
        self.image_build_epoch = 0
        self.runner_version = "unknown"


class Counters:
    """In-memory counters surfaced as Prometheus counters Pi-side (restart resets
    are normal for counters)."""

    def __init__(self) -> None:
        self.jobs_total = 0
        self.jobs_failed_total = 0
        self.last_job_exit_code = 0
        self.github_api_auth_failures_total = 0
        self.jit_config_failures = {}  # status_code -> count
        self.jit_last_success_ts = 0
        self.github_api_rate_remaining = 0


# ── Docker helpers (the daemon is the Mac's, via the mounted socket) ──────────


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    # Fixed argv (docker CLI on PATH inside the supervisor image), no shell;
    # args are repo names / container ids we control.
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)  # noqa: S603, S607


def running_job_containers() -> list[dict]:
    """Return [{id, repo}] for our live runner containers."""
    out = docker(
        "ps", "--filter", f"label={JOB_LABEL}",
        "--format", "{{.ID}}|{{.Label \"gh-runner-repo\"}}",
    )
    rows = []
    for line in out.stdout.splitlines():
        if "|" in line:
            cid, repo = line.split("|", 1)
            rows.append({"id": cid, "repo": repo})
    return rows


def exited_job_containers() -> list[dict]:
    """Return [{id, repo, exit_code}] for our exited (not-yet-reaped) containers."""
    out = docker(
        "ps", "-a", "--filter", f"label={JOB_LABEL}", "--filter", "status=exited",
        "--format", "{{.ID}}|{{.Label \"gh-runner-repo\"}}",
    )
    rows = []
    for line in out.stdout.splitlines():
        if "|" not in line:
            continue
        cid, repo = line.split("|", 1)
        insp = docker("inspect", "-f", "{{.State.ExitCode}}", cid)
        code = int(insp.stdout.strip() or "0") if insp.returncode == 0 else 0
        rows.append({"id": cid, "repo": repo, "exit_code": code})
    return rows


def _docker_sock_gid() -> str | None:
    """GID of the mounted docker socket, so the non-root runner can use buildx."""
    try:
        return str(os.stat("/var/run/docker.sock").st_gid)
    except OSError:
        return None


def spawn_runner(cfg: Config, repo: str, encoded_jit_config: str) -> bool:
    name = f"gh-runner-{repo}-{uuid.uuid4().hex[:8]}"
    args = [
        "run", "-d",
        "--name", name,
        "--label", JOB_LABEL,
        "--label", f"gh-runner-repo={repo}",
        "-e", f"ENCODED_JIT_CONFIG={encoded_jit_config}",
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
    ]
    sock_gid = _docker_sock_gid()
    if sock_gid:
        args += ["--group-add", sock_gid]
    args.append(cfg.runner_image)
    res = docker(*args)
    if res.returncode != 0:
        LOG.error("docker run failed for %s: %s", repo, res.stderr.strip())
        return False
    LOG.info("spawned ephemeral runner %s for %s", name, repo)
    return True


# ── GitHub REST helpers ───────────────────────────────────────────────────────


class GitHub:
    def __init__(self, cfg: Config, counters: Counters) -> None:
        self.cfg = cfg
        self.counters = counters
        self.client = httpx.Client(
            headers={
                "Authorization": f"Bearer {cfg.pat}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=20.0,
        )

    def _record_rate(self, resp: httpx.Response) -> None:
        rem = resp.headers.get("x-ratelimit-remaining")
        if rem is not None:
            self.counters.github_api_rate_remaining = int(rem)

    def smoke_test(self) -> bool:
        """Confirm the PAT can see each repo before spawning anything."""
        ok = True
        for repo in self.cfg.repos:
            r = self.client.get(f"{API}/repos/{self.cfg.owner}/{repo}")
            self._record_rate(r)
            if r.status_code in (401, 403):
                self.counters.github_api_auth_failures_total += 1
                LOG.error("PAT auth failed for %s (%s)", repo, r.status_code)
                ok = False
            elif r.status_code != 200:
                LOG.warning("smoke test for %s returned %s", repo, r.status_code)
                ok = False
        return ok

    def generate_jit_config(self, repo: str) -> str | None:
        name = f"gh-runner-{repo}-{uuid.uuid4().hex[:8]}"
        body = {
            "name": name,
            "runner_group_id": 1,
            "labels": self.cfg.labels.split(","),
            "work_folder": "_work",
        }
        r = self.client.post(
            f"{API}/repos/{self.cfg.owner}/{repo}/actions/runners/generate-jitconfig",
            json=body,
        )
        self._record_rate(r)
        if r.status_code in (401, 403):
            self.counters.github_api_auth_failures_total += 1
            LOG.error("JIT auth failed for %s (%s)", repo, r.status_code)
            return None
        if r.status_code not in (200, 201):
            self.counters.jit_config_failures[r.status_code] = (
                self.counters.jit_config_failures.get(r.status_code, 0) + 1
            )
            LOG.error("JIT config failed for %s: %s %s", repo, r.status_code, r.text[:200])
            return None
        self.counters.jit_last_success_ts = int(time.time())
        return r.json().get("encoded_jit_config")

    def list_runners(self, repo: str) -> list[dict]:
        r = self.client.get(
            f"{API}/repos/{self.cfg.owner}/{repo}/actions/runners?per_page=100"
        )
        self._record_rate(r)
        if r.status_code in (401, 403):
            self.counters.github_api_auth_failures_total += 1
            return []
        if r.status_code != 200:
            return []
        return r.json().get("runners", [])

    def queued_jobs(self, repo: str) -> int:
        r = self.client.get(
            f"{API}/repos/{self.cfg.owner}/{repo}/actions/runs?status=queued&per_page=1"
        )
        self._record_rate(r)
        if r.status_code != 200:
            return 0
        return int(r.json().get("total_count", 0))

    def delete_runner(self, repo: str, runner_id: int) -> None:
        r = self.client.delete(
            f"{API}/repos/{self.cfg.owner}/{repo}/actions/runners/{runner_id}"
        )
        self._record_rate(r)
        if r.status_code not in (204, 404):
            LOG.warning("delete runner %s/%s returned %s", repo, runner_id, r.status_code)


# ── Supervisor loop ───────────────────────────────────────────────────────────


class Supervisor:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.counters = Counters()
        self.gh = GitHub(cfg, self.counters)
        # runner_id -> epoch first seen offline-with-no-container (reap grace).
        self._offline_since: dict[int, float] = {}

    def inspect_runner_image(self) -> None:
        """Read build provenance labels off the runner image (best effort)."""
        if self.cfg.image_build_epoch and self.cfg.runner_version != "unknown":
            return
        fmt = '{{index .Config.Labels "org.homelab.build-epoch"}}|{{index .Config.Labels "org.homelab.runner-version"}}'
        res = docker("image", "inspect", "-f", fmt, self.cfg.runner_image)
        if res.returncode == 0 and "|" in res.stdout:
            epoch, version = res.stdout.strip().split("|", 1)
            self.cfg.image_build_epoch = int(epoch) if epoch.isdigit() else 0
            self.cfg.runner_version = version or "unknown"

    def reap_exited_containers(self) -> int:
        orphans = exited_job_containers()
        for c in orphans:
            self.counters.jobs_total += 1
            self.counters.last_job_exit_code = c["exit_code"]
            if c["exit_code"] != 0:
                self.counters.jobs_failed_total += 1
            LOG.info("job done repo=%s exit=%s — reaping %s", c["repo"], c["exit_code"], c["id"])
            docker("rm", "-f", c["id"])
        return len(orphans)

    def reap_offline_runners(self, repo: str, runners: list[dict]) -> None:
        # We can't cheaply map container→runner, so reap by status + a grace
        # window: an offline registration persisting past the grace is a stale
        # orphan (ephemeral runners auto-deregister on job completion).
        now = time.time()
        for rn in runners:
            rid = rn["id"]
            if rn.get("status") == "offline":
                first = self._offline_since.setdefault(rid, now)
                if now - first >= self.cfg.orphan_reap_seconds:
                    LOG.info("reaping offline runner %s/%s (id=%s)", repo, rn.get("name"), rid)
                    self.gh.delete_runner(repo, rid)
                    self._offline_since.pop(rid, None)
            else:
                self._offline_since.pop(rid, None)

    def maybe_spawn(self, idle_by_repo: dict[str, int], live_total: int,
                    queued_by_repo: dict[str, int]) -> None:
        if live_total >= self.cfg.max_concurrent_global:
            return
        # Repos that want another idle runner, most-demanding first.
        wants = [r for r in self.cfg.repos if idle_by_repo.get(r, 0) < self.cfg.idle_per_repo]
        if not wants:
            return
        wants.sort(key=lambda r: queued_by_repo.get(r, 0), reverse=True)
        target = wants[0]
        jit = self.gh.generate_jit_config(target)
        if jit:
            spawn_runner(self.cfg, target, jit)

    def write_status(self, per_repo: dict[str, dict], live_total: int, orphans: int) -> None:
        out_dir = self.cfg.generated_dir / "gh-runner"
        out_dir.mkdir(parents=True, exist_ok=True)
        now = int(time.time())
        status = {
            "supervisor_heartbeat_ts": now,
            "idle_count": sum(p["idle"] for p in per_repo.values()),
            "live_total": live_total,
            "orphan_containers": orphans,
            "jobs_total": self.counters.jobs_total,
            "jobs_failed_total": self.counters.jobs_failed_total,
            "last_job_exit_code": self.counters.last_job_exit_code,
            "github_api_auth_failures_total": self.counters.github_api_auth_failures_total,
            "jit_config_failures": self.counters.jit_config_failures,
            "jit_last_success_ts": self.counters.jit_last_success_ts,
            "github_api_rate_remaining": self.counters.github_api_rate_remaining,
            "image_build_timestamp": self.cfg.image_build_epoch,
            "runner_version": self.cfg.runner_version,
            "repos": per_repo,
        }
        tmp = out_dir / "status.json.tmp"
        tmp.write_text(json.dumps(status, indent=2))
        tmp.replace(out_dir / "status.json")
        (out_dir / "heartbeat").write_text(str(now))

    def tick(self) -> None:
        self.inspect_runner_image()
        orphans = self.reap_exited_containers()
        live = running_job_containers()
        live_total = len(live)
        live_by_repo: dict[str, int] = {}
        for c in live:
            live_by_repo[c["repo"]] = live_by_repo.get(c["repo"], 0) + 1

        per_repo: dict[str, dict] = {}
        idle_by_repo: dict[str, int] = {}
        queued_by_repo: dict[str, int] = {}
        for repo in self.cfg.repos:
            runners = self.gh.list_runners(repo)
            self.reap_offline_runners(repo, runners)
            online = sum(1 for r in runners if r.get("status") == "online")
            busy = sum(1 for r in runners if r.get("busy"))
            offline = sum(1 for r in runners if r.get("status") == "offline")
            queued = self.gh.queued_jobs(repo)
            # idle ≈ live containers for this repo that aren't busy on GitHub.
            idle = max(0, live_by_repo.get(repo, 0) - busy)
            idle_by_repo[repo] = idle
            queued_by_repo[repo] = queued
            per_repo[repo] = {
                "registered": len(runners),
                "online": online,
                "busy": busy,
                "offline": offline,
                "queued_jobs": queued,
                "idle": idle,
            }

        self.maybe_spawn(idle_by_repo, live_total, queued_by_repo)
        self.write_status(per_repo, live_total, orphans)

    def run(self) -> None:
        LOG.info("supervisor starting: owner=%s repos=%s image=%s",
                 self.cfg.owner, self.cfg.repos, self.cfg.runner_image)
        # Block until the PAT works — an expired/wrong PAT must alert, not spawn.
        while not self.gh.smoke_test():
            LOG.error("PAT smoke test failed — retrying in %ss", self.cfg.poll_interval)
            # Still write status so GHRunnerGitHubAuthFailure can fire Pi-side.
            try:
                self.write_status({}, 0, 0)
            except OSError as exc:
                LOG.warning("status write failed: %s", exc)
            time.sleep(self.cfg.poll_interval)
        LOG.info("PAT smoke test OK")

        while True:
            try:
                self.tick()
            except (httpx.HTTPError, OSError, subprocess.SubprocessError) as exc:
                LOG.warning("tick error: %s", exc)
            time.sleep(self.cfg.poll_interval)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request at INFO. The idle poll loop makes 4 API calls per
    # tick (runners + queued runs, ×2 repos), so at POLL_INTERVAL=60 that is
    # ~5.7k noise lines/day that bury the supervisor's own spawn/reap events and
    # churn the 10 MB log ring. Keep httpx at WARNING — real HTTP failures still
    # surface (and _record_rate/JIT paths log their own errors at ERROR).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    Supervisor(Config()).run()


if __name__ == "__main__":
    main()
