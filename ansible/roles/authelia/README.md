# authelia

Self-hosted SSO for the homelab: an OIDC provider for Grafana, Portainer and
MinIO, plus the Traefik forward-auth gate in front of selected service UIs.

## What it deploys

A single pinned `authelia/authelia` container (`authelia`), running as
`1000:1000` with `cap_drop: [ALL]`. It publishes on **loopback only**, so the
role has no `ufw.yml`; all browser access goes through Traefik. It joins the
external `mlstack_net` bridge to reach Postgres by container name.

| Item            | Value                                                         |
|-----------------|---------------------------------------------------------------|
| Portal / authz  | `127.0.0.1:9091` → container `:9091` (`authelia_port`)         |
| Metrics         | `127.0.0.1:9959` (`authelia_metrics_port`)                     |
| Traefik (HTTPS) | `https://auth.example.internal`                               |
| Config          | `/home/pi/docker/core/authelia/config` (`authelia_config_dir`) |
| Storage backend | Postgres `authelia` DB at `postgresql:5432`                    |
| Memory limit    | `authelia_mem_limit` (192M)                                    |

The user identity is a file (`users_database.yml`, one admin user in group
`admins`, 1FA only, TOTP disabled). Postgres stores sessions and OIDC state;
it is not a user directory. Notifications go to `/config/notification.txt`
because there is no SMTP.

## Deploy

```bash
make postgresql   # creates mlstack_net + the authelia DB
make authelia
make traefik      # auth.<domain> router + forwardAuth middleware
```

The rendered config is checked with `authelia validate-config` before the
container restarts, so an invalid config fails the play instead of starting
a broken gate. That check is skipped under `--check`.

## Configuration

- `authelia_image`: pinned to the version whose `authelia crypto` produced the vault hashes.
- `authelia_domains`: cookie parents; derived from `homelab_internal_domains`. Only domains with a dot get a session cookie.
- `authelia_forward_auth_services`: UIs gated with a `one_factor` rule. It **must** match every `auth: true` entry in `traefik_services` (`roles/traefik/defaults/main.yml`). A missing entry falls through to `default_policy: deny` and returns 403 to logged-in users. Guarded by `tests/test_authelia_traefik_sync.py`.
- `authelia_oidc_clients`: OIDC clients with `redirect_uris`. An empty list disables the OIDC block.
- Vault: `vault_authelia_db_password`, `vault_authelia_jwt_secret`, `vault_authelia_session_secret`, `vault_authelia_storage_encryption_key`, `vault_authelia_oidc_hmac_secret`, `vault_authelia_oidc_issuer_private_key`, `vault_authelia_admin_password_hash` and one `vault_authelia_<client>_client_secret_hash` per client. The client apps hold the plaintext secret.

## Operations

- **Load-bearing:** if Authelia is down, every forward-auth'd service fails at
  the gate. vmagent job `authelia` scrapes `127.0.0.1:9959`, and the
  `AutheliaDown` alert (`roles/vmalert/templates/rules/authelia.yml.j2`)
  watches it.
- Server timeouts are raised to 30s/30s/60s because the portal produced 408s on
  a disk-saturated Pi. Guarded by `tests/test_authelia_config.py`.
- Traefik is a convenience router, not an auth boundary. Gated services still
  have LAN-open host ports. See
  [reverse-proxy-not-an-auth-boundary](../../../docs/conventions/reverse-proxy-not-an-auth-boundary.md)
  and [SSO options research](../../../docs/research/036-sso-identity-provider-options.md).
