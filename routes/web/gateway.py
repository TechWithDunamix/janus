"""Gateway, route, upstream, domain and configuration management.

Every mutation here follows the same shape: authorise (the route's gate did
it), validate, write the desired state, audit, and flash. None of them talks to
Caddy — deploying is a separate, explicit act, because an operator editing four
routes should decide when the gateway sees them, and because a deployment is a
version in the history rather than a side effect of a form submission.
"""

from __future__ import annotations

import re
from typing import Any

from sillo.core.http import HttpContext
from sillo.responses import not_found
from sillo_inertia import back, defer, redirect, render, set_flash

from app.gateways import current_gateway, select_gateway
from app.services import audit
from app.services import configuration as config_service
from database.models import (
    ConfigVersion,
    Deployment,
    Domain,
    Gateway,
    GatewayRoute,
    Upstream,
    UpstreamTarget,
)
from routes.web._kit import action, client_ip, form, page

__all__ = [
    "config_apply",
    "config_show",
    "config_rollback",
    "deployments",
    "domain_save",
    "domains",
    "gateway_maintenance",
    "gateway_save",
    "gateways",
    "route_delete",
    "route_edit",
    "route_save",
    "route_toggle",
    "routes_index",
    "switch_gateway",
    "upstream_delete",
    "upstream_edit",
    "upstream_save",
    "upstreams",
]

ROUTE_FIELDS = (
    "name", "slug", "path", "methods", "priority", "enabled", "action",
    "upstream_id", "domain_id", "auth_policy", "timeout_seconds", "retries",
    "strip_prefix", "rewrite_to", "redirect_to", "canary_percent", "maintenance",
)
UPSTREAM_FIELDS = (
    "name", "slug", "policy", "max_fails", "fail_duration_seconds", "health_path",
    "health_interval_seconds", "dial_timeout_seconds", "enabled", "maintenance",
)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or "item"


