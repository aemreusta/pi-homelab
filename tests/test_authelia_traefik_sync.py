"""Guard: Traefik's `auth: true` set and Authelia's access_control set must match.

Forward-auth is configured in two files that know nothing about each other:

  * `roles/traefik/defaults/main.yml`  — `traefik_services[].auth: true` decides
    which routers get the `authelia` middleware attached.
  * `roles/authelia/defaults/main.yml` — `authelia_forward_auth_services` decides
    which domains get an `access_control` rule.

Authelia's `default_policy` is `deny`. So the two failure modes are not
symmetric, and the dangerous one is silent:

  * **In Traefik, missing from Authelia** → the middleware sends every request to
    Authelia, no rule matches the domain, deny wins, and the service returns 403
    to users who ARE logged in. Total outage that reads as a working security
    control. This is exactly what happened to filebrowser between 2026-08-02 and
    2026-08-04.
  * **In Authelia, missing from Traefik** → a rule nobody consults. Harmless
    today, but it is a claim the deployment does not honour, and it makes the
    Authelia config lie about what is actually gated.

Both are caught here, because the second is how the first gets introduced during
a half-finished rollout.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAEFIK_DEFAULTS = os.path.join(
    REPO_ROOT, "ansible", "roles", "traefik", "defaults", "main.yml"
)
AUTHELIA_DEFAULTS = os.path.join(
    REPO_ROOT, "ansible", "roles", "authelia", "defaults", "main.yml"
)


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _traefik_gated():
    services = _load(TRAEFIK_DEFAULTS).get("traefik_services") or []
    return {s["name"] for s in services if isinstance(s, dict) and s.get("auth") is True}


def _authelia_gated():
    return set(_load(AUTHELIA_DEFAULTS).get("authelia_forward_auth_services") or [])


def test_every_traefik_gated_service_has_an_authelia_rule():
    missing = sorted(_traefik_gated() - _authelia_gated())
    assert not missing, (
        "flagged `auth: true` in traefik_services but absent from "
        "authelia_forward_auth_services: " + ", ".join(missing) + ".\n"
        "Authelia's default_policy is deny, so these will 403 EVERY user, "
        "including authenticated ones (the 2026-08-02 filebrowser outage)."
    )


def test_every_authelia_rule_has_a_traefik_gated_service():
    extra = sorted(_authelia_gated() - _traefik_gated())
    assert not extra, (
        "listed in authelia_forward_auth_services but not flagged `auth: true` in "
        "traefik_services: " + ", ".join(extra) + ".\n"
        "The rule is never consulted — either gate the service in Traefik or drop "
        "the rule, but do not let the Authelia config claim a service is protected "
        "when no middleware routes to it."
    )


def test_the_sso_portal_never_gates_itself():
    """`auth` on the auth portal is a redirect loop: Authelia would forward-auth
    its own login page. Called out in the traefik defaults; asserted here."""
    services = _load(TRAEFIK_DEFAULTS).get("traefik_services") or []
    portal = next((s for s in services if s.get("name") == "auth"), None)
    assert portal is not None, "the `auth` (Authelia portal) service disappeared"
    assert portal.get("auth") is not True, (
        "the Authelia portal is flagged `auth: true` — it would gate its own login "
        "page and every SSO login would redirect-loop"
    )
