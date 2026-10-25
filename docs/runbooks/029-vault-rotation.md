# Vault Password Rotation

## Current setup

- **Password file:** `.vault-password` (gitignored, in repo root)
- **Encrypted file:** `ansible/inventory/group_vars/all/vault.yml`
- **Encryption method:** Ansible Vault with AES256 (default)
- **Contains:** qBittorrent credentials, homelab-control API token, NextDNS API key

## Rotation procedure

### 1. Re-key the vault

```bash
# Current password is in .vault-password
OLD_PASS=$(cat .vault-password)

# Generate a new password (32 chars, no special chars that break shells)
NEW_PASS=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)

# Re-encrypt with the new password
ansible-vault rekey ansible/inventory/group_vars/all/vault.yml \
  --vault-password-file .vault-password \
  --new-vault-password-file <(echo "$NEW_PASS")
```

### 2. Update the password file

```bash
echo "$NEW_PASS" > .vault-password
```

### 3. Verify

```bash
# Should decrypt without errors
ansible-vault view ansible/inventory/group_vars/all/vault.yml

# Syntax check should pass
make validate
```

### 4. Deploy

```bash
make base        # re-provisions anything that reads vault vars
```

## Offsite backup (encrypted copy)

Store a copy of `.vault-password` somewhere other than this machine:

```bash
# Encrypt with a passphrase you remember (NOT the vault password)
gpg --symmetric --cipher-algo AES256 .vault-password
# Produces .vault-password.gpg — commit this to a private repo,
# store on a USB drive, or upload to cloud storage.
```

Recovery:

```bash
gpg --decrypt .vault-password.gpg > .vault-password
```

## Recovery if .vault-password is lost

Without the password, `vault.yml` cannot be decrypted. Recovery requires:

1. Re-entering every credential manually into a new `vault.yml`
2. Re-encrypting with a new password

The credentials themselves are recoverable (they exist in running service configs on the Pi), but it's tedious:

```bash
# qBittorrent: check the running config
ssh raspberrypi.local 'cat ~/docker/media/qBittorrent/config/qBittorrent.conf' | grep WebUI

# homelab-control: check the systemd unit
ssh raspberrypi.local 'grep -o "token=[^ ]*" /etc/systemd/system/homelab-control.service'
```

## Rotation schedule

No fixed schedule. Rotate when:

- A team member who had access to the password leaves
- The password may have been exposed (e.g., accidental commit, shared screen)
- As part of a periodic security review (recommend every 6 months)
