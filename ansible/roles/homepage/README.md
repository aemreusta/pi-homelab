# homepage role

Deploys [gethomepage/homepage](https://gethomepage.dev/) — the dashboard at
`https://homepage.example` (host port `3001`). Config is templated from
`templates/*.j2` into the container's `/app/config`.

## Layout

Service groups (`templates/services.yaml.j2`, ordered by `layout:` in
`templates/settings.yaml.j2`):

| Group | Contents |
| --- | --- |
| Media | Jellyfin, Sonarr, Radarr, Prowlarr, qBittorrent, **Tdarr** (widget), Jellyseerr |
| Network | AdGuard, NextDNS, Traefik, Tailscale, ntfy |
| Infrastructure | Portainer, FileBrowser, Watchtower, Control API, byparr |
| Data | MinIO, Label Studio |
| Observability | Grafana, Alertmanager (Problems), VictoriaLogs (Events), VictoriaMetrics, vmagent, vmalert, vmalert-logs, Beszel, Uptime Kuma, Speedtest Tracker |
| Exporters | cAdvisor, node-exporter, fluentbit — `initiallyCollapsed` |

## Top bar (`templates/widgets.yaml.j2`)

Logo, greeting, datetime, weather (Open-Meteo, Istanbul), host resources. The
Google **search widget was removed** (operator request). The datetime uses
`locale: tr` → Türkiye format (`7 Haziran 2026 18:49`, 24h).

## Metrics — synthetic probe → Grafana

A host cron (`/usr/local/bin/homepage-probe.sh`, every 2 min) curls the dashboard
and its backing service endpoints, measuring **HTTP response time + reachability**,
and writes them to the node_exporter textfile collector:

```text
homepage_probe_duration_seconds{target="..."}   # response time (s)
homepage_probe_up{target="..."}                  # 1 = answered, 0 = no response
homepage_probe_http_code{target="..."}           # last status code
homepage_probe_last_run_timestamp_seconds        # liveness (silent-cron rule)
```

Flow: textfile → vmagent (`node-exporter` job) → VictoriaMetrics → Grafana
dashboard **"Homepage / Service Probe"**
(`grafana/files/dashboards/homepage-probe.json`), plus vmalert rules in
`vmalert/templates/rules/homepage-probe.yml.j2`:

- `HomepageProbeStale` — the cron itself stopped (silent-cron hard rule).
- `HomepageProbeTargetDown` — a target stopped answering.
- `HomepageProbeSlow` — a target is slow enough to drag the dashboard render.

Config (targets, timeout, schedule, on/off) lives in `defaults/main.yml`
(`homepage_probe_*`). **This is server-side response time, not the in-browser
render time.** For true in-browser pageload (Navigation Timing), a RUM approach
would be needed — deliberately not built (it needs a browser-writable metrics
endpoint; out of scope).

## Versions & Changelog drawer

A floating pill in the bottom-right (`📋 Changelog <latest-version>`) opens a
right-side slide-in drawer that renders the repo `CHANGELOG.md` (when present) in-dashboard, one
release per collapsible section (Unreleased expanded by default). Opens on the
pill, closes on ✕ / backdrop / Esc.

How it works:

- `tasks/deploy.yml` reads `CHANGELOG.md` (three levels up from `role_path`) into
  the fact `homepage_changelog_md` on the control node.
- `templates/custom.js.j2` embeds it via `{{ '{{' }} homepage_changelog_md | to_json {{ '}}' }}`
  (the string VALUE is rendered, so `{{ '{{' }} {{ '}}' }}`/markup inside the changelog is data,
  never re-templated) and a small in-file renderer turns `## [ver] — date`
  headings into accordion sections with `**bold**` / `` `code` `` / links.
- Styling is the `#cl-*` block in `templates/custom.css.j2` — same slate-glass
  palette as the card tint. All ids are `#cl-*`, so it never touches homepage's
  own DOM.
- Because the changelog is read at deploy time, the drawer shows exactly the
  changelog of the **deployed commit** — under the GitOps CD, the deploy tag's.

## Gotchas (homepage v1.13.x)

- **Card-opacity CSS must target `.service-card`.** The card element is
  `li.service > div.service-card` (Tailwind `dark:bg-white/5` ≈ transparent). An
  older selector (`li[data-testid*="service"] > div`) matched **nothing** after
  an upstream markup change, so the readability tint was silently dead — text was
  unreadable over the background photo. If readability regresses, re-check this
  selector against the live DOM.
- **`custom.js` loads via Next.js `afterInteractive`** (injected after
  hydration), so it can run several seconds after first paint on the Pi. The
  hidden default is therefore enforced by **CSS** (instant); JS only handles the
  reveal. Don't move the default-hide into JS — it would flash.
- **`showStats: false`** in `templates/docker.yaml.j2`: pulling live per-container
  CPU/mem for ~25 containers every refresh was a real Pi-CPU + render cost.
  Status dots still show; only the live stat polling is off.
- **Control API has no widget.** It binds `127.0.0.1` by design
  (`homelab_control`), so a LAN-IP `customapi` widget always timed out (red card).
  It's a link card; `hideErrors: true` keeps any future unreachable widget from
  dragging the whole board.
