# Runbook 049 — give rclone its own Google Drive client ID

**Status:** Desktop client created on 28 September 2026; downloaded credentials
are stored privately outside the repository. Publishing, Google consent and
production cutover are not yet complete.
**Why it matters:** performance *and* a deadline.

**Live evidence, 28 September 2026:** three tiny restore reads failed; a bounded
diagnostic returned `RATE_LIMIT_EXCEEDED` for the shared OAuth project, with
`defaultPerMinutePerProject=840000`. Private client ID/secret fields are absent
from both vault and live configuration. Ongoing upload progress is not proof
that reads/restore work.

**Live recheck, 10 October 2026:** an isolated 8 MiB upload diagnostic failed
with exit 1 and `RATE_LIMIT_EXCEEDED` for shared project `202264815644`.
The operator chose limited mitigation with the existing client for now; OAuth
credentials were not changed. This mitigation does not close the private-client
cutover or restore gate.

## The problem

Every rclone install ships with a built-in Google Drive OAuth client, and it is
shared by every rclone user in the world against **one global Google quota**.
rclone's own documentation is blunt about it:

> Using rclone's default Google Drive client_id is not recommended as it is
> shared among all users and subject to Google's global rate limits. Creating
> your own client ID is strongly advised.

Our rclone log adds the deadline, on every rclone start:

```text
NOTICE: gdrive{2Hn7T}: This remote uses rclone's shared Google Drive client_id,
which is being retired and will stop working during 2026.
```

The 4 August observation of 780 KiB/s under a 1 MiB/s cap did not by itself
establish a quota root cause. The 28 September Google error above does establish
shared-project quota exhaustion for the sampled reads. Raising `bwlimit`,
`chunk_size` or `tpslimit` cannot repair that project's exhausted quota.

**Recheck, 02:01–02:02 TRT:** a 427-byte JSON object failed one bounded read,
then passed a second read with matching local SHA-256 and valid JSON, using the
unchanged shared client. Its source remained unchanged. This proves intermittent
access, not permanent revocation, and does not close the full-backup/restore gate.

Nothing in this repo can fix it: minting an OAuth client requires a browser
session against the operator's own Google account.

## The plumbing already exists

`roles/gdrive_backup/templates/rclone.conf.j2` already emits the two fields when
they are set, and they are documented in `vault.yml.example`:

```jinja
{% raw %}{% if vault_rclone_gdrive_client_id | default('') | length > 0 %}
client_id = {{ vault_rclone_gdrive_client_id }}
client_secret = {{ vault_rclone_gdrive_client_secret }}
{% endif %}{% endraw %}
```

They are simply **not defined in the vault yet**, so the block renders empty and
rclone falls back to the shared client. Nothing needs to be written — only
filled in.

## Steps

### Cost boundary

The operator requires **no additional cloud charges**. On 28 September 2026,
the dedicated `homelab-rclone` project (`inner-chassis-509921-f2`) showed
**no linked billing account** in Cloud Console. Keep it that way: do not link
billing, activate a paid trial/plan, or request a paid quota increase as part of
this repair. The existing Drive storage subscription is separate.

Google's [current Drive API limits](https://developers.google.com/workspace/drive/api/guides/limits)
describe a daily threshold of 400 million quota units and 1 TB/day egress for
new projects, with overage billing details to follow later in 2026 and at least
90 days' notice. This is not a promise of unlimited free use forever. If the
unbilled project cannot sustain the workload, surface that limit instead of
enabling billing. Retain the current conservative request and transfer limits.

### 1. Create the OAuth client (browser, your Google account)

1. <https://console.cloud.google.com/> → create a project (any name, e.g.
   `homelab-rclone`).