def _int(data: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(data.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _bool(data: dict[str, Any], key: str, default: bool = False) -> bool:
    value = data.get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Gateways
# ---------------------------------------------------------------------------


@page("gateway.read")
async def gateways(ctx: HttpContext) -> Any:
    rows = await Gateway.all().order_by("name")
    return await render(
        "gateway/Gateways",
        {
            "gateways": [
                {
                    "id": g.pk, "name": g.name, "slug": g.slug, "region": g.region,
                    "listen": g.listen, "enabled": g.enabled, "maintenance": g.maintenance,
                    "sync_state": g.sync_state, "sync_error": g.sync_error,
                    "simulated": g.simulated, "caddy_version": g.caddy_version,
                    "admin_url": g.admin_url,
                    "last_synced_at": g.last_synced_at.isoformat() if g.last_synced_at else None,
                    "routes": await GatewayRoute.filter(gateway_id=g.pk).count(),
                    "upstreams": await Upstream.filter(gateway_id=g.pk).count(),
                }
                for g in rows
            ]
        },
    )


@action("gateway.write")
async def gateway_save(ctx: HttpContext) -> Any:
    data = await form(ctx)
    gateway_id = _int(data, "id")
    name = (data.get("name") or "").strip()
    if not name:
        set_flash(ctx, "error", "A gateway needs a name.")
        return back(fallback="/gateways", ctx=ctx)

    existing = await Gateway.get_or_none(pk=gateway_id) if gateway_id else None
    before = audit.snapshot(existing, ("name", "listen", "admin_url", "enabled", "region"))

    values = {
        "name": name,
        "slug": _slugify(data.get("slug") or name),
        "description": data.get("description") or None,
        "listen": (data.get("listen") or ":8080").strip(),
        "admin_url": (data.get("admin_url") or "").strip() or None,
        "region": (data.get("region") or "").strip() or None,
        "enabled": _bool(data, "enabled", True),
    }

    if existing is None:
        gateway = await Gateway.create(**values)
        act = "gateway.created"
    else:
        for key, value in values.items():
            setattr(existing, key, value)
        await existing.save()
        gateway = existing
        act = "gateway.updated"

    await audit.record(
        action=act, resource_type="gateway", resource_id=gateway.pk,
        resource_label=gateway.name, actor=ctx.state.auth_user, before=before,
        after=audit.snapshot(gateway, ("name", "listen", "admin_url", "enabled", "region")),
        ip=client_ip(ctx),
    )
    set_flash(ctx, "success", f"Gateway “{gateway.name}” saved.")
    return back(fallback="/gateways", ctx=ctx)


@action("gateway.write")
async def gateway_maintenance(ctx: HttpContext) -> Any:
    """Put a gateway into or out of maintenance, and deploy immediately.

    The one mutation that deploys on its own. Maintenance mode is an emergency
    control — an operator turning it on is responding to something now, and
    making them click Deploy afterwards would be a second step at the worst
    possible moment.
    """
    data = await form(ctx)
    gateway = await Gateway.get_or_none(pk=_int(data, "id"))
    if gateway is None:
        return not_found()

    before = audit.snapshot(gateway, ("maintenance", "maintenance_status"))
    gateway.maintenance = _bool(data, "maintenance")
    if data.get("maintenance_body"):
        gateway.maintenance_body = data["maintenance_body"]
    gateway.maintenance_status = _int(data, "maintenance_status", 503)
    await gateway.save()

    result = await config_service.deploy(
        gateway,
        actor=ctx.state.auth_user,
        note="Maintenance mode " + ("enabled" if gateway.maintenance else "disabled"),
        ip=client_ip(ctx),
        force=True,
    )
    await audit.record(
        action="gateway.maintenance", resource_type="gateway", resource_id=gateway.pk,
        resource_label=gateway.name, actor=ctx.state.auth_user, before=before,
        after=audit.snapshot(gateway, ("maintenance", "maintenance_status")),
        ip=client_ip(ctx),
    )
    if result.ok:
        set_flash(
            ctx, "success",
            "Maintenance mode is on." if gateway.maintenance else "Maintenance mode is off.",
        )
    else:
        set_flash(ctx, "error", f"Saved, but the gateway was not updated: {result.error}")
    return back(fallback="/gateways", ctx=ctx)


@action("gateway.read")
async def switch_gateway(ctx: HttpContext) -> Any:
    data = await form(ctx)
    await select_gateway(ctx, _int(data, "gateway_id"))
    return back(fallback="/", ctx=ctx)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@page("routes.read")
async def routes_index(ctx: HttpContext) -> Any:
    gateway = await current_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})

    rows = (
        await GatewayRoute.filter(gateway_id=gateway.pk)
        .order_by("-priority", "id")
        .prefetch_related("upstream", "domain")
    )
    return await render(
        "gateway/Routes",
        {
            "routes": [_route_dict(r) for r in rows],
            "upstreams": await _upstream_options(gateway),
            "domains": await _domain_options(gateway),
            "traffic": defer(lambda _: _route_traffic(gateway), "traffic"),
        },
    )


def _route_dict(route: GatewayRoute) -> dict[str, Any]:
    return {
        "id": route.pk,
        "name": route.name,
        "slug": route.slug,
        "description": route.description,
        "path": route.path,
        "methods": route.methods,
        "method_label": route.method_label,
        "priority": route.priority,
        "enabled": route.enabled,
        "maintenance": route.maintenance,
        "action": route.action,
        "upstream_id": route.upstream_id,
        "upstream": route.upstream.name if route.upstream else None,
        "canary_upstream_id": route.canary_upstream_id,
        "canary_percent": route.canary_percent,
        "domain_id": route.domain_id,
        "domain": route.domain.hostname if route.domain else None,
        "auth_policy": route.auth_policy,
        "auth_requirement": route.auth_requirement,
        "timeout_seconds": route.timeout_seconds,
        "retries": route.retries,
        "strip_prefix": route.strip_prefix,
        "rewrite_to": route.rewrite_to,
        "redirect_to": route.redirect_to,
        "redirect_status": route.redirect_status,
        "static_body": route.static_body,
        "static_status": route.static_status,
        "request_headers": route.request_headers or {},
        "response_headers": route.response_headers or {},
        "remove_request_headers": route.remove_request_headers or [],
        "remove_response_headers": route.remove_response_headers or [],
    }


