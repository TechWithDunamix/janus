"""The dashboard's route table.

Every entry is an exact path rather than a prefix, so nothing here claims a
subtree — which is what lets the `/api` router live in the same application
without an ordering rule.

`_r` is the only place a handler becomes a `Route`, and it is where the guard
that `routes/web/_kit.py` stashed on the handler is lifted onto
`Route(..., auth=...)`. Two consequences:

* The router runs the gate, so authorization is enforced by the framework
  rather than by a wrapper a handler could be registered without.
* `route.auth` is inspectable, so `tests/test_routes.py` can assert that every
  dashboard route is guarded — the check that catches a route added without
  protection.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sillo.core.routing import Route

from routes.web import auth, docs, gateway, overview, security, team
from routes.web._kit import GATE_ATTR

__all__ = ["routes"]


def _r(
    path: str,
    handler: Callable[..., Any],
    *,
    methods: list[str] | None = None,
    name: str | None = None,
) -> Route:
    """Build a route, lifting the handler's guard onto it."""
    return Route(
        path,
        handler=handler,
        methods=methods or ["GET"],
        name=name,
        auth=getattr(handler, GATE_ATTR, None),
    )


routes: list[Route] = [
    # -- Authentication (the only unguarded pages) -------------------------
    _r("/login", auth.login, name="login"),
    _r("/login", auth.login_submit, methods=["POST"], name="login.submit"),
    _r("/logout", auth.logout, methods=["POST"], name="logout"),

    # -- Overview ----------------------------------------------------------
    _r("/", overview.overview, name="overview"),

    # -- Gateway -----------------------------------------------------------
    _r("/gateways", gateway.gateways, name="gateways"),
    _r("/gateways/save", gateway.gateway_save, methods=["POST"], name="gateways.save"),
    _r("/gateways/maintenance", gateway.gateway_maintenance, methods=["POST"],
       name="gateways.maintenance"),
    _r("/gateways/switch", gateway.switch_gateway, methods=["POST"], name="gateways.switch"),

    _r("/routes", gateway.routes_index, name="routes"),
    _r("/routes/{route_id:int}", gateway.route_edit, name="routes.edit"),
    _r("/routes/save", gateway.route_save, methods=["POST"], name="routes.save"),
    _r("/routes/toggle", gateway.route_toggle, methods=["POST"], name="routes.toggle"),
    _r("/routes/delete", gateway.route_delete, methods=["POST"], name="routes.delete"),

    _r("/upstreams", gateway.upstreams, name="upstreams"),
    _r("/upstreams/{upstream_id:int}", gateway.upstream_edit, name="upstreams.edit"),
    _r("/upstreams/save", gateway.upstream_save, methods=["POST"], name="upstreams.save"),
    _r("/upstreams/delete", gateway.upstream_delete, methods=["POST"], name="upstreams.delete"),

    _r("/domains", gateway.domains, name="domains"),
    _r("/domains/save", gateway.domain_save, methods=["POST"], name="domains.save"),

    _r("/configuration", gateway.config_show, name="configuration"),
    _r("/configuration/apply", gateway.config_apply, methods=["POST"], name="configuration.apply"),
    _r("/configuration/rollback", gateway.config_rollback, methods=["POST"],
       name="configuration.rollback"),

    # -- Analytics ---------------------------------------------------------
    _r("/analytics", overview.analytics_overview, name="analytics"),
    _r("/analytics/requests", overview.analytics_requests, name="analytics.requests"),
    _r("/analytics/routes", overview.analytics_routes, name="analytics.routes"),
    _r("/analytics/routes/{route_id:int}", overview.route_analytics, name="analytics.route"),
    _r("/analytics/clients", overview.analytics_clients, name="analytics.clients"),
    _r("/analytics/errors", overview.analytics_errors, name="analytics.errors"),
    _r("/analytics/performance", overview.analytics_performance, name="analytics.performance"),
    _r("/analytics/bandwidth", overview.analytics_bandwidth, name="analytics.bandwidth"),

    # -- Security ----------------------------------------------------------
    _r("/security", security.security_overview, name="security"),
    _r("/security/clients", security.clients, name="security.clients"),
    _r("/security/clients/{client_id:int}", security.client_detail, name="security.client"),
    _r("/security/blocklist", security.blocklist, name="security.blocklist"),
    _r("/security/allowlist", security.allowlist, name="security.allowlist"),
    _r("/security/rules/save", security.block_create, methods=["POST"], name="security.rules.save"),
    _r("/security/rules/delete", security.block_remove, methods=["POST"],
       name="security.rules.delete"),
    _r("/security/rate-limits", security.ratelimits, name="security.ratelimits"),
    _r("/security/rate-limits/save", security.ratelimit_save, methods=["POST"],
       name="security.ratelimits.save"),
    _r("/security/rate-limits/delete", security.ratelimit_delete, methods=["POST"],
       name="security.ratelimits.delete"),
    _r("/security/api-keys", security.apikeys_index, name="security.apikeys"),
    _r("/security/api-keys/create", security.apikey_create, methods=["POST"],
       name="security.apikeys.create"),
    _r("/security/api-keys/rotate", security.apikey_rotate, methods=["POST"],
       name="security.apikeys.rotate"),
    _r("/security/api-keys/revoke", security.apikey_revoke, methods=["POST"],
       name="security.apikeys.revoke"),
    _r("/security/policies", security.policies, name="security.policies"),
    _r("/security/policies/save", security.policy_save, methods=["POST"],
       name="security.policies.save"),
    _r("/security/policies/delete", security.policy_delete, methods=["POST"],
       name="security.policies.delete"),

    # -- Team --------------------------------------------------------------
    _r("/team/users", team.users, name="team.users"),
    _r("/team/users/invite", team.user_invite, methods=["POST"], name="team.users.invite"),
    _r("/team/users/toggle", team.user_toggle, methods=["POST"], name="team.users.toggle"),
    _r("/team/users/role", team.user_role, methods=["POST"], name="team.users.role"),
    _r("/team/sessions/revoke", team.session_revoke, methods=["POST"], name="team.sessions.revoke"),
    _r("/team/roles", team.roles, name="team.roles"),
    _r("/team/roles/save", team.role_save, methods=["POST"], name="team.roles.save"),
    _r("/team/roles/permissions", team.role_permissions, methods=["POST"],
       name="team.roles.permissions"),
    _r("/team/audit", team.audit_log, name="team.audit"),

    # -- Documentation -----------------------------------------------------
    _r("/docs", docs.docs_index, name="docs"),
    _r("/docs/search", docs.docs_search, name="docs.search"),
    _r("/docs/{slug:str}", docs.docs_page, name="docs.page"),

    # -- System ------------------------------------------------------------
    _r("/system/deployments", gateway.deployments, name="system.deployments"),
    _r("/system/health", team.health, name="system.health"),
    _r("/system/modules", team.modules, name="system.modules"),
    _r("/system/modules/declare", team.modules_declare, methods=["POST"],
       name="system.modules.declare"),
    _r("/system/settings", team.settings, name="system.settings"),
]
