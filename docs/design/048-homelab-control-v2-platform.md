# 048 — homelab-control v2: Go backend, and how much frontend is warranted

**Status:** Go backend accepted by the operator on 2026-09-27. Owned runtime
migration has started; see [the migration ledger](../operations/go-migration.md).
Frontend choices do not block the runtime/collector work. The full control API
and network/firewall UI remain later phases.
**Date:** 2026-08-03
**Supersedes (eventually):** the single-file Python service in `roles/homelab_control`
**Related:** [design 049](049-cloudflare-tunnel-mlflow-minio.md) (separate proposal, same session —
public ingress for MLflow/MinIO, not part of this scope)

Operator input: *"homelab control cok basit bir yapi su anda. bunu react vite bun htmx ile
uygun sekilde planlayabiliriz. arkasina da guzel bir go backend yapisi olabilir. bu aralar
go cok yaziyorum."*

---

## What is actually wrong with v1

Being fair to it first: the current service is ~1700 lines of **stdlib-only Python**, has no
dependencies, starts instantly, and has survived a year. That is not nothing, and "it's
simple" is not by itself a defect.

The real problems are specific:

1. **One file holds routing, business logic, HTML and CSS.** `PANEL_HTML` is a string
   constant inside the same file as the shutdown handler.
2. **Hand-rolled routing** — an `elif` chain on `parsed.path`. Adding more endpoints
   makes it longer, not better.
3. **Handlers are largely untested.** 58 tests exist, but they concentrate on the dangerous
   helpers (symlink protection, junk scan, `_dir_size`). The HTTP layer is mostly uncovered
   — a `/storage` bug shipped through it and was found by reading, not by a test.
4. **Blocking request handling.** `ThreadingHTTPServer` with `_dir_size` walking 1.3 T
   behind a 60 s TTL cache; a cold cache blocks a request for minutes.
5. **No schema.** Every endpoint invents its response shape, so a frontend cannot be
   generated or type-checked against it.

## Backend: Go — yes, and for concrete reasons

Not merely because the operator is writing Go this month, though that matters for
maintenance:

| | Python v1 | Go v2 |
|---|---|---|
| Deploy artefact | interpreter + source + system Python | one static ARM64 binary |
| RSS | ~30–40 MB | ~10–15 MB typical |
| Dependencies | system Python (breaks on a Debian major upgrade) | none |
| Concurrency | GIL + threads for a `du` over 1.3 T | goroutines, trivially |
| Cross-compile | n/a | `GOOS=linux GOARCH=arm64` from the Mac |

**The memory point is not academic on this box.** It runs 41 containers on 8 GB with
**2.3 GB already in swap**; the container mem-limit ceilings deliberately overcommit. A
20 MB saving is small, but the *predictability* of a static binary matters more — there is
no runtime that can be broken by an OS upgrade on a machine whose recovery story is
"drive to it".

Ship it into the existing `zot` registry as a container, or as a bare binary + systemd unit
like today. **Bare binary is simpler** and matches the current unit; the control plane
should not depend on Docker being healthy, since one of its jobs is restarting Docker.

## Frontend: this is where I would push back

React + Vite + Bun is a real toolchain — a `node_modules`, a build step, a bundle, and a
client-side router — for a page that is, in substance:

- a handful of status cards refreshed on a timer,
- some tables,
- and about six buttons, two of which are destructive.

