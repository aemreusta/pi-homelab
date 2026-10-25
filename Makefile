.PHONY: ping base check adguard portainer media watchtower homepage filebrowser homelab-control tailscale tailscale-dns traefik zot jellyseerr beszel uptime-kuma speedtest ntfy postgresql authelia minio label-studio nfs-server victorialogs victoriametrics vmalert alertmanager verify-vmalert node-exporter github-runner-metrics homelab-autodeploy cadvisor vmagent grafana fluentbit observability backup backup-run lint lint-ports shellcheck test test-bats cd-drill molecule validate verify-ntfy requirements cp-clean help

ANSIBLE_DIR := ansible
export PATH := $(CURDIR)/.venv/bin:$(PATH)
# macOS make can resolve a simple recipe with its original process PATH.
# Use the explicit interpreter as well as exporting PATH for shell recipes.
PYTHON := $(if $(wildcard $(CURDIR)/.venv/bin/python3),$(CURDIR)/.venv/bin/python3,python3)

.PHONY: dev dev-upgrade
dev: ## Install the locked Python toolchain into .venv (requires uv)
	@test -x .venv/bin/python || uv venv --python 3.12 .venv
	uv pip sync --python .venv/bin/python requirements-dev.lock

dev-upgrade: ## Resolve latest compatible development tools and refresh the lock
	uv pip compile --universal --python-version 3.12 --upgrade requirements-dev.txt -o requirements-dev.lock
	$(MAKE) dev

# ── Manual deploys are being RETIRED onto the GitOps CD (design 039) ─────────
# The Pi-side homelab_autodeploy controller auto-deploys allowlisted roles on
# every CI-green commit to master; the manual `make <service>` targets are the
# escape hatch (off-LAN, emergency, pre-pilot roles), NOT the primary path.
# Targets tagged "[CD pilot]" in `make help` already auto-deploy on commit —
# prefer committing over running them by hand. The pilot cohort widens one
# tier per sprint (ansible/roles/homelab_autodeploy/defaults/main.yml:
# homelab_autodeploy_target_allowlist); everything else stays manual until it
# earns in. Do NOT delete these targets — retirement here means "stop reaching
# for them", not "remove the capability".

# Cross-repo deploy gate: every `make <deploy>` runs syntax-check + a
# semantic `--check --diff` against the live Pi BEFORE applying. Toggles:
#   DEPLOY_DEEP_CHECK=0   skip ONLY the deep `--check --diff`, keep
#                         syntax-check. The deep check resolves vars/when/
#                         roles/vault/Jinja and catches what --syntax-check
#                         can't (undefined vars, missing roles, wrong vault
#                         key) — empirically --syntax-check exits 0 on a
#                         totally undefined var. Born opt-in (Sprint 017
#                         Story 9), flipped to DEFAULT-ON 2026-06-25 (H1)
#                         because it's the only var-resolving deploy-time
#                         gate and it self-disables when the Pi is
#                         unreachable, so off-LAN Tailscale deploys still
#                         work without extra flags.
#   DEPLOY_SKIP_CHECK=1   skip BOTH checks (e.g. emergency outage where the
#                         playbook intentionally has a broken include for a
#                         half-rolled-back role).
# `make validate` (full lint+test+port-check) is still the pre-merge bar;
# this gate catches problems at deploy time.
# DEPLOY_GATE takes the playbook path as $(1) — call via $(call …) so
# the value is a real Make variable, not a sibling shell assignment.
# See docs/archive/research/027-makefile-deploy-gate-bug.md for the history of
# the broken $(PLAYBOOK) pattern this replaced.
# CP_CLEAN clears stale SSH ControlPath sockets before a deploy. Background:
# a stuck `ansible-playbook` during the Tdarr-dedup deploy left 5+ orphaned
# control connections that piled up past sshd's default MaxStartups (10:30:100)
# and transiently throttled new SSH from the Mac (~1h lost — incident
# 2026-05-29, docs/incidents/). This was NOT a fail2ban ban (jail showed 0
# banned) — just connection pile-up. Pruning sockets idle >30 min stops the
# recurrence. CAVEAT: this is time-based, so a *legitimate* multi-host deploy
# whose own control socket has been idle >30 min could lose it (it just
# reconnects). The correct long-term fix is `ssh -O check`-based wedge
# detection — tracked as a follow-up in docs/incidents/. `-type s` scopes to
# sockets only; the `|| true` keeps a missing ~/.ansible/cp dir from failing
# the gate on a fresh checkout.
CP_CLEAN = find $${HOME}/.ansible/cp -mmin +30 -type s -delete 2>/dev/null || true

