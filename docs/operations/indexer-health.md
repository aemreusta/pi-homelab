# Prowlarr and Byparr functional observation

The media role installs `/usr/local/bin/homelab-indexer-health.sh` and root cron
`Indexer functional health` at minutes 9, 19, 29, 39, 49 and 59. The wrapper calls
`homelab-agent indexer-health`; it contains no decision logic or API key. CD must
deploy `homelab-runtime` before media. Installation probes `indexer-health --probe`.

The observer reads Prowlarr's local `config.xml` without logging its key. It reads
`/api/v1/health` and `/api/v1/indexerstatus` independently, counting warning/error
proxy and indexer health records and unique indexers with a future `disabledTill`.
Nullable or expired timestamps are not active backoff. Invalid responses publish
failed-read flags rather than an apparent zero-problem success.

It then posts one `request.get` to Byparr `/v1`, targeting `https://example.com/`
with `blockMedia: true`. Success requires an `ok` envelope, a 2xx solution status
and nonempty page content. A successful canary establishes browser execution and
reachability of that public target. Protected searches, Cloudflare challenges,
provider uptime and torrent binary downloads require separate native acceptance.
Kuma's existing `/docs` availability monitor is unchanged.

Each Prowlarr request is bounded to five seconds; the browser HTTP request is
bounded to 35 seconds by default, with Byparr `maxTimeout` capped at 30 seconds.
The configurable HTTP timeout is limited to 1–60 seconds. Redirects are disabled
so the Prowlarr key cannot be forwarded to another origin. Each response is at
most 1 MiB, and GNU `timeout 90` bounds the wrapper. The ten-minute schedule
prevents a monitoring retry loop against protected providers.

Publication uses atomic replacement: private root-owned 0600
`/var/lib/homelab/indexer-health/status.json`, and numeric 0644
`/var/lib/node_exporter/textfile/indexer_health.prom`. Raw pages, application
messages, cookies and keys are never persisted. The shared textfile directory
remains owned by `docker_user`, so other user-run collectors retain atomic-write
permission; root owns only this collector's state and emitted metric file.
A completed unhealthy observation returns success while its flags are zero.
A configuration/publication failure
returns nonzero; absence/staleness alerts cover a collector that cannot publish.

Five vmalert rules cover collector absence/staleness, failed authenticated reads,
proxy health, browser failure and indexer health/backoff. Browser/proxy conditions
must persist 15 minutes and use existing `mac_dependent` inhibition while the Mac
is absent or settling. Local API failures retain their own alert. Indexer failure
or active backoff must persist 30 minutes; short native cooldowns do not page.
The observer never edits settings, resets backoff, searches, grabs or deletes queues.

For rollback, set `media_indexer_health_enabled: false` in shared inventory group
vars (visible to both playbooks), and deploy media and vmalert through the usual
release gate. This removes cron and published metrics
and renders no observer rules. Disable the consumer before selecting an older
retained runtime artifact that lacks the subcommand; retain all other consumers'
binary contracts. This is new owned logic, with no Python predecessor to retire.
The original `.2` media role has the documented shared-owner defect; retain the
`.3` ownership correction or disable the observer before using that older role.

Validation covers real HTTP fixtures, malformed/null/oversized replies, future/
expired/nullable backoff, redirect credential isolation, deadline cancellation,
private publication, shell quoting and alert inhibition/disabled scope.
