.PHONY: ping base check adguard portainer media watchtower homepage glances filebrowser homelab-control tailscale traefik maintainerr jellyseerr lint requirements help

ANSIBLE_DIR := ansible

ping: ## Test Pi connectivity
	cd $(ANSIBLE_DIR) && ansible pi -m ping

base: ## Run full base provisioning
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/base.yml

check: ## Dry-run base provisioning (no changes)
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/base.yml --check --diff

adguard: ## Deploy AdGuard Home DNS server
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/adguard.yml

portainer: ## Deploy Portainer container management
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/portainer.yml

media: ## Deploy media stack (Jellyfin, Sonarr, Radarr, Prowlarr, qBittorrent)
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/media.yml

watchtower: ## Deploy Watchtower container auto-updater
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/watchtower.yml

homepage: ## Deploy Homepage dashboard
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/homepage.yml

glances: ## Deploy Glances system monitoring
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/glances.yml

filebrowser: ## Deploy FileBrowser file manager
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/filebrowser.yml

homelab-control: ## Deploy Homelab Control API (shutdown/restart/update)
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/homelab-control.yml

tailscale: ## Deploy Tailscale VPN
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/tailscale.yml

maintainerr: ## Deploy Maintainerr media cleanup
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/maintainerr.yml

jellyseerr: ## Deploy Jellyseerr media requests
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/jellyseerr.yml

traefik: ## Deploy Traefik reverse proxy
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/traefik.yml

lint: ## Run ansible-lint
	cd $(ANSIBLE_DIR) && ansible-lint

requirements: ## Install Ansible collections
	cd $(ANSIBLE_DIR) && ansible-galaxy collection install -r requirements.yml

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