ifndef DEPLOY_SKIP_CHECK
DEPLOY_GATE = $(CP_CLEAN); cd $(ANSIBLE_DIR) && ansible-playbook --syntax-check $(1) && echo "✓ syntax OK" && cd $(CURDIR) && bash scripts/deploy-deep-check.sh $(1) && cd $(ANSIBLE_DIR) && ansible-playbook $(1)
else
DEPLOY_GATE = $(CP_CLEAN); cd $(ANSIBLE_DIR) && ansible-playbook $(1)
endif

ping: ## Test Pi connectivity
	cd $(ANSIBLE_DIR) && ansible pi -m ping

base: ## Run full base provisioning
	$(call DEPLOY_GATE,playbooks/base.yml)

check: ## Dry-run base provisioning (no changes)
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/base.yml --check --diff

adguard: ## Deploy AdGuard Home DNS server
	$(call DEPLOY_GATE,playbooks/adguard.yml)

portainer: ## Deploy Portainer container management
	$(call DEPLOY_GATE,playbooks/portainer.yml)

media: ## Deploy media stack (Jellyfin, Sonarr, Radarr, Prowlarr, qBittorrent)
	$(call DEPLOY_GATE,playbooks/media.yml)

watchtower: ## Deploy Watchtower container auto-updater
	$(call DEPLOY_GATE,playbooks/watchtower.yml)

homepage: ## Deploy Homepage dashboard [CD pilot: auto-deploys on commit]
	$(call DEPLOY_GATE,playbooks/homepage.yml)

filebrowser: ## Deploy FileBrowser file manager
	$(call DEPLOY_GATE,playbooks/filebrowser.yml)

homelab-control: ## Deploy Homelab Control API (shutdown/restart/update) [CD pilot: auto-deploys on commit]
	$(call DEPLOY_GATE,playbooks/homelab-control.yml)

tailscale: ## Deploy Tailscale VPN
	$(call DEPLOY_GATE,playbooks/tailscale.yml)

tailscale-dns: ## Deploy Tailscale DNS resolver (dnsmasq)
	$(call DEPLOY_GATE,playbooks/tailscale-dns.yml)

jellyseerr: ## Deploy Jellyseerr media requests
	$(call DEPLOY_GATE,playbooks/jellyseerr.yml)

traefik: ## Deploy Traefik reverse proxy
	$(call DEPLOY_GATE,playbooks/traefik.yml)

zot: ## Deploy Zot local OCI registry (+ web UI) for homelab-built images
	$(call DEPLOY_GATE,playbooks/zot.yml)

beszel: ## Deploy Beszel monitoring hub and agent
	$(call DEPLOY_GATE,playbooks/beszel.yml)

uptime-kuma: ## Deploy Uptime Kuma endpoint monitor
	$(call DEPLOY_GATE,playbooks/uptime-kuma.yml)

uptime-kuma-drift: ## Show Uptime Kuma config drift (live monitors vs committed JSON)
	ssh pi@raspberrypi.local 'sudo /usr/local/bin/uptime-kuma-drift.sh -v' \
	  || ssh pi@100.64.0.10 'sudo /usr/local/bin/uptime-kuma-drift.sh -v'

uptime-kuma-seed: ## Idempotently (re)seed UK monitors+notifications from the committed JSON
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/uptime-kuma.yml --tags seed

