# ntfy

Self-hosted push notifications. Host scripts, Beszel, Alertmanager (via the
ntfy-bridge) and pipeline jobs deliver every homelab alert to the operator's
phone through it.

## What it deploys

A single `binwiederhier/ntfy:latest` container (`ntfy`, `command: serve`) with
`cap_drop: [ALL]` plus `NET_BIND_SERVICE` (it binds :80 inside), a read-only
root filesystem and a tmpfs `/tmp`. It joins its own `ntfy_default` network and
the external `media_media_net` bridge, so containers on that network reach it
at `http://ntfy:80`.

| Item            | Value                                                          |
|-----------------|----------------------------------------------------------------|
| HTTP            | host `:2586` → container `:80` (UFW: `192.168.0.0/16`)         |
| Public URL      | `https://ntfy.example.internal` (Traefik, `ntfy_public_url`)   |
| Data / cache    | `/home/pi/docker/core/ntfy/data` (`cache.db`, root-owned)    |
| Default topic   | `homelab` (`ntfy_topic`)                                       |
| Memory limit    | `ntfy_mem_limit` (128M)                                        |

## Deploy

```bash
make media        # creates the external media_media_net network
make ntfy
make verify-ntfy  # host DNS, /v1/health, container DNS
```

## Configuration

`ntfy_port`, `ntfy_topic`, `ntfy_hostname`, `ntfy_public_url`,
`ntfy_external_*` and `ntfy_shoutrrr_url` live in
`inventory/group_vars/all/main.yml` so that every sender uses the same values.

- `ntfy_public_url` → `NTFY_BASE_URL`: HTTPS links in action buttons, the web app and the phone subscription.
- `ntfy_external_base_url` / `ntfy_external_topic_url`: internal HTTP `:2586` path for senders. It is deliberately separate from the public URL.
- `ntfy_shoutrrr_url`: the same target in shoutrrr syntax (Beszel, Watchtower).
- `NTFY_BEHIND_PROXY=true` and `NTFY_ENABLE_METRICS=true` are set in the compose template.

Never build ntfy URLs from the host IP. Use these vars.

## Operations

- Healthcheck: `/v1/health` must report `"healthy": true`.
- vmagent job `ntfy` scrapes `127.0.0.1:2586/metrics`. The alerts `NtfyDown`
  and `NtfyNoSubscribers` (no subscriber for 6h) are defined in
  `roles/vmalert/templates/rules/observability-services.yml.j2`, alongside the
  ntfy-bridge alerts.
- A healthy server with zero subscribers delivers nothing:
  [subscribers=0 incident](../../../docs/incidents/2026-06-06-ntfy-silent-delivery-subscribers-zero.md).
- Phone setup: [ntfy phone setup runbook](../../../docs/runbooks/001-ntfy-phone-setup.md).
