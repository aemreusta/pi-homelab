# 036 — SSO / Identity-Provider options for the homelab (evaluation + safe plan)

**Research date:** 2026-06-07
**Status:** **Evaluation only — NOT scheduled. Do not deploy as the original report describes.**
**Trigger:** an `/sc:analyze` SSO research report recommending Authelia forward-auth across
~20 services. This doc is the corrected version after verifying every load-bearing claim against
the live Pi and the actual Traefik topology.

> **One-line verdict:** the tool choice (Authelia) is sound, but Traefik forward-auth **gates
> nothing in this topology** because every service also publishes a LAN-open host port. Making
> SSO actually enforce requires closing those ports + rewiring routing — which is the single
> change most likely to brick the box. Sequence the work so the zero-risk wins land first.

---

## 1. The original report's numbers vs. verified live state

The source report's capacity analysis was inflated/outdated. Verified 2026-06-07:

| Report claim | Verified reality (live) | Verdict |
|---|---|---|
| "already using **14 GB swap**" | **2.2 GiB** of a 6 GiB zram device in use; 3.9 GiB free (`free -m`) | ✗ Wrong (cites retired 12 GB layout) |
| "**91%** disk" | **89%**, 206 GB free on `/` | ≈ close |
| "**23 secrets** in vault.yml" | **35 keys** (`ansible-vault view`) | ✗ Wrong |
| "**32 services** behind Traefik" | **29** entries in `traefik_services`; 7 are VM/VL/vmagent/vmalert UIs the report itself says need no login | ✗ Inflated |
| "3–4 services have native OIDC (Portainer, Grafana, MinIO, Label Studio)" | Correct | ✓ |
| Authelia ~60–100 MB / Authentik ~1.5–2 GB | Directionally right | ✓ |

**The resource conclusion still holds:** RAM is 7819 MiB total / **3316 MiB available**, load avg
**6.4** on 4 cores (media-workload baseline). Authentik's ~2 GB is genuinely
reckless here; Authelia's ~60–100 MB is fine. But the specific figures in the source report are
sloppy — re-measure against `free -m` / `df -h` / cAdvisor before quoting any of them in a sprint.

## 2. The architectural blind spot the report missed (the load-bearing finding)

The report's entire value proposition — *"Forward-auth via Traefik (~20 services) → one login
protects all"* — **is false in this topology.** Proven on the live box 2026-06-07:

```text
curl http://192.0.2.100:8989/      → HTTP 302   (Sonarr answers directly — no Traefik, no auth)
curl -H Host:sonarr.example :80       → HTTP 301   (the Traefik path)
```

Why: this Traefik is a **file-provider** setup (not Docker-label-based). It runs `network_mode:
host` and routes every service as `http://127.0.0.1:<port>`. Each service **publishes its own host
port**, and UFW explicitly opens them to `Anywhere`:

```text
8989/tcp  ALLOW IN  Anywhere   # Sonarr
7878/tcp  ALLOW IN  Anywhere   # Radarr
8181/tcp  ALLOW IN  Anywhere   # qBittorrent
8096/tcp  ALLOW IN  Anywhere   # Jellyfin
9000/9443 ALLOW IN  Anywhere   # Portainer
8082/tcp  ALLOW IN  Anywhere   # FileBrowser
6246/tcp  ALLOW IN  Anywhere   # Maintainerr
... (most services)
```

So putting Authelia in front of `sonarr.example` does **nothing** — any LAN/Tailscale client just
hits `192.0.2.100:8989` and walks past it. **Forward-auth here is security theater until the
host ports are closed.** This is recorded as a standing constraint in
[conventions/reverse-proxy-not-an-auth-boundary.md](../conventions/reverse-proxy-not-an-auth-boundary.md)
— any future auth/exposure/SSO sprint must start from that fact.

To make reverse-proxy-layer auth actually enforce, you must **all three**:

1. Delete every `ports:` host binding from ~29 compose templates,
2. Remove every matching UFW `ALLOW IN` rule,
3. Rewire Traefik from `127.0.0.1:port` to reach services over a **shared Docker network by
   container name** (Traefik is currently host-networked; services would need to join a network).

That rewire touches the core of the stack. The original report hand-waved it as "step 6: roll out
to all services."

## 3. Brick vectors — what "not bricking" actually requires

- **Forward-auth lockout, all-at-once.** If the auth subrequest ever 5xxs (Authelia OOM, Redis
  hiccup, one config typo) **all gated vhosts go down simultaneously**, including over Tailscale —
  remote lockout with no console. This is the *same class* we just lived through with the
  Alertmanager folded-matcher 3 h crash-loop
  ([incident 2026-06-07](../incidents/2026-06-07-alertmanager-crash-loop-folded-matcher.md)): a
  config valid to YAML but fatal to the consumer. Any IdP/forward-auth config needs the same
  pre-deploy validation gate the alertmanager role now has (`amtool`-style) — **render + validate
  with the consuming binary before restart.**
