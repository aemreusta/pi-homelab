# `community.docker.docker_container` vs `docker stop/start` for Compose-Managed Containers

Status: open — bite recovered 2026-05-22 during Sprint 019 Phase 1.1b apply.
One audited regression site (`ansible/roles/media/tasks/deploy.yml`) is
tracked as a follow-up; no other call sites in the repo.

## TL;DR

When the container is owned by a `docker-compose.yml` (deployed via
`community.docker.docker_compose_v2`), **do not** use
`community.docker.docker_container: state=stopped/started` to toggle its
runtime state inside an Ansible task list. Use plain `docker stop` /
`docker start` via `ansible.builtin.command` instead.

The failure mode is that `docker_container` can recreate the container
between `state=stopped` and `state=started`, and when it does it builds
the new container from the params passed to the module — *not* from the
existing compose config. With only `name` and `state` in the call, the
recreated container inherits Docker daemon defaults: anonymous volumes
in place of bind mounts, no `Memory` / `MemorySwap` cgroup limits, no
network attachments beyond the default bridge. The compose project
labels remain stale on the old container, the new one has none.

## Concrete incident

A library-prune task in a media role (original, pre-fix) used
`community.docker.docker_container: state=stopped` before a SQLite
DELETE+VACUUM transaction on the app's database, and `state=started`
after. Live effects observed:

| Property | Pre-apply (compose-managed) | Post-apply (after `docker_container` cycle) |
|---|---|---|
| Container ID | `b5b71ffbb4b1` | `864f5ef399cf` *(different — recreated)* |
| `HostConfig.Memory` | `3221225472` (3 GiB) | `0` (no cgroup limit) |
| `Mounts[].Source` (config) | `/home/pi/docker/<stack>/config/<app>` (bind) | `/srv/docker/volumes/798a3…/_data` (anonymous volume) |
| Compose labels | `project=<stack> service=<app>` | none |
| App DB view | full library | empty (fresh DB in anon volume) |
| `/ping` from host | 200 / 9 ms | HTTP 000 (connection refused) |

The drained DB on disk was intact (`sqlite3` against the bind-mounted
file showed the expected rows). The app was just looking at the wrong
file because the bind mount was gone.

Recovery: `docker rm -f <container> && make <role>`. The `make <role>`
call re-renders the compose template and recreates the container
correctly via `docker_compose_v2`.

## Fix pattern

Replace this:

```yaml
- name: Stop the app before DB edit
  community.docker.docker_container:
    name: <container>
    state: stopped

# ... do work on host-mounted file ...

- name: Start the app after DB edit
  community.docker.docker_container:
    name: <container>
    state: started
```

with this:

```yaml
- name: Stop the app (docker stop preserves compose config)
  ansible.builtin.command: docker stop <container>
  register: result_stop
  changed_when: result_stop.rc == 0

# ... do work on host-mounted file ...

- name: Start the app
  ansible.builtin.command: docker start <container>
  register: result_start
  changed_when: result_start.rc == 0
```

`restart: unless-stopped` (the policy every service in this repo uses)
honors explicit `docker stop`, so the container will NOT auto-restart
between the two calls. Bind mounts, `mem_limit`, networks, and the full
set of compose-rendered fields are preserved because the container
object itself is never destroyed — only paused and resumed.

## Why this is subtle

`community.docker.docker_container` is documented as idempotent and
"checks the current state before acting". In practice the module's
default reconciliation can decide to recreate when it cannot reconcile
the running container's config against what the module has been given.
When you pass only `name` and `state` the module's view of the desired
config is essentially empty, so any drift between the running container
and the empty-spec triggers a "recreate to match desired" path on
Docker daemon API versions where the module isn't conservative enough.

This is fine when you own the container outright (the `community.docker.docker_container` module created it, and the same module owns its full spec). It is **not** fine when `docker_compose_v2` owns the container — the compose-managed config is invisible to the `docker_container` module.

## Audit results (2026-05-22)

`rg -nB1 -A3 "community.docker.docker_container" ansible/` returns one
other instance of the pattern:

- `ansible/roles/media/tasks/deploy.yml:160-218` — qBittorrent
  stop-before-config-edit / start-after-config-edit pair. Currently
  healthy on the live host (3 GiB cgroup, bind mounts intact) — this
  pattern only bites when the module recreates, which appears to
  correlate with daemon load (the prune-apply happened while the affected
  container was OOM-respawning every ~1 h, possibly producing a Docker API state
  the module couldn't reconcile). Tracked as a Sprint 019 follow-up;
  no urgency, but should be migrated to `docker stop` / `docker start`
  before the next time someone redeploys media during a stress event.

No other call sites use `docker_container` for state transitions
against a compose-managed container.

## Don't repeat

- Never use `community.docker.docker_container: state=stopped/started`
  against a container that has compose labels (`com.docker.compose.*`).
- For state toggles, use `ansible.builtin.command: docker stop|start`.
- For recreates, use `community.docker.docker_compose_v2`.
- `docker_container` is appropriate for containers you own
  end-to-end in Ansible (full spec passed every call) — there are
  currently zero such cases in this repo.
