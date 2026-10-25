# 049 — Cloudflare Tunnel: public ingress for MLflow + MinIO console on aemreusta.com

**Status:** proposal — not implemented. Documented for later; do not deploy without
re-reading the "Manual steps" section first (they need to happen before any Ansible run).
**Date:** 2026-08-03
**Supersedes (scope-wise):** the Cloudflare Tunnel / Access portion of
[PRD-005](../archive/prds/005-external-access.md) and
[Sprint 4](../archive/sprints/004-external-access.md) — those also bundle a GitHub
Actions self-hosted runner and OpenClaw, which are **not** part of this doc and remain
unscoped/undecided.
**Related:** [docs/conventions/reverse-proxy-not-an-auth-boundary.md](../conventions/reverse-proxy-not-an-auth-boundary.md)

---

## Why

Operator owns `aemreusta.com` and wants to reach specific homelab tooling from outside
the LAN/Tailscale — explicitly **not** media services. Security is the primary
constraint, more than convenience.

## Scope decision (confirmed with operator, 2026-08-03)

- **In scope (phase 1):** MLflow (experiment tracking UI) and the MinIO web console
  (object storage admin UI).
- **Out of scope for now:** PostgreSQL (no raw-TCP tunnel route), Label Studio. A few
  small websites are planned later, once they exist — this design keeps the ingress
  list trivially extensible for that, but creates no website content now.
- **Auth:** Cloudflare Access (Zero Trust), login method = **Email OTP**. No IdP
  integration (Google/GitHub OAuth) needed for phase 1.
- **Hard guardrail, carried forward from the repo's standing rules:** never add
  `qbittorrent` or any other media-service hostname to the tunnel ingress list — media
  services stay off public ingress without exception.

## Why this doesn't repeat the "Traefik isn't an auth boundary" mistake

[docs/conventions/reverse-proxy-not-an-auth-boundary.md](../conventions/reverse-proxy-not-an-auth-boundary.md)
documents that Traefik-layer auth in this stack is decorative, because every service
also keeps its LAN host port open — a client can just skip the proxy and hit the port
directly. That failure mode requires an *inbound path that bypasses the gate*.