speedtest: ## Deploy Speedtest Tracker (hourly ISP bandwidth log)
	$(call DEPLOY_GATE,playbooks/speedtest.yml)

ntfy: ## Deploy ntfy push notification service
	$(call DEPLOY_GATE,playbooks/ntfy.yml)

postgresql: ## Deploy PostgreSQL shared ML backend (MLflow, Langfuse, Prefect)
	$(call DEPLOY_GATE,playbooks/postgresql.yml)

authelia: ## Deploy Authelia SSO / OIDC provider + forward-auth
	$(call DEPLOY_GATE,playbooks/authelia.yml)

minio: ## Deploy MinIO S3-compatible object storage
	$(call DEPLOY_GATE,playbooks/minio.yml)

mlflow: ## Deploy MLflow tracking server (requires postgresql + minio)
	$(call DEPLOY_GATE,playbooks/mlflow.yml)

label-studio: ## Deploy Label Studio (audio/ASR dataset labeling UI)
	$(call DEPLOY_GATE,playbooks/label-studio.yml)

nfs-server: ## Deploy NFS server for Mac compute offload
	$(call DEPLOY_GATE,playbooks/nfs-server.yml)

victorialogs: ## Deploy VictoriaLogs (LogsQL log database)
	$(call DEPLOY_GATE,playbooks/victorialogs.yml)

victoriametrics: ## Deploy VictoriaMetrics (Prometheus-compatible TSDB)
	$(call DEPLOY_GATE,playbooks/victoriametrics.yml)

node-exporter: ## Deploy Prometheus node_exporter (host metrics) [CD pilot: auto-deploys on commit]
	$(call DEPLOY_GATE,playbooks/node-exporter.yml)

github-runner-metrics: ## Deploy Pi-side emitter for the Mac CI runner (design 039)
	$(call DEPLOY_GATE,playbooks/github-runner-metrics.yml)

homelab-autodeploy: ## Deploy Pi-side homelab GitOps CD controller (auto-deploy/rollback, design 039)
	$(call DEPLOY_GATE,playbooks/homelab-autodeploy.yml)

cadvisor: ## Deploy Google cAdvisor (container metrics) [CD pilot: auto-deploys on commit]
	$(call DEPLOY_GATE,playbooks/cadvisor.yml)

vmagent: ## Deploy vmagent (metric scraper for VictoriaMetrics) [CD pilot: auto-deploys on commit]
	$(call DEPLOY_GATE,playbooks/vmagent.yml)

grafana: ## Deploy Grafana (dashboards & explore UI) [CD pilot: auto-deploys on commit]
	$(call DEPLOY_GATE,playbooks/grafana.yml)

fluentbit: ## Deploy Fluent Bit (log collector for VictoriaLogs) [CD pilot: auto-deploys on commit]
	$(call DEPLOY_GATE,playbooks/fluentbit.yml)

vmalert: ## Deploy vmalert (alert-rule evaluator for VM + VL)
	$(call DEPLOY_GATE,playbooks/vmalert.yml)

alertmanager: ## Deploy Alertmanager + ntfy-bridge webhook receiver
	$(call DEPLOY_GATE,playbooks/alertmanager.yml)

verify-vmalert: ## End-to-end probe: chain check (CHECK_LOGSQL=1 also probes VL field names)
	@flags=""; [ "$${CHECK_LOGSQL:-0}" = "1" ] && flags="--check-logsql"; \
	  ssh pi@raspberrypi.local "sudo /usr/local/bin/verify-vmalert.sh $$flags" \
	  || ssh pi@100.64.0.10 "sudo /usr/local/bin/verify-vmalert.sh $$flags"

observability: victorialogs victoriametrics node-exporter cadvisor vmagent grafana fluentbit vmalert alertmanager traefik homepage  ## Deploy whole observability stack (Sprints 9+18)

lint: ## Run ansible-lint
	cd $(ANSIBLE_DIR) && ansible-lint

lint-ports: ## Check for duplicate host ports across compose templates
	$(PYTHON) tests/lint_ports.py

