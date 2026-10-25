# Ansible Standards

Conventions for this homelab Ansible codebase. Follow these when adding new roles or modifying existing ones.

## Role Structure

```text
ansible/roles/<name>/
  defaults/main.yml      # Configurable variables (ports, paths, limits)
  tasks/main.yml         # Entrypoint — includes deploy.yml, ufw.yml, etc.
  tasks/deploy.yml       # Template compose, create dirs, start containers
  tasks/ufw.yml          # Firewall rules for the service's ports
  templates/             # docker-compose.yml.j2 and config templates
  handlers/main.yml      # Restart handlers
```

## Variable Naming

- Variables inside a role MUST be prefixed with the role name: `media_puid`, `adguard_mem_limit`
- Registered variables follow the same rule: `common_wifi_connection`
- Global variables (in `group_vars/all/`) have no prefix: `timezone`, `lan_subnet`, `docker_user`
- Lint enforces this via `var-naming[no-role-prefix]`

## Compose Template Standards

Every `docker-compose.yml.j2` MUST include:

```yaml
services:
  <name>:
    restart: unless-stopped
    mem_limit: {{ role_mem_limit }}
    security_opt:
      - no-new-privileges:true
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

Critical services (adguard, traefik, portainer, jellyfin, beszel) also need a `healthcheck:` block.

## Tags

Every `include_tasks` in `tasks/main.yml` must have tags:

```yaml
- name: Include deploy tasks
  ansible.builtin.include_tasks: deploy.yml
  tags: [role_name]

- name: Include firewall tasks
  ansible.builtin.include_tasks: ufw.yml
  tags: [role_name, firewall]
```

## UFW Rules

Use the global `lan_subnet` variable, never hardcode CIDRs:

```yaml
from_ip: "{{ lan_subnet }}"
```

## Secrets

- All secrets in `inventory/group_vars/all/vault.yml` (Ansible Vault encrypted)
- Vault variables prefixed with `vault_`: `vault_qbittorrent_password`
- Never commit unencrypted secrets

## Testing

```bash
make lint       # ansible-lint (offline mode)
make test       # pytest (control API template tests)
make validate   # lint + syntax-check + test
```

## Commits

Conventional commit format enforced by pre-commit hook:

```text
feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert: subject
```
