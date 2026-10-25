# 2026-09-03 — Jellyfin's ffprobe was OOM-killed, so a direct-playable remux became a 16× transcode loop

- **What happened:** `OOMKilled` + `ContainerMemoryHeadroomCritical` (jellyfin 511/512 MiB)
  fired. A 32.8 GB H.264 remux was launching a full software HLS encode on every play
  attempt — 16 of them between 21:48 and 22:14, each dying with `FFmpeg exited with code 137`,
  each retried by the client. Initial triage read the symptom (an ffmpeg command with no
  `-c copy`) as the cause and concluded the fix was client-side or a smaller release.
- **Why:** the cause was one layer up and one hour earlier. Jellyfin probes with
  `ffprobe -analyzeduration 200M -probesize 1G`, which buffers up to 1 GiB before emitting
  any JSON; the 512 MiB `mem_limit` OOM-killed it (reproduced on the live container: exit 137,
  0 bytes stdout, `memory.peak` = exactly 512 MiB; the same probe at `-probesize 400M` returns
  56 KB of valid JSON). Jellyfin parses that empty stdout as JSON, logged
  `Error in "Probe Provider" — JsonException: The input does not contain any JSON tokens`,
  and **persisted the item with zero MediaStreams and null RunTimeTicks**. An item with no
  streams has no direct-play decision to make, so the client fell through to `live.m3u8`,
  where the server emits an HLS command with no `-map` and no codec args — ffmpeg then
  default-encodes h264+aac. The file itself was fine and always direct-playable
  (H.264 High 1080p). 1 of 32 library items was affected — the largest; a 10.2 GB title
  probed fine with 12 streams.
- **Class:** *container cap kills a helper process, not the app, and the app persists the
  failure as valid state.* The cgroup limit did its containment job on `ffprobe`; what made it
  an outage is that Jellyfin caught the downstream parse error and wrote a permanently broken
  DB row instead of failing the refresh. Neighbouring class: **the alert that named the true
  root cause was dismissed as a symptom** — `ContainerMemoryHeadroomCritical` fires per
  container against its OWN cap and said exactly what to do; triage overrode it by citing the
  ceiling-sum convention, which is about a different thing.
- **Occurrence:** 1st of the helper-process-OOM shape. The dismissed-alert shape is the 2nd
  instance of the *review/triage built on an unverified premise* class
  ([ops-discipline §4](../operations/ops-discipline.md); 1st: 2026-05-29 "/srv is a separate
  mount" would have shipped a dead alert).
- **Structural fix:** `media_mem_jellyfin` 512M → 2048M with the measurement in the comment
  (root cause), plus `media/tasks/configure-jellyfin.yml` enforcing
  `EnableVideoPlaybackTranscoding: false` — the project docs have claimed "direct play only" since the
  repo began and nothing applied it; the live policy said `True`. The two ship together on
  purpose: at 512M a stray transcode died fast, at 2048M it would instead peg four cores at
  ~0.1x realtime. **Deferred:** nothing alerts on `Error in "Probe Provider"` — an item can
  still silently land with zero streams.
