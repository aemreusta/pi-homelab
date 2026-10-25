# 2026-09-28 — Tdarr playbook bypassed the base-role deployment boundary

- **What happened:** The service-only release reached Tdarr and executed the Docker provisioning role during its deep check. The CD target plan correctly excluded base provisioning.
- **Why:** `playbooks/tdarr.yml` listed `docker` directly. The September guard inspected role metadata dependencies but did not inspect playbook role lists.
- **Class:** An incomplete execution-boundary check: the same privileged role could enter through a second Ansible call path.
- **Occurrence:** Recurrence of the September 5 base-deny-list bypass described in `test_role_meta_dependencies.py`. Live deep-check output showed unchanged packages, daemon configuration and firewall configuration; the GPG fetch alone reported a predicted change. The existing release was allowed to finish after this inspection, rather than interrupting a converged host halfway through its rollout.
- **Structural fix:** Remove `docker` from the Tdarr playbook. Extend regression coverage to every service playbook, direct short/dict role forms, nested include/import-role tasks and base-playbook imports. Base provisioning remains an explicit prerequisite. Its separate APT-module deprecation remains a separate follow-up rather than being changed through a service release.
