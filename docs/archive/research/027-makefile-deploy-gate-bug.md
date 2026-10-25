# 027 — Makefile DEPLOY_GATE drops $(PLAYBOOK) before syntax-check + deep-check

**Status:** **fixed 2026-05-22** — option A shipped (commit follows
this note). Verified end-to-end: `make -n fluentbit` now expands with
the playbook in all three positions; `make fluentbit` ran the full
syntax-check → deep-check → apply chain with the arg threaded through
each. See "Fix shipped" section at the bottom.

**Filed:** 2026-05-22 (discovered during Sprint 18b `make fluentbit`).
**Affected:** every deploy target in `Makefile` (`base`, `adguard`,
`media`, `fluentbit`, all 25+ service targets).
**Severity:** silently bypassed the syntax check and the
`DEPLOY_DEEP_CHECK=1` semantic gate — `--check --diff` against the
live Pi never ran even when the env var was set. Pre-deploy
regressions (undefined vars, missing roles, wrong vault keys) reached
the actual `ansible-playbook` step that was the *only* one wired
correctly.
**Pre-fix workaround:** run `ansible-playbook` directly:
`cd ansible && ansible-playbook playbooks/<name>.yml [-e ansible_host=<IP>]`.

## Repro

```bash
make -n fluentbit
```

Expanded recipe (`PLAYBOOK=` is the shell assignment; `$PLAYBOOK` is the
shell variable; the gaps show where `$(PLAYBOOK)` Make-expanded to
empty):

```text
PLAYBOOK=playbooks/fluentbit.yml; cd ansible && \
  ansible-playbook --syntax-check  && \
  echo "✓ syntax OK" && \
  cd /Users/operator/GitHub/raspi-homelab && \
  bash scripts/deploy-deep-check.sh  && \
  cd ansible && \
  ansible-playbook $PLAYBOOK
```

The first call (`ansible-playbook --syntax-check`) errors out with
`error: the following arguments are required: playbook`, so the recipe
aborts before the deploy. `DEPLOY_SKIP_CHECK=1 make <target>` bypasses
this, but at the cost of disabling the gate that's supposed to be on
by default.

## Root cause

`Makefile` defines:

```make
ifndef DEPLOY_SKIP_CHECK
DEPLOY_GATE = cd $(ANSIBLE_DIR) && ansible-playbook --syntax-check $(PLAYBOOK) && \
  echo "✓ syntax OK" && cd $(CURDIR) && \
  bash scripts/deploy-deep-check.sh $(PLAYBOOK) && \
  cd $(ANSIBLE_DIR) && ansible-playbook
else
DEPLOY_GATE = cd $(ANSIBLE_DIR) && ansible-playbook
endif
```

And recipes like:

```make
fluentbit:
    PLAYBOOK=playbooks/fluentbit.yml; $(DEPLOY_GATE) $$PLAYBOOK
```

(Recipe lines are indented with a TAB in the actual Makefile; the
4-space rendering above is markdownlint-friendly. Same applies to the
recipe-line samples further down.)

`$(PLAYBOOK)` inside `DEPLOY_GATE` is a *Make* variable — expanded by
Make at recipe-expansion time. The `PLAYBOOK=…` in the recipe is a
*shell* assignment that only exists inside the spawned `/bin/sh` after
expansion has finished. There is no Make variable named `PLAYBOOK`, so
`$(PLAYBOOK)` always expands to empty. Only the trailing `$$PLAYBOOK`
(escaped to `$PLAYBOOK` for the shell) gets the right value, and only
the final `ansible-playbook` call uses it.

The bug exists since the gate was introduced; Sprint 17 (commit
`9b80c0b`) added the deep-check arm but inherited the same broken
pattern.

## Fix options

### A. Use `$(call …)` with a positional arg (preferred)

Promote `DEPLOY_GATE` to a multi-line variable that takes the playbook
as `$(1)`, then invoke it via `$(call …)`:

```make
define DEPLOY_GATE
cd $(ANSIBLE_DIR) && ansible-playbook --syntax-check $(1) && \
  echo "✓ syntax OK" && cd $(CURDIR) && \
  bash scripts/deploy-deep-check.sh $(1) && \
  cd $(ANSIBLE_DIR) && ansible-playbook $(1)
endef

fluentbit:
    $(call DEPLOY_GATE,playbooks/fluentbit.yml)
```

Removes both the shell assignment and the `$$PLAYBOOK` trailer — the
recipe becomes one self-contained Make-expanded line. Targets stay
one line each.

### B. Target-specific Make variables

```make
fluentbit: PLAYBOOK := playbooks/fluentbit.yml
fluentbit:
    $(DEPLOY_GATE)
```

