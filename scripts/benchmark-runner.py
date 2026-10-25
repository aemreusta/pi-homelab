#!/usr/bin/env python3
"""Compare fixed runner snapshots on Linux; GNU time reports per-process RSS/CPU."""
import argparse
import json
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--binary", default="/usr/local/bin/homelab-agent")
p.add_argument("--reference", default="/usr/local/bin/gh_runner_metrics.py")
p.add_argument("--source", required=True)
p.add_argument("--iterations", type=int, default=30)
args = p.parse_args()
if not 3 <= args.iterations <= 100:
    p.error("iterations must be between 3 and 100")
results = {}
with tempfile.TemporaryDirectory(prefix="runner-benchmark-") as tmp:
    root = Path(tmp)
    for name in ("status.json", "heartbeat", "rebuild-status.json"):
        source = Path(args.source) / name
        if source.exists():
            (root / name).write_bytes(source.read_bytes())
    common = [str(root / "status.json"), str(root / "heartbeat"), "600", str(int(time.time())), str(root / "rebuild-status.json")]
    commands = {"python": ["/usr/bin/python3", args.reference, *common], "go": [args.binary, "runner-metrics", *common]}
    outputs = {}
    for name, command in commands.items():
        elapsed, rss, cpu = [], [], []
        for _ in range(args.iterations):
            before = time.perf_counter()
            completed = subprocess.run(  # noqa: S603 — explicit local collector CLI under operator control
                ["/usr/bin/time", "-f", "%U %S %M", "-o", str(root / "time"), *command],
                capture_output=True, text=True, check=True,
            )
            elapsed.append(time.perf_counter() - before)
            user, system, peak = map(float, (root / "time").read_text().split())
            cpu.append(user + system)
            rss.append(peak)
            outputs[name] = sorted(completed.stdout.splitlines())
        results[name] = {
            "iterations": args.iterations, "median_ms": statistics.median(elapsed) * 1000,
            "p95_ms": sorted(elapsed)[int(0.95 * (len(elapsed) - 1))] * 1000,
            "max_rss_kib": max(rss), "mean_cpu_seconds": statistics.mean(cpu),
        }
    results["metric_parity"] = outputs["python"] == outputs["go"]
    results["scope"] = "fixed current supervisor snapshot; startup+read+render, warm filesystem cache, sequential runs"
    print(json.dumps(results, indent=2))
    if not results["metric_parity"]:
        raise SystemExit(1)
