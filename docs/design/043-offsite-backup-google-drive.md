# 043 — Off-site backup to Google Drive (rclone)

**Status:** shipped and live — token in vault, unencrypted `homelab/` tree
**Date:** 2026-08-02
**Account:** `backup-account@example.com` — 5 TB available, backup set is ~6 GiB/week
**Supersedes:** the retired Sprint 013 crypt/three-version remote-retention
proposal and the "second local disk holds the backups" half of
[design 042](042-second-ssd-integration-plan.md), which
[failed five times](../conventions/pi4-usb-power-budget.md).

---

## Why

After the ssd2 rollback, `backup_root` is back on `/srv/backups` — **the same
disk the backups protect, and the same disk that has failed five times in twelve
hours.** That is not a backup, it is a copy. The second-local-disk answer is
dead: the Pi cannot power one. Google Drive has no power budget to exceed, is
genuinely off-device and off-site, and the account already has 5 TB.

This does **not** replace the local backup. The local set stays the fast restore
path; Drive is the copy that survives the Pi losing its disk.

**Scope:** this backup sync copies service snapshots off-site; it does not
reclaim live media. Application configuration and generated assets remain local
unless separately migrated.

## Shape

```text
  weekly local backup            off-site push
  (Sun 03:00, ~5 min)            (Sun 05:00)
  homelab-backup.sh  ─────►  /srv/backups  ─────►  rclone sync  ─────►  Google Drive
                              (~6 GiB)                                  homelab/backups/
```

`05:00` is deliberate: the local backup starts 03:00 and docker-prune runs 04:00,
so the push lands after both.

## Decisions

**Plaintext, not encrypted — operator decision, 2026-08-02.** A `crypt` remote
was built first and then removed. The argument for encrypting was that the
backup set contains Postgres dumps, SQLite databases and service configs — i.e.
credentials, including Authelia's session and OIDC store. The operator's call:
the account is single-owner and private, and the tree must stay **browsable in
the Drive UI** because this account is also a general storage channel, not just
a backup sink. Crypt makes filenames and directory names opaque, which defeats
that.

**The residual risk, stated plainly:** anything that gets the Google account
gets the credentials inside the backups. Google itself was never the threat
model; account compromise is. Cheap mitigations worth doing: 2FA on the
account, and periodically reviewing third-party app access (revoking rclone
there is also what `GdriveBackupStale` would catch).

**One `homelab/` tree**, not a flat dump:

```text
homelab/
├── backups/     <- rclone sync target for /srv/backups
├── movies/
├── documents/
└── archive/
```

Folders are created idempotently on every deploy (`rclone mkdir`), because
Drive has no `mkdir -p` that a plain `sync` would trigger for anything beyond
its own target.

**Upstream rclone binary, pinned to `v1.75.0`.** Debian Trixie ships 1.60.1
(2022), which predates several years of Google Drive OAuth and uploader fixes.
Pinned so a re-provision installs the same thing.

**`sync`, not `copy` — and therefore an empty-source guard.** `sync` mirrors
deletions, which is what keeps Drive from growing forever as retention prunes
locally. The cost is that an empty or unmounted source would **delete the
off-site copy**. That is not theoretical: on 2026-08-02 the ssd2 bind went away
and `/srv/backups` became an empty directory on root. So the script refuses to
run when the source has zero files, and says so loudly.

**Bandwidth-capped at 4 MB/s** by default. The uplink is shared with the rest of
the house; an uncapped 6 GiB push saturates it.

**Metrics on every exit path** (`trap emit EXIT`), per the repo's silent-cron
rule. A job that dies leaving yesterday's green gauge behind is the failure mode
this repo has shipped four times.

## Alerts

Three rules, because an off-site backup that quietly stopped is worse than none —
you believe you are covered.

| Alert | Fires when | Why it exists |
|---|---|---|
| `GdriveBackupStale` | no run in >14 d | Two missed weeklies. Catches a revoked OAuth token — Google can revoke refresh tokens from the account page, and nothing local would notice. |
| `GdriveBackupFailing` | last sync exited non-zero | The obvious one. |
| `GdriveBackupNoData` | succeeds but 0 bytes moved in 30 d | The subtle one. rclone exits 0 with nothing to do, so the success gauge alone cannot distinguish "working" from "the local backup stopped producing files". Silent-zero class. |

## The operator step (done)

The Pi is headless and modern rclone dropped the paste-a-code flow, so the OAuth
token must be minted on a machine with a browser:

```bash
brew install rclone          # on the Mac
rclone authorize "drive"     # opens a browser, prints a JSON token
```

Then one value goes into `ansible/inventory/group_vars/all/vault.yml`:

```yaml
vault_rclone_gdrive_token: '<the JSON blob printed above>'
```

**Done 2026-08-02** for `backup-account@example.com`. The `refresh_token` inside
that blob is the long-lived part; rclone renews the access token itself.

Without the token the role is still safe to deploy: it installs rclone and the
script but **skips `rclone.conf` and leaves the cron absent**, so nothing fails
and no alert fires on a credential it was never given.

### Your own OAuth client — REQUIRED before the end of 2026

The first live sync surfaced this, verbatim from rclone:

> `NOTICE: gdrive: This remote uses rclone's shared Google Drive client_id,
> which is being retired and will stop working during 2026. Create your own
> client_id to avoid interruption.`

So `vault_rclone_gdrive_client_id` / `_secret` are **not optional** — they are a
dated deadline. When the shared ID is retired this setup stops uploading, and
the only thing that would notice is `GdriveBackupStale` two weeks later. Create
a client at <https://rclone.org/drive/#making-your-own-client-id>, add both
values to vault, and re-run `make gdrive-backup`. See
[runbook 049](../runbooks/049-gdrive-own-client-id.md).

## Verify

```bash
make gdrive-backup            # deploy
make gdrive-backup-run        # trigger a sync now
rclone ls gdrive:homelab/backups --config /root/.config/rclone/rclone.conf  # on the Pi
curl -s localhost:9100/metrics | grep gdrive_backup_
```

**First live run, 2026-08-02:** 17 objects / 908.041 MiB uploaded, 533 s,
`gdrive_backup_success 1`, `gdrive_backup_errors 0`. Verified with
`rclone size gdrive:homelab`.

A restore drill — pull a snapshot back from Drive and actually open it — has
**not** happened yet. Until it does, this is an unverified backup.