def _blank_route() -> dict[str, Any]:
    """The defaults a new route starts from.

    Mirrors the model's own defaults rather than inventing a second set, so a
    route created through the form and one created through the API begin
    identically.
    """
    return {
        "id": 0, "name": "", "slug": "", "description": None, "path": "/*",
        "methods": "", "method_label": "ANY", "priority": 0, "enabled": True,
        "maintenance": False, "action": "proxy", "upstream_id": None, "upstream": None,
        "canary_upstream_id": None, "canary_percent": 0, "domain_id": None, "domain": None,
        "auth_policy": "public", "auth_requirement": None, "timeout_seconds": 30,
        "read_timeout_seconds": 0, "retries": 0, "strip_prefix": None, "rewrite_to": None,
        "redirect_to": None, "redirect_status": 302, "static_body": None, "static_status": 200,
        "request_headers": {}, "response_headers": {},
        "remove_request_headers": [], "remove_response_headers": [],
    }


async def _route_traffic(gateway: Gateway) -> dict[str, Any]:
    """Per-route request counts for the last hour, for the sparkline column."""
    from app.services import analytics

    window = analytics.range_bounds("1h")
    table = await analytics.route_table(gateway, window)
    return {str(row["id"]): row for row in table if row["id"] is not None}


@page("routes.read")
async def route_edit(ctx: HttpContext, route_id: int) -> Any:
    """The route form, for an existing route or a new one.

    `/routes/0` is the new-route form. A separate `/routes/new` path was the
    obvious alternative and was not taken: the form, its validation and its
    submit target are identical either way, and two routes rendering the same
    component is two places for them to drift apart.
    """
    gateway = await current_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})

    route = (
        await GatewayRoute.get_or_none(pk=route_id, gateway_id=gateway.pk) if route_id else None
    )
    if route_id and route is None:
        return not_found()
    if route is not None:
        await route.fetch_related("upstream", "domain")

    return await render(
        "gateway/RouteEdit",
        {
            "route": _route_dict(route) if route is not None else _blank_route(),
            "upstreams": await _upstream_options(gateway),
            "domains": await _domain_options(gateway),
        },
    )


@action("routes.write")
async def route_save(ctx: HttpContext) -> Any:
    data = await form(ctx)
    gateway = await current_gateway(ctx)
    if gateway is None:
        return not_found()

    route_id = _int(data, "id")
    name = (data.get("name") or "").strip()
    path = (data.get("path") or "/*").strip()
    if not name:
        set_flash(ctx, "error", "A route needs a name.")
        return back(fallback="/routes", ctx=ctx)
    if not path.startswith("/"):
        set_flash(ctx, "error", "A route path must begin with a slash.")
        return back(fallback="/routes", ctx=ctx)

    existing = await GatewayRoute.get_or_none(pk=route_id, gateway_id=gateway.pk) if route_id else None
    before = audit.snapshot(existing, ROUTE_FIELDS)

    slug = _slugify(data.get("slug") or name)
    clash = await GatewayRoute.get_or_none(gateway_id=gateway.pk, slug=slug)
    if clash is not None and (existing is None or clash.pk != existing.pk):
        slug = f"{slug}-{await GatewayRoute.filter(gateway_id=gateway.pk).count() + 1}"

    values: dict[str, Any] = {
        "name": name,
        "slug": slug,
        "description": data.get("description") or None,
        "path": path,
        "methods": (data.get("methods") or "").strip().upper(),
        "priority": _int(data, "priority"),
        "enabled": _bool(data, "enabled", True),
        "maintenance": _bool(data, "maintenance"),
        "action": (data.get("action") or "proxy").strip(),
        "upstream_id": _int(data, "upstream_id") or None,
        "canary_upstream_id": _int(data, "canary_upstream_id") or None,
        "canary_percent": max(0, min(100, _int(data, "canary_percent"))),
        "domain_id": _int(data, "domain_id") or None,
        "auth_policy": (data.get("auth_policy") or "public").strip(),
        "auth_requirement": (data.get("auth_requirement") or "").strip() or None,
        "timeout_seconds": _int(data, "timeout_seconds", 30),
        "read_timeout_seconds": _int(data, "read_timeout_seconds"),
        "retries": _int(data, "retries"),
        "strip_prefix": (data.get("strip_prefix") or "").strip() or None,
        "rewrite_to": (data.get("rewrite_to") or "").strip() or None,
        "redirect_to": (data.get("redirect_to") or "").strip() or None,
        "redirect_status": _int(data, "redirect_status", 302),
        "static_body": data.get("static_body") or None,
        "static_status": _int(data, "static_status", 200),
        "request_headers": data.get("request_headers") or {},
        "response_headers": data.get("response_headers") or {},
        "remove_request_headers": data.get("remove_request_headers") or [],
        "remove_response_headers": data.get("remove_response_headers") or [],
    }

    if existing is None:
        route = await GatewayRoute.create(gateway_id=gateway.pk, **values)
        act = "route.created"
    else:
        for key, value in values.items():
            setattr(existing, key, value)
        await existing.save()
        route = existing
        act = "route.updated"

    await audit.record(
        action=act, resource_type="route", resource_id=route.pk,
        resource_label=f"{route.method_label} {route.path}", actor=ctx.state.auth_user,
        before=before, after=audit.snapshot(route, ROUTE_FIELDS), ip=client_ip(ctx),
    )
    set_flash(ctx, "success", f"Route “{route.name}” saved. Deploy to apply it.")
    return redirect("/routes")


