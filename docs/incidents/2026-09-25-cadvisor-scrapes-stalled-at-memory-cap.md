# 2026-09-25 — cAdvisor health passed while metric scrapes stalled

- **What happened:** After fixing containerd root visibility and moving cAdvisor to the current official image, `/healthz` stayed healthy but `/metrics` exceeded 20 seconds. CPU reached 258% on the four-core Pi.
- **Why:** Restoring filesystem visibility enabled recursive image-filesystem walks. The old 128 MiB ceiling was exhausted; 384 MiB initially restored responses but still produced 11-second scrapes. Cgroup accounting showed 374 MiB of kernel slab (373 MiB reclaimable), versus 28 MiB process RSS. This was cache pressure from directory walks, not a large Go heap (9.7 MiB). No OOM restart was recorded.
- **Class:** A health endpoint does not establish service usefulness; retained resource limits need workload validation after an upgrade.
- **Occurrence:** First recorded instance for cAdvisor; related to other successful-deploy/unhealthy-runtime incidents.
- **Structural fix:** Disable only the expensive `disk` size/inode walk, retaining `diskIO`, CPU, memory, network, pressure and OOM metrics. No repository consumer uses container size totals; host capacity remains in node-exporter. Keep a 384 MiB ceiling pending measured steady-state sizing. Verify actual scrape latency and resource use after monitoring upgrades, alongside process health.