2. **APIs & Services → Library** → enable **Google Drive API**.
3. **APIs & Services → OAuth consent screen** → **External** → fill in the app
   name and your own email. Under **Audience**, add your own Google account as a
   **Test user**.
   - For unattended Drive access, use *In production* before minting the final
     token. External apps left in *Testing* receive seven-day refresh tokens
     unless scopes are limited to basic identity; Drive access is not that
     exception. See [Google's token-expiration rules](https://developers.google.com/identity/protocols/oauth2#expiration).
     Complete any required branding fields rather than assuming the publish
     button is available. Follow the [current rclone setup](https://rclone.org/drive/#making-your-own-client-id).
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** →
   application type **Desktop app**.
5. Download the client JSON into a private local directory. Do not paste its
   secret into chat or commit it. Verify the client appears in the project's
   **Clients** list: creating the project or enabling Drive does not create it.

The operator created `homelab-rclone-desktop` at 01:54 TRT on 28 September.
The downloaded JSON was checked for the correct project and required fields,
then stored outside git with mode 0600. The app remains External / **Testing**;
**Publish app** is disabled pending Branding. The app name, support and contact
emails are present, but homepage and privacy URLs are missing.

**Operator decision, 28 September:** keep this a personal tool; do not create
a public website or public repository for OAuth branding. The proposed static
site was not published and its local drafts were removed. Keep the homelab
repository private. Do not substitute another party's homepage/privacy policy
or invent URLs to satisfy Branding.

Google's **Internal** audience is for a Workspace/Cloud Identity organization;
the current personal Gmail project uses **External / Testing**. This can support
bounded private validation with the operator as the test user, but Drive refresh
tokens expire after seven days. Do not label that credential as an unattended
production fix or silently deploy it in place of the existing credentials.
The [current rclone instructions](https://rclone.org/drive/#making-your-own-client-id)
document both the new publishing requirements and this Testing-mode limitation.

### 2. Re-mint the token — the step that is easy to miss

An OAuth refresh token is bound to the client that issued it. The existing
`vault_rclone_gdrive_token` was issued by rclone's shared client, so it **will
not work** with the new client_id. Mint a new one on a machine with a browser
(the Pi is headless):

```bash
rclone authorize "drive" "<CLIENT_ID>" "<CLIENT_SECRET>"
```

It prints a JSON blob — that is the new token.

### 3. Put all three into the vault

```bash
cd ansible && ansible-vault edit inventory/group_vars/all/vault.yml
```

```yaml
vault_rclone_gdrive_client_id: "<CLIENT_ID>"
vault_rclone_gdrive_client_secret: "<CLIENT_SECRET>"
vault_rclone_gdrive_token: '<the JSON from step 2>'
```

### 4. Deploy

`rclone.conf` is owned by the `gdrive_backup` role. A long-running rclone
process reads it **once at start**, so any such process must be restarted to
pick the change up.

First validate the new credentials using a separate mode-0600 rclone config:
one bounded listing and a few known tiny-object restore/hash comparisons. Keep
the old encrypted credentials and current live config until these pass. Do not
print either token in a terminal transcript or commit a plaintext client JSON.

Inspect the release plan before promoting the vault/config change. The current
controller deliberately maps shared `inventory/group_vars` changes, including
`vault.yml`, to **no individual target** (`plan_targets` in
`roles/homelab_autodeploy/files/homelab_autodeploy.py`). A vault-only tag therefore
does not apply these credentials. Use the ordinary CI-green deploy-tag path for
role changes; for this credential-only bootstrap/recovery, explicitly run the
Ansible `gdrive-backup` target after syntax and live check/diff gates at the safe
cutover point. Record its exact commit and outcome. Do not manufacture an
unrelated role edit just to trigger CD. The role writes the shared config;
it does not prove that existing upload processes have loaded it.

**Do not cut over mid-upload.** Inspect every rclone `sync`/`copy` owner, its
locks and any retry unit. Prefer letting the owner finish; if it must be
stopped, stop that owner gracefully, preserve source data and record the
interrupted result. Do not force-kill rclone.

```bash
ssh pi@raspberrypi.local 'sudo systemctl status homelab-gdrive-retry-20260927.service --no-pager'
ssh pi@raspberrypi.local 'sudo pgrep -af "rclone (copy|sync)"'
```

The dated retry unit is an example from this incident; inspect current owners
and arguments rather than assuming that one name remains authoritative.

### 5. Verify

Verify that the next rclone run loaded the new client and that bounded cloud
restores match their local checksums. Check logs from the new process start;
absence of an old NOTICE in the last five lines is not proof of credential
activation.

Observe the next full backup's terminal exit, last-success metric and restore
sample. Compare throughput at the current configured cap, object sizes and
workload; neither progress nor a higher transfer rate proves backup integrity.

## What NOT to do

- **Do not delete the old token before the new one works.** Keep the previous
  `vault_rclone_gdrive_token` value to hand until step 5 passes.
- **Do not add a `crypt` remote while you are in there.** The operator decided
  on 2026-08-02 that this account stays browsable from the Drive UI
  ([design 043](../design/043-offsite-backup-google-drive.md)).
- **Keep the current conservative request limit during migration.** Raising it
  cannot repair another project's exhausted quota. Google's current limits use
  method-weighted quota units and the project's actual quota can differ; verify
  it in Cloud Console and the [Drive usage-limit documentation](https://developers.google.com/workspace/drive/api/guides/limits)
  rather than treating ten requests/second as a universal provider limit.
