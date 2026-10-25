# 028 — Backup Restore Runbook

Operator playbook for restoring from `/srv/backups/` snapshots produced
by the `backup` role (Sprint 10). Read this entire doc before running
anything: backups without a verified restore drill are theater.

**Authoritative companions:**

- `docs/archive/sprints/010-automated-backups.md` — full sprint spec
- `ansible/roles/backup/defaults/main.yml` — service paths
- `ansible/roles/backup/templates/homelab-backup.sh.j2` — orchestrator

## Snapshot layout

```text
/srv/backups/
├── qbittorrent/2026-05-18/qbittorrent.tar.gz        # stop_tar
├── sonarr/2026-05-18/sonarr.db.bak.gz               # sqlite
├── radarr/2026-05-18/radarr.db.bak.gz               # sqlite
├── prowlarr/2026-05-18/prowlarr.db.bak.gz           # sqlite
├── jellyseerr/2026-05-18/jellyseerr.tar.gz          # stop_tar
├── maintainerr/2026-05-18/maintainerr.tar.gz        # stop_tar
├── adguard/2026-05-18/adguard.tar.gz                # tar (no quiesce)
└── grafana/2026-05-18/grafana.db.bak.gz             # sqlite
```

All files are owned `root:root`, mode `0600`. Reads, integrity checks,
and restores require `sudo`. Retention: 28 days. Layout owner: the
orchestrator's `mkdir -p` runs as root.

## Universal pre-flight (always run first)

```bash
# 1. Confirm the snapshot you want exists and isn't an in-flight partial.
sudo ls -la /srv/backups/<service>/
# A directory entry ending in `.partial.<pid>` means a backup is mid-write
# — wait for `pgrep -f homelab-backup.sh` to return nothing before
# trusting any same-day snapshot.

# 2. Stop the service you're restoring (containers only — adguard etc.).
docker stop <container>

# 3. Move the live data dir aside with a timestamp suffix. NEVER delete
#    the live state until the restore is smoke-tested.
sudo mv /path/to/live/data /path/to/live/data.preroll-$(date -u +%Y%m%dT%H%M%SZ)
```

Restore can ALWAYS be undone by `mv -f` of the `.preroll-…` directory
back into place, *as long as you haven't started the container against
the restored state and accumulated writes*. Once the container has been
running for any duration, treat the live state as the new authority.

## Pattern A — SQLite (*arr, grafana)

Drilled successfully 2026-05-18 against grafana (1 user/1 org match live).

### Dry-run integrity check

```bash
# Extract the snapshot to a temp file (the in-memory `:memory:` pattern
# doesn't work for piped DBs — sqlite3 expects a file path, not stdin
# bytes).
sudo gunzip -c /srv/backups/<service>/<date>/<service>.db.bak.gz \
  | sudo tee /tmp/<service>-restore-test.db >/dev/null

# Cheap schema sanity:
sudo sqlite3 /tmp/<service>-restore-test.db ".schema" | head -5

# Service-specific row count — must match live (within a small drift
# tolerance if the backup is an hour or more old):
sudo sqlite3 /tmp/<service>-restore-test.db \
  "SELECT COUNT(*) FROM <table>;"
```

Table names verified live:

| Service | Sanity query |
|---|---|
| sonarr | `SELECT COUNT(*) FROM Series;` |
| radarr | `SELECT COUNT(*) FROM Movies;` |
| prowlarr | `SELECT COUNT(*) FROM Indexers;` |
| grafana | `SELECT COUNT(*) FROM user; SELECT COUNT(*) FROM org;` |

If `.schema` errors or the count is wildly off (zero on a non-empty
service, error messages), the snapshot is corrupt: try the previous
week's directory. Don't proceed.

### Live restore (sonarr example)

