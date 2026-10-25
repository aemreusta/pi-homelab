# label_studio

Label Studio Community Edition (Apache-2.0) — the data-labeling UI for the ML
stack (PRD-004). Built for **audio / speech-to-text dataset QA**: correcting
machine-generated transcripts, labeling segments, and checking existing labels.

## What it deploys

A single `heartexlabs/label-studio` container (pinned release), **all-in-one
SQLite** (not the Postgres+nginx+Redis production stack — the Pi has no RAM for
it). Source audio and annotation exports live in **MinIO** (S3), never uploaded
into the app, so the server footprint stays ~0.6–0.9 GB.

| Item            | Value                                              |
|-----------------|----------------------------------------------------|
| Web UI          | host `:8085` → container `:8080`                    |
| Data            | `/srv/data/label-studio` (SQLite DB, config, exports; UID/GID 1001) |
| Traefik (HTTPS) | `https://label-studio.example` (UI)                |

Host port 8085 is used because the container's 8080 clashes with AdGuard.

## Access

- **Browser:** `https://label-studio.example` (trusted internal-CA cert on LAN +
  Tailscale).
- **API / direct:** `http://192.0.2.100:8085` (LAN host port) — used by the
  Faz-2 STT prediction-import script.
- **Credentials:** `vault_label_studio_username` / `vault_label_studio_password`
  (bootstrap admin, created on first boot). `vault_label_studio_user_token` is a
  fixed API token for the import script. The deploy fails fast if any are unset.

## Deploy

```bash
make label-studio   # role: container + UFW
make traefik        # publishes label-studio.example HTTPS router + cert SAN
```

## Security

- **UFW LAN-only:** the host port 8085 is allowed only from `lan_subnet`;
  everything external comes through Traefik (which terminates on the host). The
  Pi's `DOCKER-USER` chain additionally scopes Docker-published ports to
  LAN + Tailscale, but the UFW rule is the canonical access control here.
- Open registration is disabled (`LABEL_STUDIO_DISABLE_SIGNUP_WITHOUT_LINK`);
  telemetry is off (`COLLECT_ANALYTICS=false`).
- `no-new-privileges`, `cap_drop: [ALL]`, `mem_limit`, `pids_limit` per repo
  convention.

## Notes

- **Runs as UID 1001**, the image's baked-in runtime user (we do *not* override
  `user:` — the entrypoint writes to HOME `/label-studio`). The host data dir is
  pre-owned `1001:1001` to match — this is an app-private dir, so it does **not**
  follow the host-1000 `/srv` convention.
- **`CSRF_TRUSTED_ORIGINS` is multi-valued** (LAN HTTPS + Tailscale HTTPS + bare
  LAN host port) so the login POST isn't rejected behind the proxy. Editing the
  hostname/port means editing `label_studio_csrf_trusted_origins` in defaults.
- Image is **pinned** (not `:latest`) — bump deliberately.
- The data dir is included in `make backup` (project DB + annotations are
  irreplaceable QA work).
- **Pi RAM is tight** (containment `mem_limit`, not a reservation). If swap/load
  spike sustainedly after deploy, lower the cap or reconsider hosting — see
  design 035.