Cloudflare Tunnel has the opposite topology: `cloudflared` makes an **outbound-only**
connection to Cloudflare's edge. There is no listening port added on the Pi and no
router port-forward — the tunnel is the *only* route from the public internet in, and
Cloudflare Access enforces auth at Cloudflare's edge before any packet reaches the Pi.
LAN/Tailscale access to MLflow/MinIO is unaffected and stays exactly as open as it is
today (acceptable — LAN is the trusted zone in this repo's threat model). There is no
second, unguarded door here the way there is for Traefik-fronted services.

## Architecture

```text
Internet ── Cloudflare Access (Email OTP) ── Cloudflare edge
                                                   │ (outbound tunnel, mTLS, port 7844)
                                                   ▼
                                    cloudflared container (Pi, mlstack_net)
                                          │                        │
                                          ▼                        ▼
                              http://mlflow:5000        http://minio:9001 (console)
```

`cloudflared` joins the existing `mlstack_net` Docker bridge (created by the
`postgresql` role) so it reaches MLflow/MinIO by service name — no host-port hairpin,
no dependency on `minio_api_port`/`mlflow_port` host bindings.

## New role: `ansible/roles/cloudflare_tunnel/`

Single-container role (`cloudflare/cloudflared:latest`), modeled on the `minio` role's
structure — fail-loud guard on empty secrets, `docker_compose_v2`, **no `ufw.yml`**
(outbound-only container, nothing published; confirmed this is the correct pattern for
a no-firewall-rules role — `minio`/`mlflow` both have real `ufw.yml` files because they
publish host ports, and there's no existing "no-op ufw.yml" placeholder to imitate).

- `meta/main.yml` — `dependencies: [{ role: docker }]`.
- `defaults/main.yml`:
  - `cloudflare_tunnel_compose_dir: /home/{{ docker_user }}/docker/network/cloudflare`
  - `cloudflare_tunnel_id: "{{ vault_cloudflare_tunnel_id | default('') }}"`
  - `cloudflare_tunnel_credentials_json: "{{ vault_cloudflare_tunnel_credentials_json | default('') }}"`
  - `cloudflare_tunnel_image: "cloudflare/cloudflared:latest"`
  - `cloudflare_tunnel_mem_limit: 128M`, `pids_limit`, `cap_drop: [ALL]`, standard OCI
    labels block (same shape as `minio_oci_labels`).
  - `cloudflare_tunnel_ingress` — list of `{hostname, service}`:
    - `mlflow.{{ cloudflare_tunnel_domain }}` → `http://mlflow:5000`
    - `minio.{{ cloudflare_tunnel_domain }}` → `http://minio:9001` (console port —
      the raw S3 API port is not tunneled in phase 1, no reason to expose it)
    - A large comment restating the guardrail above, so it can't be missed when this
      list is next edited.
  - `cloudflare_tunnel_network: mlstack_net`.
- `tasks/main.yml` — thin dispatcher, `include_tasks: deploy.yml` only.
- `tasks/deploy.yml` (mirrors `minio/tasks/deploy.yml`):
  1. `assert` — fail loudly if `cloudflare_tunnel_id` / `cloudflare_tunnel_credentials_json`
     is empty (signals the manual Cloudflare setup below hasn't happened yet).
  2. Ensure compose dir exists.
  3. Write `credentials.json` (`no_log: true`, mode `0600`).
  4. Template `config.yml` (tunnel id, `credentials-file`, the ingress list, trailing
     `- service: http_status:404` catch-all).
  5. Template `docker-compose.yml` (`notify: Restart cloudflared`).
  6. `docker_compose_v2` up.
  7. Wait-for-ready via `ansible.builtin.uri` against `http://127.0.0.1:2000/ready`
     (`retries: 12`, `delay: 5`) — same pattern as MinIO's health-wait task. Requires
     adding `--metrics 0.0.0.0:2000` to the `tunnel run` command and using that
     endpoint as the container healthcheck too.
- `handlers/main.yml` — `Restart cloudflared`.
- `templates/docker-compose.yml.j2` — `command: tunnel --config /etc/cloudflared/config.yml run --metrics 0.0.0.0:2000`,
  mounts `config.yml` + `credentials.json` read-only, `networks: [mlstack_net]`
  (external: true), **no `ports:`**, `restart: unless-stopped`,
  `security_opt: no-new-privileges:true`, `cap_drop: ALL`.

Confirmed via Context7 (`/cloudflare/cloudflared`) that a locally-managed tunnel
(`config.yml` + `credentials-file`, as opposed to a token-only remotely-managed tunnel)
is the right shape for keeping ingress rules in git rather than only in the Cloudflare
dashboard — consistent with this repo's "prefer Ansible for all feasible changes" rule.
An optional further-hardening step (not phase 1): cloudflared's own `originRequest`
config supports `access: { required: true, teamName, audTag }`, which makes cloudflared
itself validate the Cloudflare Access JWT in addition to the edge doing so — worth
adding once the Access Application's AUD tag exists (chicken-and-egg with phase 1
setup, so deferred).

## Cross-cutting changes

- **`ansible/inventory/group_vars/all/main.yml`** — add `cloudflare_tunnel_domain:
  aemreusta.com` next to `homelab_internal_domains`, as the single source of truth for
  public tunnel hostnames (same pattern already used for `.example.internal`).
- **`ansible/roles/mlflow/defaults/main.yml`** — tighten `mlflow_allowed_hosts` from
  `"*"` to an explicit list including `mlflow.{{ cloudflare_tunnel_domain }}`, per the
  comment already sitting above that variable ("tighten to explicit host:port patterns
  if ever exposed"). MLflow 3.x's DNS-rebinding-protection middleware 403s any Host
  header not on the allowlist — without this, the tunnel would 403 every request.
- **`ansible/inventory/group_vars/all/vault.yml.example`** — add, in the existing
  `# --- ... ---` section-header style:

  ```yaml
  # --- Cloudflare Tunnel (public ingress for MLflow / MinIO console) ---
  # Tunnel UUID (from `cloudflared tunnel create`) and its credentials JSON
  # (from ~/.cloudflared/<tunnel-id>.json). Paste the JSON body as-is.
  vault_cloudflare_tunnel_id: "CHANGE_ME"
  vault_cloudflare_tunnel_credentials_json: |
    {"AccountTag":"CHANGE_ME","TunnelSecret":"CHANGE_ME","TunnelID":"CHANGE_ME"}
  ```

- **`Makefile`** — add `cloudflare-tunnel` to `.PHONY` and:

  ```makefile
  cloudflare-tunnel: ## Deploy Cloudflare Tunnel (public HTTPS ingress for MLflow / MinIO console)
  	$(call DEPLOY_GATE,playbooks/cloudflare-tunnel.yml)
  ```

- **`ansible/playbooks/cloudflare-tunnel.yml`** — minimal single-role playbook
  (`hosts: pi`, `become: true`, `roles: [cloudflare_tunnel]`), same shape as
  `ansible/playbooks/minio.yml`.
- **`.ansible-lint`** — add `cloudflare_tunnel` to `mock_roles`.
- **No entry needed in `homelab_autodeploy_role_target_map`.** `_role_to_target()`'s
  default underscore→hyphen convention (`cloudflare_tunnel` → `cloudflare-tunnel`)
  already matches the Makefile target and playbook filename above, and
  `tests/test_homelab_autodeploy.py::TestRoleTargetMappingIntegrity` asserts every
  deployable role resolves to an existing playbook — free CI coverage that the wiring
  is correct. This role carries none of the `base`/`homelab-autodeploy` risk classes,
  so it should **not** be added to `homelab_autodeploy_never_auto_targets` — once
  phase-1 manual setup is done, future pushes auto-deploy it like any other service
  role.

## Manual steps (operator-only — need an interactive Cloudflare login; cannot be scripted)

These must happen before `make cloudflare-tunnel` can succeed — the role's `assert`
task fails loudly and explains why until they're done:

1. Confirm `aemreusta.com`'s nameservers point at Cloudflare (needed for Access + DNS
   routing).
2. On the Mac: `cloudflared tunnel login` (browser OAuth → `cert.pem`), then
   `cloudflared tunnel create homelab` → tunnel ID + `<tunnel-id>.json` credentials.
3. `cloudflared tunnel route dns homelab mlflow.aemreusta.com` and the same for
   `minio.aemreusta.com` — creates the CNAME records.
4. Put the tunnel ID into `vault_cloudflare_tunnel_id` and the full credentials JSON
   into `vault_cloudflare_tunnel_credentials_json` in the real (encrypted)
   `ansible/inventory/group_vars/all/vault.yml`.
5. Zero Trust dashboard → Settings → Authentication: confirm **One-time PIN** login
   method is enabled.
6. Zero Trust → Access → Applications: one **Self-hosted** application per hostname
   (`mlflow.aemreusta.com`, `minio.aemreusta.com`), each with an **Allow** policy
   scoped to the specific email address(es) that should have access, login method =
   One-time PIN, a sane session duration (e.g. 24h).

## Verification (once implemented)

- `make lint`, `ansible-playbook ansible/playbooks/cloudflare-tunnel.yml --syntax-check`.
- No new Python diff logic → no new pytest file needed (`make test` unaffected).
- After manual steps + vault secrets: `make cloudflare-tunnel` → `docker logs
  cloudflared` shows a registered connection; `docker exec cloudflared wget -qO-
  http://127.0.0.1:2000/ready`.
- From an external network (not LAN/Tailscale): `https://mlflow.aemreusta.com` and
  `https://minio.aemreusta.com` should show a Cloudflare Access Email OTP page, not the
  app directly. Confirm an allowed email gets through and a random email is rejected.
- Confirm LAN access to MLflow/MinIO (`http://192.0.2.100:5000`, `:9011`) is
  unaffected — no Access wall, exactly as today.
