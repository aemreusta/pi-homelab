#!/usr/bin/env python3
"""tailscale_split_dns — detect drift in the tailnet's split-DNS routes.

Tailscale split-DNS routes are **tailnet control-plane state**: they live in the
admin console (or behind the ``/dns/split-dns`` API), not on any host, so no
Ansible run can converge them. That makes them the one part of an internal-domain
migration that can silently rot — and on 2026-07-15 it did. The
``.example`` -> ``.example.internal`` cut updated AdGuard, the Pi's dnsmasq,
Traefik, Authelia and every variable in the repo, while the split-DNS route kept
saying ``example``. MagicDNS therefore answered NXDOMAIN for every
``*.example.internal`` name for three weeks.

That is not a cosmetic gap. Every Docker container resolves through
``127.0.0.11 -> 100.100.100.100 (tailscaled)``, so the stale route took out the
Uptime Kuma Filebrowser monitor and dropped ntfy-bridge alerts. It read as
*intermittent* because Docker's embedded resolver caches hits, so a name worked
until its TTL lapsed.

This module is the tested decision layer for the drift cron. The shell side does
I/O only: run ``tailscale dns status --json``, pipe it here, emit the textfile
metric. Decision/diff math lives here per CLAUDE.md (set-diff logic in a plain
Python module + tests, never bash).

Detection, deliberately — not enforcement. Converging the route needs a Tailscale
API key with tailnet-wide DNS write scope; minting one so a cron can rewrite
network policy is a materially larger blast radius than the drift it prevents.
``desired_patch`` is emitted so an operator (or a future opt-in task) can apply
it verbatim against ``PATCH /api/v2/tailnet/-/dns/split-dns``, whose partial
semantics leave unrelated routes untouched.

CLI
---
::

    tailscale dns status --json \\
      | python3 tailscale_split_dns.py --domains example.internal --nameserver 100.64.0.10

stdout: JSON verdict (always, even on bad input — the caller emits a metric).
exit 0 = no drift, 1 = drift, 2 = could not read the input.

Run tests:  python3 -m pytest tests/test_tailscale_split_dns.py -v
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field


def _norm(domain: str) -> str:
    """Normalise a DNS name for comparison: lowercase, no trailing dot."""
    return domain.strip().rstrip(".").lower()


def covers(route: str, domain: str) -> bool:
    """True if a split-DNS route for ``route`` resolves ``domain``.

    A route covers its own name and everything **below** it, and nothing else.
    The boundary must be a whole label: ``ab.internal`` does not cover
    ``example.internal`` even though the string ends with it.

    The case this exists for: ``covers("example", "example.internal")`` is
    ``False``. ``example.internal`` is a sibling of ``example``, not a child —
    which is exactly why the pre-migration route silently stopped matching.
    """
    r, d = _norm(route), _norm(domain)
    if not r or not d:
        return False
    return d == r or d.endswith("." + r)


@dataclass
class Verdict:
    """Outcome of comparing live routes against the domains we require."""

    missing: list[str] = field(default_factory=list)
    """Required domains with no covering route at all."""

    misrouted: list[str] = field(default_factory=list)
    """Required domains that have a route, but not to our nameserver."""

    stale: list[str] = field(default_factory=list)
    """Routes pointing at OUR nameserver for a domain we no longer require."""

    desired_patch: dict[str, list[str] | None] = field(default_factory=dict)
    """Body for ``PATCH /dns/split-dns``: domain -> [ns], or None to clear."""

    @property
    def ok(self) -> bool:
        """True when every required domain resolves via our nameserver.

        ``stale`` is deliberately excluded: a leftover route to a dead domain
        breaks nothing, it is only untidy. Folding it in would make the alert
        fire for a condition with no user-visible symptom, and an alert that
        cries wolf about tidiness is how the real one gets ignored.
        """
        return not self.missing and not self.misrouted

    def to_dict(self, scrape_ok: int = 1) -> dict:
        return {
            "missing": self.missing,
            "misrouted": self.misrouted,
            "stale": self.stale,
            "desired_patch": self.desired_patch,
            "ok": 1 if self.ok else 0,
            "scrape_ok": scrape_ok,
        }


def decide(
    actual_routes: dict[str, list[str]],
    required_domains: list[str],
    nameserver: str,
) -> Verdict:
    """Diff live split-DNS routes against the domains we require.

    ``actual_routes`` maps route name -> nameserver addresses (from
    :func:`parse_status`). ``required_domains`` is ``homelab_internal_domains``,
    order-preserving because element 0 is the canonical domain. ``nameserver``
    is the Pi's tailnet DNS address.

    A required domain is satisfied when SOME route covers it AND that route
    lists ``nameserver``. Extra nameservers alongside ours are fine — a
    secondary resolver is not drift.
    """
    ns = _norm(nameserver)
    v = Verdict()

    for domain in required_domains:
        d = _norm(domain)
        if not d:
            continue  # blank entries in the var list are not a requirement
        covering = [r for r in actual_routes if covers(r, d)]
        if not covering:
            v.missing.append(domain.strip())
            v.desired_patch[domain.strip()] = [nameserver]
        elif not any(ns in {_norm(a) for a in actual_routes[r]} for r in covering):
            v.misrouted.append(domain.strip())
            v.desired_patch[domain.strip()] = [nameserver]

    # Stale = routed to US for something we no longer claim. Scoped to our own
    # nameserver on purpose: a route to a third party (e.g. ts.net.) is not
    # ours to judge, and clearing it would be an outage we caused.
    required_norm = [_norm(x) for x in required_domains if _norm(x)]
    for route, addrs in actual_routes.items():
        if ns not in {_norm(a) for a in addrs}:
            continue
        if not any(covers(route, d) or covers(d, route) for d in required_norm):
            v.stale.append(route)
            v.desired_patch[route] = None

    return v


def parse_status(raw: str) -> dict[str, list[str]]:
    """Extract ``{route: [addr, ...]}`` from ``tailscale dns status --json``.

    Raises ``ValueError`` on malformed JSON so the caller can emit
    ``scrape_ok=0`` rather than silently reporting "no routes" — which would
    look identical to a tailnet with split DNS switched off, and would fire a
    drift alert for a parse bug. An *absent* or null ``SplitDNSRoutes`` key is
    a real, legitimate state and reads as ``{}``.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"unparseable tailscale dns status: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("tailscale dns status did not yield an object")

    routes = data.get("SplitDNSRoutes") or {}
    if not isinstance(routes, dict):
        return {}

    out: dict[str, list[str]] = {}
    for route, entries in routes.items():
        addrs = []
        for e in entries or []:
            addr = e.get("Addr") if isinstance(e, dict) else e
            if addr:
                addrs.append(str(addr))
        out[str(route)] = addrs
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--domains",
        required=True,
        help="Comma-separated required domains (homelab_internal_domains).",
    )
    p.add_argument(
        "--nameserver",
        required=True,
        help="Tailnet IP that should serve those domains (tailscale_dns_ip).",
    )
    args = p.parse_args(argv)

    domains = [d for d in (args.domains or "").split(",") if d.strip()]

    raw = sys.stdin.read()
    try:
        actual = parse_status(raw)
    except ValueError:
        # Always emit a verdict object: the caller is a cron that must write a
        # metric on every path (silent-cron rule). scrape_ok=0 distinguishes
        # "could not look" from "looked and found nothing".
        print(json.dumps(Verdict().to_dict(scrape_ok=0), separators=(",", ":")))
        return 2

    verdict = decide(actual, domains, args.nameserver)
    print(json.dumps(verdict.to_dict(), separators=(",", ":")))
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    sys.exit(main())