```bash
# Pre-flight: container down, live data moved aside.
docker stop sonarr
sudo mv /home/pi/docker/media/config/sonarr/sonarr.db \
        /home/pi/docker/media/config/sonarr/sonarr.db.preroll-$(date -u +%Y%m%dT%H%M%SZ)

# Restore — extract to the original location, preserve PUID/PGID.
sudo gunzip -c /srv/backups/sonarr/<date>/sonarr.db.bak.gz \
  | sudo tee /home/pi/docker/media/config/sonarr/sonarr.db >/dev/null
sudo chown 1000:1000 /home/pi/docker/media/config/sonarr/sonarr.db
sudo chmod 0644 /home/pi/docker/media/config/sonarr/sonarr.db

# Bring it back up.
docker start sonarr

# Smoke test: the same endpoint the container healthcheck uses.
sleep 15
curl -sf http://localhost:8989/ping
```

Same shape for radarr/prowlarr/grafana — only the live path and the
container name change (`db_host_path` in
`ansible/roles/backup/defaults/main.yml`). LinuxServer's *arr images expect
`/config/<service>.db`.

## Pattern B — stop_tar (qbittorrent, jellyseerr, maintainerr)

Drilled successfully 2026-05-18 against qBittorrent: 1 078 `.fastresume`
files in the archive, 1 078 in the live config dir (whole tree, not just
the `BT_backup/` subdir — see footnote about XDG path duality below).

### Dry-run integrity check

```bash
# Extract to a temp dir.
sudo mkdir -p /tmp/<service>-restore-test
sudo tar -xzf /srv/backups/<service>/<date>/<service>.tar.gz \
     -C /tmp/<service>-restore-test

# Service-specific spot-check:
# qBittorrent — fastresume count (BT_backup + any data/ XDG copies):
sudo find /tmp/qbit-restore-test -name "*.fastresume" -type f | wc -l
# Jellyseerr — settings.json + db.sqlite3 present:
sudo ls /tmp/jellyseerr-restore-test/home/pi/docker/media/jellyseerr/config/

sudo rm -rf /tmp/<service>-restore-test
```

### Live restore (qBittorrent example — replays the 2026-05-13 incident)

```bash
# Stop the container so the SQLite WAL settles cleanly.
docker stop qbittorrent

# Move live dir aside.
sudo mv /home/pi/docker/media/config/qbittorrent \
        /home/pi/docker/media/config/qbittorrent.preroll-$(date -u +%Y%m%dT%H%M%SZ)

# Restore the whole config tree. The archive root is `/` (the tar was
# built with `-C /`), so this lands the files back where they came from.
sudo tar -xzf /srv/backups/qbittorrent/<date>/qbittorrent.tar.gz -C /
sudo chown -R 1000:1000 /home/pi/docker/media/config/qbittorrent

docker start qbittorrent

# Smoke test: torrent count should match the pre-roll directory's
# fastresume count. The 2026-05-13 incident lost exactly this signal.
sleep 30
QBIT_USER='pi'; QBIT_PASS='<from vault_qbittorrent_password>'
SID=$(curl -sf -c - --data-urlencode "username=$QBIT_USER" \
      --data-urlencode "password=$QBIT_PASS" \
      http://localhost:8181/api/v2/auth/login | awk '/SID/{print $NF}')
curl -sf -b "SID=$SID" http://localhost:8181/api/v2/torrents/info \
  | python3 -c "import sys,json; print('torrents:', len(json.load(sys.stdin)))"
```

If the count matches the snapshot's fastresume count, the restore
worked. If not, halt and investigate before touching the
`.preroll-…` backup directory.

## Pattern C — tar without quiesce (adguard)

AdGuard writes its YAML atomically (rename-into-place), so a file-level
copy without quiesce is safe. Restore is identical to Pattern B except
no `docker stop` is strictly required — but stop it anyway to avoid
racing the running container while you swap files.