@action("routes.write")
async def route_toggle(ctx: HttpContext) -> Any:
    data = await form(ctx)
    gateway = await current_gateway(ctx)
    route = await GatewayRoute.get_or_none(pk=_int(data, "id"), gateway_id=gateway.pk if gateway else 0)
    if route is None:
        return not_found()

    before = audit.snapshot(route, ROUTE_FIELDS)
    route.enabled = not route.enabled
    await route.save()
    await audit.record(
        action="route.enabled" if route.enabled else "route.disabled",
        resource_type="route", resource_id=route.pk,
        resource_label=f"{route.method_label} {route.path}", actor=ctx.state.auth_user,
        before=before, after=audit.snapshot(route, ROUTE_FIELDS), ip=client_ip(ctx),
    )
    set_flash(
        ctx, "success",
        f"Route “{route.name}” {'enabled' if route.enabled else 'disabled'}. Deploy to apply it.",
    )
    return back(fallback="/routes", ctx=ctx)


@action("routes.write")
async def route_delete(ctx: HttpContext) -> Any:
    data = await form(ctx)
    gateway = await current_gateway(ctx)
    route = await GatewayRoute.get_or_none(pk=_int(data, "id"), gateway_id=gateway.pk if gateway else 0)
    if route is None:
        return not_found()

    await audit.record(
        action="route.deleted", resource_type="route", resource_id=route.pk,
        resource_label=f"{route.method_label} {route.path}", actor=ctx.state.auth_user,
        before=audit.snapshot(route, ROUTE_FIELDS), ip=client_ip(ctx),
    )
    name = route.name
    await route.delete()
    set_flash(ctx, "success", f"Route “{name}” deleted. Deploy to apply it.")
    return redirect("/routes")


# ---------------------------------------------------------------------------
# Upstreams
# ---------------------------------------------------------------------------


async def _upstream_options(gateway: Gateway | None) -> list[dict[str, Any]]:
    if gateway is None:
        return []
    return [
        {"id": u.pk, "name": u.name, "slug": u.slug, "enabled": u.enabled}
        for u in await Upstream.filter(gateway_id=gateway.pk).order_by("name")
    ]


async def _domain_options(gateway: Gateway | None) -> list[dict[str, Any]]:
    if gateway is None:
        return []
    return [
        {"id": d.pk, "hostname": d.hostname, "tls_mode": d.tls_mode, "enabled": d.enabled}
        for d in await Domain.filter(gateway_id=gateway.pk).order_by("hostname")
    ]


@page("upstreams.read")
async def upstreams(ctx: HttpContext) -> Any:
    gateway = await current_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})

    rows = await Upstream.filter(gateway_id=gateway.pk).order_by("name").prefetch_related("targets")
    return await render(
        "gateway/Upstreams",
        {
            "upstreams": [
                {
                    "id": u.pk, "name": u.name, "slug": u.slug, "description": u.description,
                    "policy": u.policy, "enabled": u.enabled, "maintenance": u.maintenance,
                    "max_fails": u.max_fails, "fail_duration_seconds": u.fail_duration_seconds,
                    "health_path": u.health_path,
                    "health_interval_seconds": u.health_interval_seconds,
                    "health_timeout_seconds": u.health_timeout_seconds,
                    "health_expect_status": u.health_expect_status,
                    "dial_timeout_seconds": u.dial_timeout_seconds,
                    "max_connections": u.max_connections,
                    "route_count": await GatewayRoute.filter(upstream_id=u.pk).count(),
                    "targets": [
                        {
                            "id": t.pk, "dial": t.dial, "weight": t.weight,
                            "enabled": t.enabled, "healthy": t.healthy,
                            "last_error": t.last_error,
                            "last_checked_at": (
                                t.last_checked_at.isoformat() if t.last_checked_at else None
                            ),
                        }
                        for t in sorted(u.targets, key=lambda t: t.pk)
                    ],
                }
                for u in rows
            ]
        },
    )