**Recommendation: Go + [templ](https://templ.guide) + htmx.** Server-rendered, type-checked
templates that compile to Go code, with htmx doing partial refreshes. Concretely that means
`hx-get="/storage" hx-trigger="every 10s"` replacing a card — no client state, no
bundle, no hydration, and the panel keeps working with JS half-loaded over Tailscale on a
phone. templ gives compile-time errors for template bugs, which is strictly better than
both the current Python f-strings and JSX.

**When React would be the right call** — and it is worth naming honestly: if a panel table
needs client-side sort/filter/facet over thousands of rows with no round trip. That is a genuine SPA use case. But it argues for
**one React island mounted into a templ page**, not a React shell around the whole panel.
Vite builds it on the Mac, Ansible ships the static bundle; Bun is fine as the package
manager/runner but nothing on the Pi needs it.

**What I would not do:** rewrite the panel as an SPA and keep the Python backend, or adopt
all four technologies at once. The stack should follow the requirement, and today's
requirement is "show numbers, press a button, don't lie about disk usage".

## Migration: strangler, never big-bang

This service can power off the machine and is deployed by the CD. A flag-day rewrite of a
1700-line control plane on a box whose alerting lives on the same disk is exactly the risk
profile the CD's `never_auto` deny-list exists for.

1. **Contract first.** Write an OpenAPI spec for the *existing* endpoints, and add tests
   asserting v1 conforms. This is valuable even if v2 never ships — it is the missing
   schema, and it makes the rewrite verifiable rather than hopeful.
2. **New endpoints in Go only.** They land in the Go service on a different port.
   Nothing existing moves.
3. **Go service proxies unknown paths to Python.** Both run; the Go binary fronts them.
   Traefik keeps pointing at one address.
4. **Migrate endpoint by endpoint, each with the conformance test green**, destructive ones
   (`/shutdown`, `/restart`, `/update`) **last** and each with its own deploy.
5. Retire the Python service only when its route table is empty.

Add `homelab-control` to `homelab_autodeploy_never_auto_targets` for the duration, the same
way `base` is protected: a successful-but-bricking control-plane deploy will not roll back,
because rollback fires on ansible failure and this would exit 0.

## Scope extension (2026-08-03): network visibility + Pi-local firewall management

Operator input: *"Go ile internal management sistemi yazmamiz ve butun evin internetini
track etmemiz ve belki bir firewall kurmamiz gerek"* — then, when offered the trade-offs:
this extends v2 rather than being a separate service; traffic visibility level undecided
(discuss trade-offs before picking); firewall scope is **Pi-local only**, not a
whole-home gateway.

**Why Pi-local, not whole-home gateway.** The Pi is a normal LAN client today — no second
NIC, no port-mirror/NetFlow capability recorded for the current router, no VLAN. Making it
(or anything) the house's gateway/firewall means it sits inline between the modem and every
other device: if it's down or misconfigured, **the house loses internet**, which is a
categorically bigger blast radius than anything else this repo manages (compare: `base` is
already the most protected deploy target for a *single-box* mistake — a gateway mistake is
worse). The operator picked the low-risk option: manage/expose what the Pi already enforces
(UFW + the `DOCKER-USER` iptables chain), not take over routing.

**Why DNS/domain-level visibility is the sane starting point, not a compromise.** AdGuard
Home (already deployed, already the LAN's DNS server) exposes exactly this for free:

- `GET /control/querylog` — per-query log: `client` (source IP), `question.host`,
  `time`, `reason`/`rule` (e.g. `FilteredBlackList`), `status`. This is "which device
  asked for which domain, and was it blocked" at zero marginal infrastructure cost —
  confirmed against AdGuard Home's own API doc (`AGHTechDoc.md`) via Context7.
- `GET /control/stats` — aggregate counters (top clients, top domains, blocked count)
  over AdGuard's configured retention window.

This does **not** give bytes-transferred or non-DNS traffic (hardcoded-IP apps, DNS through
a VPN/DoH client that isn't AdGuard). Real per-device bandwidth needs either (a) the
current router's own per-client stats if it exposes any (SNMP, vendor API — **unchecked,
worth a five-minute look before building anything, since it could be free**), or (b) a
**separate, dedicated** passive inline tap between modem and router (its own small box,
transparent L2 bridge, fails open if unplugged) — deliberately **not** ARP-spoofing from
the Pi, which is fragile and trips security software on other devices. (b) is a distinct,
separately-scoped hardware decision if it's ever pursued; it is not part of this v2 platform
work.

**What this adds to the Go v2 service (same strangler-migration shape as above — new
endpoints only, nothing existing moves):**

- A network page: recent/aggregate AdGuard querylog+stats, polled server-side (the Go
  service holds no DNS logic itself, it's a thin client of AdGuard's existing API) and
  rendered the same htmx-partial way as the rest of the panel.
- A firewall page: **read-only first** — render the live `ufw status numbered` +
  `iptables -L DOCKER-USER` output so the operator can see current rule state in one
  place instead of SSH+two commands. Whether this later grows edit/apply capability (and
  what guardrails that needs — this is a rule-change surface, closer to `base` than to a
  status card) is an open question, not decided here.
- Neither of these needs `homelab-control` to run with elevated network privileges beyond
  what it already needs for `docker`/`shutdown`/`restart` — AdGuard is a normal HTTP API
  call, and `ufw status`/`iptables -L` are read-only.

## Open questions for the operator

1. **React island, or htmx only?** Depends on whether you want to browse large tables
   interactively or just read summaries. I would start htmx-only and add the
   island when a table actually gets painful.
2. **Binary or container?** Recommendation: binary + systemd, so the control plane does not
   depend on the thing it manages.
3. **Is this worth doing before the storage work lands?** My honest read: **no.** The
   `/storage` bug is fixed and the pending storage work is a bigger operator win. v2 is a
   maintainability investment, not a capability unlock — it should be scheduled
   deliberately, not squeezed in beside a multi-day storage job.