```bash
docker stop adguard
sudo mv /home/pi/docker/network/adguard/data/conf \
        /home/pi/docker/network/adguard/data/conf.preroll-$(date -u +%Y%m%dT%H%M%SZ)
sudo tar -xzf /srv/backups/adguard/<date>/adguard.tar.gz -C /
docker start adguard

# Smoke test: DNS resolution works.
sleep 5
dig +short ntfy.example @192.0.2.100   # → 192.0.2.100
```

## Drill output captured 2026-05-18

These are the actual command outputs from the post-deploy smoke test,
verbatim. If your future drill returns wildly different numbers (zero
counts, integrity errors), the snapshot you're looking at is corrupt
or partial.

```text
=== Grafana drill ===
Snapshot: 1 user, 1 org
Live:     1 user, 1 org
→ MATCH

=== qBittorrent drill ===
Snapshot: 1 078 fastresume files (recursive)
Live:     1 078 fastresume files (recursive)
→ MATCH
```

## Known caveats

1. **`.fastresume` count comparison must be recursive across the whole
   config dir.** qBit 5.x writes to BOTH `qBittorrent/BT_backup/` (legacy
   XDG path) AND `qBittorrent/data/BT_backup/` (new XDG path). The
   2026-05-13 incident was exactly this dual-path issue. `find -name
   "*.fastresume"` from the config root, not from a sub-path, is the
   safe pattern.
2. **Snapshot files are mode 0600 root-only.** Reading, drilling, and
   restoring all need `sudo`. Don't `chmod` them — restrictive mode is
   intentional (these contain secrets — *arr API keys, qBit credentials).
3. **Same-day re-runs land into a `.partial.$$` tmp file first** and
   atomic-rename into place at the end. So a backup running while you
   drill won't corrupt the previous snapshot — but if you see a
   `.partial.…` file in the directory, that's the in-flight write,
   ignore it. Always validate against the non-partial filename.
4. **SQLite restore needs the right binary on host.** Pi has `sqlite3
   3.46.1` (verified 2026-05-18). Backups were taken with this version;
   restoring on a system with a much older sqlite (< 3.34) may fail to
   read the file. Stay on the Pi for restores.
5. **No offsite snapshots yet.** This sprint is local-only. Pi disk
   failure = backup gone with the live data. Sprint 11 covers offsite
   (rclone → Google Drive).
6. **`docker stop --time=30`** is the orchestrator's quiesce timeout.
   For a clean restore, the original snapshot is point-in-time
   consistent if the stop took less than 30 s; if a service was busy and
   needed longer, you may see SQLite WAL files in the snapshot that need
   `sqlite3 ... ".wal_checkpoint"` post-restore. None observed in the
   2026-05-18 baseline drill — listed for completeness.

## Rolling back a botched restore

```bash
# Stop the container.
docker stop <container>

# Swap back to the pre-roll snapshot.
sudo rm -rf /path/to/live/data
sudo mv /path/to/live/data.preroll-<ts> /path/to/live/data

docker start <container>
```

The `.preroll-<ts>` directory is the last-known-good live state — keep
it around for at least one full backup cycle (one week) after a
successful restore in case a latent issue surfaces.

## Disaster scenarios

| Scenario | Recovery |
|---|---|
| One service's DB corrupted | Pattern A/B for that service. ~5 min wall-clock. |
| Whole `/srv/docker` lost (SSD failure on docker data root) | Re-provision via `make base` → `make <service>` for each, then restore each from `/srv/backups`. Backups are on the same SSD — if the whole disk is gone, this sprint won't save you. **Sprint 11 (offsite) is the answer.** |
| `/srv/backups` deleted | Last weekly snapshot is gone. Lower-priority data is rebuildable from the live containers; high-value data (the application databases) is only recoverable from the running container's own DB. Run `make backup` immediately. |
| Botched restore (wrong snapshot date / corrupt archive) | Rollback section above. Keep the `.preroll-…` dir until next successful drill. |
| Vault file lost | Snapshots include service config files but **not** the Ansible vault (`ansible/inventory/group_vars/all/vault.yml`). Keep that backed up separately (the git repo + a personal password manager). |