Works because target-specific recursive variables are visible inside
`$(DEPLOY_GATE)` when it expands. Drops `DEPLOY_GATE`'s trailing
`ansible-playbook` (no more `$$PLAYBOOK`), but every target needs the
two-line form.

### C. Lean on the shell, drop `$(PLAYBOOK)` from `DEPLOY_GATE`

Rewrite `DEPLOY_GATE` to reference `$$PLAYBOOK` throughout, so all
three sub-commands resolve via the shell variable. Smallest diff
(only `Makefile` lines 19-22 change), but the recipe sees one long
shell pipeline with no Make-time visibility into the playbook name.

## Suggested next step

Take option A. Single change to `Makefile`. No `DEPLOY_SKIP_CHECK=1`
needed anymore for normal deploys; `DEPLOY_DEEP_CHECK=1 make <target>`
starts actually running `--check --diff` against the Pi for the first
time since Sprint 17 shipped.

## Verification after the fix

```bash
# Negative — should fail because syntax-check sees a real broken file:
echo "this is not yaml" > /tmp/broken-playbook.yml
make -n fluentbit | grep -q 'ansible-playbook --syntax-check playbooks/fluentbit.yml'

# Positive — gate actually catches a broken playbook in the role:
sed -i.bak 's/^---$/--/' ansible/playbooks/fluentbit.yml
DEPLOY_DEEP_CHECK=1 make fluentbit   # expect: gate fails, no deploy
mv ansible/playbooks/fluentbit.yml.bak ansible/playbooks/fluentbit.yml
```

## Fix shipped (2026-05-22)

Option A was applied. Diff summary:

- `Makefile` lines 19-22 — replaced `$(PLAYBOOK)` (Make variable that
  never resolved) with `$(1)` (positional arg) in both arms of the
  `ifndef DEPLOY_SKIP_CHECK` toggle. Added a comment block pointing
  back at this note.
- 30 deploy-target recipes — rewritten from
  `\tPLAYBOOK=playbooks/X.yml; $(DEPLOY_GATE) $$PLAYBOOK` to
  `\t$(call DEPLOY_GATE,playbooks/X.yml)`. One sed pass.

Targets touched include (no behaviour change beyond the gate now running):
`base`, `adguard`, `portainer`, `media`, `watchtower`, `homepage`,
`filebrowser`, `homelab-control`, `tailscale`,
`tailscale-dns`, `maintainerr`, `jellyseerr`, `traefik`, `beszel`,
`uptime-kuma`, `speedtest`, `scrutiny`, `ntfy`, `nfs-server`,
`victorialogs`, `victoriametrics`, `node-exporter`, `cadvisor`,
`vmagent`, `grafana`, `fluentbit`, `vmalert`, `alertmanager`.

Targets that bypass the gate intentionally and were *not* touched:
`check` (`--check --diff` on base, opt-in dry-run by design),
`backup` (no gate on storage-touching deploy), `verify-vmalert`,
`observability` (umbrella target — composes the gated children),
`verify-ntfy`.

### Evidence

```text
$ make -n fluentbit
cd ansible && ansible-playbook --syntax-check playbooks/fluentbit.yml \
  && echo "✓ syntax OK" \
  && cd /Users/operator/GitHub/raspi-homelab \
  && bash scripts/deploy-deep-check.sh playbooks/fluentbit.yml \
  && cd ansible && ansible-playbook playbooks/fluentbit.yml

$ DEPLOY_DEEP_CHECK=1 make fluentbit       # (off-LAN, mDNS unavailable)
playbook: playbooks/fluentbit.yml
✓ syntax OK
⚠ DEPLOY_DEEP_CHECK=1 set but Pi unreachable — skipping --check --diff
  (run with DEPLOY_SKIP_CHECK=1 to also skip syntax-check, or
   pass -e ansible_host=100.64.0.10 to use Tailscale IP)
PLAY [Deploy Fluent Bit (...)] *********
TASK [Gathering Facts] *****
[ERROR]: Failed to connect to the host via ssh: ssh: Could not resolve
hostname raspberrypi.local: nodename nor servname provided, or not known
```

All three gate phases ran with the playbook arg threaded through; the
final apply only failed on the reachability check, which is a
deploy-environment matter outside the gate's responsibility.

### What about `-e ansible_host=...`

`deploy-deep-check.sh` runs `ansible pi -m ping` with no extra args.
Off-LAN deploys still need the `-e ansible_host=100.64.0.10` override
passed to the *outer* `ansible-playbook` call. Two options if that's
worth cleaning up:

1. Teach `deploy-deep-check.sh` to honour an `ANSIBLE_EXTRA_VARS` env
   var so both the ping and the dry-run see the override.
2. Add a `TAILSCALE=1 make <target>` toggle that injects
   `-e ansible_host=…` everywhere automatically.

Out of scope for this fix. File separately if the manual override
proves annoying during the next remote deploy.