@page("upstreams.read")
async def upstream_edit(ctx: HttpContext, upstream_id: int) -> Any:
    """The upstream form. `/upstreams/0` is the new-upstream case."""
    gateway = await current_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})

    upstream = (
        await Upstream.get_or_none(pk=upstream_id, gateway_id=gateway.pk) if upstream_id else None
    )
    if upstream_id and upstream is None:
        return not_found()
    if upstream is None:
        return await render("gateway/UpstreamEdit", {"upstream": _blank_upstream()})

    await upstream.fetch_related("targets")
    return await render(
        "gateway/UpstreamEdit",
        {
            "upstream": {
                "id": upstream.pk, "name": upstream.name, "slug": upstream.slug,
                "description": upstream.description, "policy": upstream.policy,
                "enabled": upstream.enabled, "maintenance": upstream.maintenance,
                "max_fails": upstream.max_fails,
                "fail_duration_seconds": upstream.fail_duration_seconds,
                "health_path": upstream.health_path,
                "health_interval_seconds": upstream.health_interval_seconds,
                "health_timeout_seconds": upstream.health_timeout_seconds,
                "health_expect_status": upstream.health_expect_status,
                "dial_timeout_seconds": upstream.dial_timeout_seconds,
                "max_connections": upstream.max_connections,
                "targets": [
                    {"id": t.pk, "dial": t.dial, "weight": t.weight, "enabled": t.enabled}
                    for t in sorted(upstream.targets, key=lambda t: t.pk)
                ],
            }
        },
    )


def _blank_upstream() -> dict[str, Any]:
    """Defaults for a new upstream, mirroring the model's own."""
    return {
        "id": 0, "name": "", "slug": "", "description": None,
        "policy": "weighted_round_robin", "enabled": True, "maintenance": False,
        "max_fails": 3, "fail_duration_seconds": 30, "health_path": None,
        "health_interval_seconds": 30, "health_timeout_seconds": 5,
        "health_expect_status": 200, "dial_timeout_seconds": 10,
        "max_connections": 0, "targets": [],
    }


@action("upstreams.write")
async def upstream_save(ctx: HttpContext) -> Any:
    data = await form(ctx)
    gateway = await current_gateway(ctx)
    if gateway is None:
        return not_found()

    upstream_id = _int(data, "id")
    name = (data.get("name") or "").strip()
    if not name:
        set_flash(ctx, "error", "An upstream needs a name.")
        return back(fallback="/upstreams", ctx=ctx)

    existing = (
        await Upstream.get_or_none(pk=upstream_id, gateway_id=gateway.pk) if upstream_id else None
    )
    before = audit.snapshot(existing, UPSTREAM_FIELDS)

    values = {
        "name": name,
        "slug": _slugify(data.get("slug") or name),
        "description": data.get("description") or None,
        "policy": (data.get("policy") or "weighted_round_robin").strip(),
        "max_fails": _int(data, "max_fails", 3),
        "fail_duration_seconds": _int(data, "fail_duration_seconds", 30),
        "health_path": (data.get("health_path") or "").strip() or None,
        "health_interval_seconds": _int(data, "health_interval_seconds", 30),
        "health_timeout_seconds": _int(data, "health_timeout_seconds", 5),
        "health_expect_status": _int(data, "health_expect_status", 200),
        "dial_timeout_seconds": _int(data, "dial_timeout_seconds", 10),
        "max_connections": _int(data, "max_connections"),
        "enabled": _bool(data, "enabled", True),
        "maintenance": _bool(data, "maintenance"),
    }

    if existing is None:
        upstream = await Upstream.create(gateway_id=gateway.pk, **values)
        act = "upstream.created"
    else:
        for key, value in values.items():
            setattr(existing, key, value)
        await existing.save()
        upstream = existing
        act = "upstream.updated"

    # Targets arrive as a list of objects and are replaced wholesale. A diffing
    # merge was tried and thrown away: the identity of a target is its dial
    # address, so an edit that changes the address is indistinguishable from a
    # delete plus an add, and treating it as an update silently kept the old
    # one in rotation.
    targets = data.get("targets")
    if isinstance(targets, list):
        await UpstreamTarget.filter(upstream_id=upstream.pk).delete()
        for entry in targets:
            dial = (entry.get("dial") or "").strip()
            if not dial:
                continue
            await UpstreamTarget.create(
                upstream_id=upstream.pk,
                dial=dial,
                weight=max(1, int(entry.get("weight") or 1)),
                enabled=bool(entry.get("enabled", True)),
            )

    await audit.record(
        action=act, resource_type="upstream", resource_id=upstream.pk,
        resource_label=upstream.name, actor=ctx.state.auth_user, before=before,
        after=audit.snapshot(upstream, UPSTREAM_FIELDS), ip=client_ip(ctx),
    )
    set_flash(ctx, "success", f"Upstream “{upstream.name}” saved. Deploy to apply it.")
    return redirect("/upstreams")