shellcheck: ## Render pipeline shell templates and run shellcheck on them
	@command -v shellcheck >/dev/null 2>&1 || { echo "shellcheck not installed (brew install shellcheck)"; exit 1; }
	@bash scripts/check-shell.sh

backup: ## Deploy automated backup role (cron + scripts + state dir)
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/backup.yml

gdrive-backup: ## Deploy off-site backup to Google Drive (rclone, encrypted)
	$(call DEPLOY_GATE,playbooks/gdrive-backup.yml)

gdrive-backup-run: ## Trigger an immediate off-site sync to Google Drive
	ssh pi@raspberrypi.local 'sudo /usr/local/bin/homelab-gdrive-sync.sh'

backup-run: ## Trigger an immediate full-stack backup on the Pi
	ssh pi@raspberrypi.local 'sudo /usr/local/bin/homelab-backup.sh'

test: test-bats ## Run Python tests + bats shell tests
	$(PYTHON) -m pytest tests/ -v

test-bats: ## Run bats tests for shell libraries (alert-lib, deploy-deep-check)
	@command -v bats >/dev/null 2>&1 || { echo "bats is required (brew install bats-core)"; exit 1; }
	@bats tests/*.bats

cd-drill: ## CD drill — end-to-end rollback→held→alert test of the autodeploy bash orchestrator
	$(PYTHON) -m pytest tests/test_autodeploy_drill.py -v

molecule: ## Run Molecule scenarios for all roles that have them (check-mode, no DinD)
	@for d in ansible/roles/*/molecule; do \
	  role=$$(dirname $$d); \
	  echo "==> molecule test: $$role"; \
	  ( cd $$role && molecule test ) || exit $$?; \
	done

validate: go-test ## Pre-flight checks: lint + syntax + tests + ports + all shell scripts
	ruff check tests scripts mac-worker ansible/roles/*/files
	cd $(ANSIBLE_DIR) && ansible-lint --offline
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/base.yml --syntax-check
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/verify-ntfy.yml --syntax-check
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/backup.yml --syntax-check
	$(PYTHON) -m pytest tests/ -v
	@$(MAKE) test-bats
	$(PYTHON) tests/lint_ports.py
	@$(MAKE) shellcheck
	@echo "All checks passed."

verify-ntfy: ## Verify ntfy.example plumbing (host DNS, /v1/health, container DNS)
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/verify-ntfy.yml

cp-clean: ## Prune stale SSH ControlPath sockets (>30 min idle) — also auto-runs before every deploy
	@$(CP_CLEAN); echo "✓ pruned ControlPath sockets idle >30 min (~/.ansible/cp)"

requirements: ## Install/upgrade Ansible collections to latest (standing "latest everything" directive)
	# --upgrade is load-bearing: `ansible-galaxy collection install` is a NO-OP
	# when a collection is already present at ANY version, so without it the
	# "latest everything" directive silently never happens for collections —
	# stale installs persist for months. That is exactly how ansible.posix sat
	# on 2.1.0 (deprecated `ansible.module_utils._text` imports → INJECT-era
	# deprecation spam) while 2.2.1 with the fix was available. Always upgrade.
	cd $(ANSIBLE_DIR) && ansible-galaxy collection install -r requirements.yml --upgrade

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help

.PHONY: go-test go-build homelab-runtime
go-test: ## Test Go runtime, race safety and vet
	cd runtime && go test -race ./... && go vet ./...

go-build: ## Build Linux and Darwin ARM64 runtime binaries
	mkdir -p build/linux-arm64 build/darwin-arm64
	cd runtime && CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath -o ../build/linux-arm64/homelab-agent ./cmd/homelab-agent
	cd runtime && CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build -trimpath -o ../build/darwin-arm64/homelab-agent ./cmd/homelab-agent

homelab-runtime: ## Bootstrap or update the shared Go runtime via Ansible
	$(call DEPLOY_GATE,playbooks/homelab-runtime.yml)