- **Self-lockout of Traefik dashboard / the Authelia portal** if the middleware is applied too
  broadly. Always exclude the auth server and the dashboard from auth; keep a break-glass path
  (SSH tunnel / Tailscale-direct to a port) documented *before* the first gate goes live.
- **Forward-auth breaks every programmatic client.** Prowlarr→Sonarr/Radarr,
  Jellyseerr→Jellyfin, Homepage widget calls, qBit's API, ntfy
  *publish*, the alert→ntfy-bridge path — all use API keys/tokens, **not browser sessions**. The
  report's "~20 services behind forward-auth" list includes Sonarr/Radarr/Prowlarr/qBittorrent/
  ntfy — the exact services that must stay machine-reachable. Interactive login in front of
  an API endpoint breaks the automation that runs this homelab.
- **Cookie / redirect plumbing under the internal CA.** Session cookies need a parent domain
  (`.example`); OIDC redirect URIs must be HTTPS under the internal CA
  ([design/033](../design/033-internal-ca-https.md)); every client/phone/the Mac must trust that CA
  or OIDC handshakes fail. The source report never mentions any of this.

## 4. Tool choice (unchanged from the report, and correct)

For *this* Pi: **Authelia** if/when an IdP is wanted — single Go binary (~60–100 MB), YAML config
fits the Ansible-as-code model, SQLite backup drops into the existing backup role, first-class
Traefik forward-auth. **Authentik** only if a user-management web UI / SAML / LDAP is ever needed
(cost: ~2 GB RAM + 4 containers — too heavy here). **Keycloak: no** (JVM on ARM). The tool was
never the problem with the report; the deployment model was.

## 5. The safe path — ordered so the zero-risk wins land first

Execute in this order. Each phase is independently valuable and reversible; **do not skip ahead.**

**Phase 0 — UFW tightening (highest ROI, smallest risk, do this regardless of SSO).**
Most service ports are `ALLOW IN Anywhere` today while the apps behind them have weak/no auth.
Restrict them to `192.0.2.0/24` + Tailscale CIDR. This is a bigger, faster security win than SSO with a fraction of the brick risk and
**no topology change**. Reversible per-rule.

**Phase 1 — native OIDC on the 4 services that support it.**
Stand up one Authelia instance as an **OIDC provider only** (no forward-auth) and point Portainer,
Grafana, MinIO, Label Studio at it. **No host-port removal, no UFW change, no Traefik rewrite, no
automation breakage.** Retires 4 password entries, proves the IdP under real load, gives a measured
RAM/CPU number from the existing cAdvisor stack. The Authelia role ships with a render-time config
validation gate (per §3, mirroring the alertmanager `amtool` gate) and SQLite backup wired into the
backup role.

**Phase 2 — forward-auth on ONE human-only, no-API service (pilot).**
FileBrowser or Homepage only, and **only after closing that one service's host port + UFW rule**,
with Authelia and the Traefik dashboard explicitly excluded and a documented break-glass. Measure,
live with it a week before considering a second service.

**Never:** put forward-auth in front of the *arr / qBit / ntfy API surfaces. Those stay
key-authenticated. If you want them off the open LAN, the answer is **Phase 0 (close the port,
reach them via Traefik/Tailscale)**, not an interactive login.

## 6. What a sprint plan for this still needs (gaps in the source report)

The source report is a fine *research* doc but not a safe *plan*. Before any of Phase 1+ is
scheduled, the sprint must add: the Ansible role design (single container, OIDC-only first);
**rollback + break-glass** path; firewall reconciliation (Phase 0); explicit handling of the
API-client breakage; cookie-domain / OIDC-redirect design under the internal CA; backup-role
integration; the render-time **config-validation deploy gate**; and an incident pre-mortem. Treat
§3 as the acceptance checklist.

---

## Verification commands (re-run before quoting any number here)

```bash
# resources
ansible homelab -i inventory/hosts.ini -m shell -a "free -m; df -h /; uptime"
# the bypass proof — service answers directly, skipping Traefik+auth
curl -s -o /dev/null -w '%{http_code}\n' http://192.0.2.100:8989/
# the open-to-Anywhere ports
ansible homelab -i inventory/hosts.ini -m shell -a "sudo ufw status verbose"
```

## See also

- [conventions/reverse-proxy-not-an-auth-boundary.md](../conventions/reverse-proxy-not-an-auth-boundary.md) — the standing constraint distilled from §2
- [design/001-architecture.md](../design/001-architecture.md#infrastructure-and-network) — exposure model (services are host-port published)
- [guides/004-traefik.md](../guides/004-traefik.md) — routing model + the auth caveat
- [design/033-internal-ca-https.md](../design/033-internal-ca-https.md) — internal CA (OIDC redirect URIs depend on it)
- [incidents/2026-06-07-alertmanager-crash-loop-folded-matcher.md](../incidents/2026-06-07-alertmanager-crash-loop-folded-matcher.md) — the config-validation-gate precedent