@action("upstreams.write")
async def upstream_delete(ctx: HttpContext) -> Any:
    data = await form(ctx)
    gateway = await current_gateway(ctx)
    upstream = await Upstream.get_or_none(pk=_int(data, "id"), gateway_id=gateway.pk if gateway else 0)
    if upstream is None:
        return not_found()

    using = await GatewayRoute.filter(upstream_id=upstream.pk).count()
    if using:
        set_flash(
            ctx, "error",
            f"“{upstream.name}” is used by {using} route(s). Repoint them first.",
        )
        return back(fallback="/upstreams", ctx=ctx)

    await audit.record(
        action="upstream.deleted", resource_type="upstream", resource_id=upstream.pk,
        resource_label=upstream.name, actor=ctx.state.auth_user,
        before=audit.snapshot(upstream, UPSTREAM_FIELDS), ip=client_ip(ctx),
    )
    name = upstream.name
    await upstream.delete()
    set_flash(ctx, "success", f"Upstream “{name}” deleted.")
    return back(fallback="/upstreams", ctx=ctx)


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------


@page("domains.read")
async def domains(ctx: HttpContext) -> Any:
    gateway = await current_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})

    rows = await Domain.filter(gateway_id=gateway.pk).order_by("hostname")
    return await render(
        "gateway/Domains",
        {
            "domains": [
                {
                    "id": d.pk, "hostname": d.hostname, "tls_mode": d.tls_mode,
                    "tls_status": d.tls_status, "tls_issuer": d.tls_issuer,
                    "tls_expires_at": d.tls_expires_at.isoformat() if d.tls_expires_at else None,
                    "enabled": d.enabled,
                    "route_count": await GatewayRoute.filter(domain_id=d.pk).count(),
                }
                for d in rows
            ],
            "traffic": defer(lambda _: _domain_traffic(gateway), "traffic"),
        },
    )


async def _domain_traffic(gateway: Gateway) -> dict[str, Any]:
    from app.services import analytics

    window = analytics.range_bounds("24h")
    breakdown = await analytics.traffic_breakdown(gateway, window)
    return {row["label"]: row for row in breakdown["by_domain"]}


@action("domains.write")
async def domain_save(ctx: HttpContext) -> Any:
    data = await form(ctx)
    gateway = await current_gateway(ctx)
    if gateway is None:
        return not_found()

    hostname = (data.get("hostname") or "").strip().lower()
    if not hostname:
        set_flash(ctx, "error", "A domain needs a hostname.")
        return back(fallback="/domains", ctx=ctx)

    domain_id = _int(data, "id")
    existing = await Domain.get_or_none(pk=domain_id, gateway_id=gateway.pk) if domain_id else None
    before = audit.snapshot(existing, ("hostname", "tls_mode", "enabled"))

    values = {
        "hostname": hostname,
        "tls_mode": (data.get("tls_mode") or "auto").strip(),
        "enabled": _bool(data, "enabled", True),
    }
    if existing is None:
        domain = await Domain.create(gateway_id=gateway.pk, **values)
        act = "domain.created"
    else:
        for key, value in values.items():
            setattr(existing, key, value)
        await existing.save()
        domain = existing
        act = "domain.updated"

    await audit.record(
        action=act, resource_type="domain", resource_id=domain.pk,
        resource_label=domain.hostname, actor=ctx.state.auth_user, before=before,
        after=audit.snapshot(domain, ("hostname", "tls_mode", "enabled")), ip=client_ip(ctx),
    )
    set_flash(ctx, "success", f"Domain “{domain.hostname}” saved. Deploy to apply it.")
    return back(fallback="/domains", ctx=ctx)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@page("configuration.read")
async def config_show(ctx: HttpContext) -> Any:
    """The generated configuration, its history, and the pending difference."""
    gateway = await current_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})

    payload, warnings = await config_service.generate(gateway)
    active = (
        await ConfigVersion.get_or_none(pk=gateway.active_version_id)
        if gateway.active_version_id
        else None
    )
    diff = config_service.diff_versions(active.payload if active else None, payload)

    return await render(
        "gateway/Configuration",
        {
            "generated": payload,
            "warnings": warnings,
            "diff": diff,
            "has_changes": bool(diff),
            "active": (
                {
                    "version": active.version,
                    "checksum": active.checksum[:12],
                    "activated_at": active.activated_at.isoformat() if active.activated_at else None,
                    "summary": active.summary,
                }
                if active
                else None
            ),
            "sync_state": gateway.sync_state,
            "sync_error": gateway.sync_error,
            "history": defer(lambda _: _history_prop(gateway), "history"),
        },
    )


async def _history_prop(gateway: Gateway) -> list[dict[str, Any]]:
    rows = await config_service.history(gateway, limit=50)
    return [
        {
            "id": v.pk, "version": v.version, "status": v.status, "summary": v.summary,
            "note": v.note, "checksum": v.checksum[:12], "origin": v.origin,
            "route_count": v.route_count, "upstream_count": v.upstream_count,
            "author": v.author.email if v.author else "system",
            "created_at": v.created_at.isoformat(),
            "activated_at": v.activated_at.isoformat() if v.activated_at else None,
            "validation_error": v.validation_error,
        }
        for v in rows
    ]


@action("configuration.write")
async def config_apply(ctx: HttpContext) -> Any:
    """Run the full pipeline."""
    gateway = await current_gateway(ctx)
    if gateway is None:
        return not_found()

    data = await form(ctx)
    result = await config_service.deploy(
        gateway,
        actor=ctx.state.auth_user,
        origin="web",
        note=(data.get("note") or "").strip() or None,
        ip=client_ip(ctx),
        force=_bool(data, "force"),
    )

    if result.unchanged:
        set_flash(ctx, "info", "The gateway is already running this configuration.")
    elif result.ok:
        message = f"Deployed v{result.version.version}."
        if result.warnings:
            message += f" {len(result.warnings)} warning(s)."
        set_flash(ctx, "success", message)
    else:
        set_flash(
            ctx, "error",
            f"Deployment failed at {result.stage}: {result.error} "
            "The gateway is still running its previous configuration.",
        )
    return back(fallback="/configuration", ctx=ctx)


@action("configuration.rollback")
async def config_rollback(ctx: HttpContext) -> Any:
    gateway = await current_gateway(ctx)
    if gateway is None:
        return not_found()

    data = await form(ctx)
    result = await config_service.rollback(
        gateway, _int(data, "version"), actor=ctx.state.auth_user, ip=client_ip(ctx)
    )
    if result.ok:
        set_flash(ctx, "success", f"Rolled back — now running v{result.version.version}.")
    else:
        set_flash(ctx, "error", f"Rollback failed: {result.error}")
    return back(fallback="/configuration", ctx=ctx)


@page("configuration.read")
async def deployments(ctx: HttpContext) -> Any:
    gateway = await current_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})

    rows = (
        await Deployment.filter(gateway_id=gateway.pk)
        .order_by("-started_at")
        .limit(100)
        .prefetch_related("version", "actor")
    )
    return await render(
        "system/Deployments",
        {
            "deployments": [
                {
                    "id": d.pk,
                    "version": d.version.version if d.version else None,
                    "summary": d.version.summary if d.version else None,
                    "status": d.status, "stage": d.stage, "error": d.error,
                    "origin": d.origin, "rolled_back": d.rolled_back,
                    "actor": d.actor.email if d.actor else "system",
                    "started_at": d.started_at.isoformat(),
                    "finished_at": d.finished_at.isoformat() if d.finished_at else None,
                    "duration_ms": d.duration_ms,
                }
                for d in rows
            ]
        },
    )
