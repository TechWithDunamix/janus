"""The JSON API.

Two audiences, one implementation. The dashboard uses Inertia and does not need
this; what does is the CLI, and anything else automating Janus. Every endpoint
here goes through the same permission check the dashboard's routes use — the
CLI authenticates with a bearer token, resolves to a real user, and is then
subject to exactly the same `require()` call.

That is what stops the CLI from being an RBAC bypass, and it is structural
rather than a convention: there is no code path in this file that performs an
operation without first calling `require`.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sillo.core.http import HttpContext
from sillo.core.routing import Route, Router
from sillo.responses import json

from app.authz import PermissionDenied, permissions_of, require, role_names_of
from app.config import config
from app.gateways import gateway_options
from app.services import analytics, apikeys, audit, security
from app.services import configuration as config_service
from database.models import (
    ApiKey,
    AuditEvent,
    ConfigVersion,
    Deployment,
    Domain,
    Gateway,
    GatewayRoute,
    IpRule,
    LoginEvent,
    RateLimit,
    Upstream,
    UpstreamTarget,
    User,
    UserSession,
)

__all__ = ["router"]

router = Router(prefix="/api")


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class ApiError(Exception):
    """A failure with a status code, so handlers can raise rather than return."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


async def actor_for(ctx: HttpContext) -> User:
    """Resolve the caller from a bearer token, or refuse.

    The token is a :class:`UserSession` of kind `cli`, stored as a digest. It
    is looked up by digest and checked for liveness on *every* request, so
    revoking a CLI session takes effect immediately rather than whenever it
    would have expired.

    Raises:
        ApiError: 401 when there is no valid token.
    """
    header = ctx.headers.get("authorization") or ""
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token:
        raise ApiError("Authentication required. Run: janus login", 401)

    digest = hashlib.sha256(token.encode()).hexdigest()
    session = await UserSession.get_or_none(key_hash=digest)
    if session is None or not session.is_live:
        raise ApiError("Authentication required. Run: janus login", 401)
    # `fetch_related` rather than chaining `prefetch_related` off `get_or_none`:
    # Sillo overrides that classmethod as an `async def` returning the instance,
    # not Tortoise's chainable QuerySetSingle, so the chained form raises
    # "'coroutine' object has no attribute 'prefetch_related'" at runtime.
    await session.fetch_related("user")
    if session.user is None:
        raise ApiError("Authentication required. Run: janus login", 401)
    if not session.user.is_active:
        raise ApiError("This account is disabled.", 403)

    session.last_seen_at = datetime.now(UTC)
    await session.save()
    return session.user


def endpoint(*permissions: str, methods: list[str] | None = None):
    """Register an authenticated, authorised JSON endpoint.

    The decorator is the enforcement point. A handler cannot be registered
    without going through it, and it always calls `require` before the handler
    body runs — so adding an endpoint without a permission is a visible,
    deliberate act (passing no permissions) rather than an omission.
    """

    def decorate(handler):
        async def wrapper(ctx: HttpContext, **kwargs: Any) -> Any:
            try:
                actor = await actor_for(ctx)
                await require(actor, *permissions)
                ctx.state.api_actor = actor
                return await handler(ctx, **kwargs)
            except ApiError as error:
                return json({"error": error.message}, status_code=error.status)
            except PermissionDenied as error:
                return json(
                    {
                        "error": f"You do not have permission to do that ({error.permission}).",
                        "permission": error.permission,
                    },
                    status_code=403,
                )

        wrapper.__name__ = handler.__name__
        wrapper._permissions = permissions
        return wrapper

    return decorate


async def _body(ctx: HttpContext) -> dict[str, Any]:
    try:
        return dict(await ctx.json)
    except (ValueError, TypeError):
        return {}


async def _gateway(ctx: HttpContext) -> Gateway:
    """The gateway named in the query, or the first enabled one."""
    requested = ctx.query_params.get("gateway")
    if requested:
        found = (
            await Gateway.get_or_none(pk=int(requested))
            if requested.isdigit()
            else await Gateway.get_or_none(slug=requested)
        )
        if found is None:
            raise ApiError(f"No gateway '{requested}'.", 404)
        return found
    first = await Gateway.filter(enabled=True).order_by("name").first()
    if first is None:
        raise ApiError("No gateways are configured.", 404)
    return first


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------


async def api_login(ctx: HttpContext) -> Any:
    """Exchange credentials for a CLI token.

    Deliberately unauthenticated — it is how authentication starts. Every
    attempt is recorded in `login_events` exactly as a browser sign-in is, so
    a brute-force against the CLI is as visible as one against the dashboard.
    """
    data = await _body(ctx)
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    ip = (ctx.headers.get("x-forwarded-for") or "").split(",")[0].strip() or None

    async def refuse(reason: str, user: User | None = None):
        await LoginEvent.create(
            user_id=getattr(user, "pk", None), email=email or "(blank)",
            successful=False, reason=reason, kind="cli", ip=ip,
        )
        return json({"error": "Those credentials do not match our records."}, status_code=401)

    user = await User.get_or_none(email=email)
    if user is None:
        return await refuse("unknown_user")
    if not user.is_active:
        return await refuse("disabled", user)
    if not user.check_password(password):
        return await refuse("bad_password", user)

    token = secrets.token_urlsafe(40)
    await UserSession.create(
        user_id=user.pk,
        key_hash=hashlib.sha256(token.encode()).hexdigest(),
        kind="cli",
        ip=ip,
        label=(data.get("label") or "janus CLI")[:120],
        expires_at=datetime.now(UTC) + timedelta(days=30),
        last_seen_at=datetime.now(UTC),
    )
    await user.set_last_login()
    await LoginEvent.create(
        user_id=user.pk, email=email, successful=True, reason="ok", kind="cli", ip=ip
    )
    await audit.record(
        action="user.signed_in", resource_type="user", resource_id=user.pk,
        resource_label=user.email, actor=user, origin="cli", ip=ip,
    )
    return json(
        {
            "token": token,
            "user": {"email": user.email, "name": user.name},
            "expires_in_days": 30,
        }
    )


@endpoint()
async def api_whoami(ctx: HttpContext) -> Any:
    actor = ctx.state.api_actor
    return json(
        {
            "email": actor.email,
            "name": actor.name,
            "is_superuser": actor.is_superuser,
            "roles": await role_names_of(actor),
            "permissions": sorted(await permissions_of(actor)),
        }
    )


@endpoint()
async def api_logout(ctx: HttpContext) -> Any:
    header = ctx.headers.get("authorization") or ""
    token = header[7:].strip()
    session = await UserSession.get_or_none(key_hash=hashlib.sha256(token.encode()).hexdigest())
    if session is not None:
        session.revoked_at = datetime.now(UTC)
        session.revoked_reason = "signed out from the CLI"
        await session.save()
    return json({"ok": True})


# ---------------------------------------------------------------------------
# Gateways
# ---------------------------------------------------------------------------


@endpoint("gateway.read")
async def api_gateways(ctx: HttpContext) -> Any:
    return json({"gateways": await gateway_options()})


@endpoint("gateway.read")
async def api_gateway_status(ctx: HttpContext) -> Any:
    from app.services.health import gateway_health

    gateway = await _gateway(ctx)
    return json({"gateway": gateway.name, "status": await gateway_health(gateway)})


@endpoint("gateway.write")
async def api_gateway_create(ctx: HttpContext) -> Any:
    data = await _body(ctx)
    name = (data.get("name") or "").strip()
    if not name:
        raise ApiError("A name is required.")
    slug = (data.get("slug") or name.lower().replace(" ", "-")).strip()
    gateway = await Gateway.create(
        name=name, slug=slug,
        listen=(data.get("listen") or ":8080"),
        admin_url=(data.get("admin_url") or "").strip() or None,
        region=(data.get("region") or "").strip() or None,
    )
    await audit.record(
        action="gateway.created", resource_type="gateway", resource_id=gateway.pk,
        resource_label=gateway.name, actor=ctx.state.api_actor, origin="cli",
    )
    return json({"id": gateway.pk, "name": gateway.name, "slug": gateway.slug}, status_code=201)


@endpoint("gateway.write")
async def api_gateway_delete(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    await audit.record(
        action="gateway.deleted", resource_type="gateway", resource_id=gateway.pk,
        resource_label=gateway.name, actor=ctx.state.api_actor, origin="cli",
    )
    name = gateway.name
    await gateway.delete()
    return json({"ok": True, "deleted": name})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _route_json(route: GatewayRoute) -> dict[str, Any]:
    return {
        "id": route.pk, "name": route.name, "slug": route.slug,
        "method": route.method_label, "path": route.path, "priority": route.priority,
        "enabled": route.enabled, "action": route.action,
        "upstream": route.upstream.name if route.upstream_id and route.upstream else None,
        "domain": route.domain.hostname if route.domain_id and route.domain else None,
        "auth_policy": route.auth_policy, "timeout_seconds": route.timeout_seconds,
        "retries": route.retries, "canary_percent": route.canary_percent,
        "maintenance": route.maintenance,
    }


@endpoint("routes.read")
async def api_routes(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    rows = (
        await GatewayRoute.filter(gateway_id=gateway.pk)
        .order_by("-priority", "id")
        .prefetch_related("upstream", "domain")
    )
    return json({"routes": [_route_json(r) for r in rows]})


@endpoint("routes.read")
async def api_route_show(ctx: HttpContext, route_id: int) -> Any:
    gateway = await _gateway(ctx)
    route = await GatewayRoute.get_or_none(pk=route_id, gateway_id=gateway.pk)
    if route is None:
        raise ApiError(f"No route {route_id}.", 404)
    await route.fetch_related("upstream", "domain")
    return json({"route": _route_json(route)})


@endpoint("routes.write")
async def api_route_create(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    data = await _body(ctx)
    name = (data.get("name") or "").strip()
    if not name:
        raise ApiError("A name is required.")

    upstream = None
    if data.get("upstream"):
        upstream = await Upstream.get_or_none(gateway_id=gateway.pk, slug=data["upstream"])
        if upstream is None:
            upstream = await Upstream.get_or_none(gateway_id=gateway.pk, name=data["upstream"])
        if upstream is None:
            raise ApiError(f"No upstream '{data['upstream']}'.", 404)

    route = await GatewayRoute.create(
        gateway_id=gateway.pk,
        name=name,
        slug=(data.get("slug") or name.lower().replace(" ", "-")).strip(),
        path=(data.get("path") or "/*").strip(),
        methods=(data.get("methods") or "").strip().upper(),
        priority=int(data.get("priority") or 0),
        upstream_id=upstream.pk if upstream else None,
        action=(data.get("action") or "proxy"),
        auth_policy=(data.get("auth_policy") or "public"),
        timeout_seconds=int(data.get("timeout_seconds") or 30),
        retries=int(data.get("retries") or 0),
        strip_prefix=(data.get("strip_prefix") or "").strip() or None,
        redirect_to=(data.get("redirect_to") or "").strip() or None,
    )
    await audit.record(
        action="route.created", resource_type="route", resource_id=route.pk,
        resource_label=f"{route.method_label} {route.path}", actor=ctx.state.api_actor,
        after=audit.snapshot(route, ("name", "path", "methods", "priority")), origin="cli",
    )
    await route.fetch_related("upstream", "domain")
    return json({"route": _route_json(route)}, status_code=201)


@endpoint("routes.write")
async def api_route_update(ctx: HttpContext, route_id: int) -> Any:
    gateway = await _gateway(ctx)
    route = await GatewayRoute.get_or_none(pk=route_id, gateway_id=gateway.pk)
    if route is None:
        raise ApiError(f"No route {route_id}.", 404)

    data = await _body(ctx)
    before = audit.snapshot(route, ("name", "path", "methods", "priority", "enabled"))
    for field in ("name", "path", "action", "auth_policy", "strip_prefix", "redirect_to"):
        if field in data:
            setattr(route, field, data[field])
    for field in ("priority", "timeout_seconds", "retries", "canary_percent"):
        if field in data:
            setattr(route, field, int(data[field] or 0))
    if "methods" in data:
        route.methods = (data["methods"] or "").upper()
    if "enabled" in data:
        route.enabled = bool(data["enabled"])
    await route.save()

    await audit.record(
        action="route.updated", resource_type="route", resource_id=route.pk,
        resource_label=f"{route.method_label} {route.path}", actor=ctx.state.api_actor,
        before=before,
        after=audit.snapshot(route, ("name", "path", "methods", "priority", "enabled")),
        origin="cli",
    )
    await route.fetch_related("upstream", "domain")
    return json({"route": _route_json(route)})


@endpoint("routes.write")
async def api_route_delete(ctx: HttpContext, route_id: int) -> Any:
    gateway = await _gateway(ctx)
    route = await GatewayRoute.get_or_none(pk=route_id, gateway_id=gateway.pk)
    if route is None:
        raise ApiError(f"No route {route_id}.", 404)
    await audit.record(
        action="route.deleted", resource_type="route", resource_id=route.pk,
        resource_label=f"{route.method_label} {route.path}", actor=ctx.state.api_actor,
        before=audit.snapshot(route, ("name", "path", "methods")), origin="cli",
    )
    label = f"{route.method_label} {route.path}"
    await route.delete()
    return json({"ok": True, "deleted": label})


@endpoint("routes.write")
async def api_route_toggle(ctx: HttpContext, route_id: int) -> Any:
    gateway = await _gateway(ctx)
    route = await GatewayRoute.get_or_none(pk=route_id, gateway_id=gateway.pk)
    if route is None:
        raise ApiError(f"No route {route_id}.", 404)
    data = await _body(ctx)
    route.enabled = bool(data.get("enabled", not route.enabled))
    await route.save()
    await audit.record(
        action="route.enabled" if route.enabled else "route.disabled",
        resource_type="route", resource_id=route.pk,
        resource_label=f"{route.method_label} {route.path}",
        actor=ctx.state.api_actor, origin="cli",
    )
    await route.fetch_related("upstream", "domain")
    return json({"route": _route_json(route)})


# ---------------------------------------------------------------------------
# Upstreams
# ---------------------------------------------------------------------------


@endpoint("upstreams.read")
async def api_upstreams(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    rows = await Upstream.filter(gateway_id=gateway.pk).order_by("name").prefetch_related("targets")
    return json(
        {
            "upstreams": [
                {
                    "id": u.pk, "name": u.name, "slug": u.slug, "policy": u.policy,
                    "enabled": u.enabled, "maintenance": u.maintenance,
                    "health_path": u.health_path,
                    "targets": [
                        {"dial": t.dial, "weight": t.weight, "enabled": t.enabled,
                         "healthy": t.healthy}
                        for t in sorted(u.targets, key=lambda t: t.pk)
                    ],
                }
                for u in rows
            ]
        }
    )


@endpoint("upstreams.write")
async def api_upstream_create(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    data = await _body(ctx)
    name = (data.get("name") or "").strip()
    if not name:
        raise ApiError("A name is required.")

    upstream = await Upstream.create(
        gateway_id=gateway.pk,
        name=name,
        slug=(data.get("slug") or name.lower().replace(" ", "-")).strip(),
        policy=(data.get("policy") or "weighted_round_robin"),
        health_path=(data.get("health_path") or "").strip() or None,
    )
    for entry in data.get("targets") or []:
        dial = (entry.get("dial") if isinstance(entry, dict) else str(entry)).strip()
        if dial:
            await UpstreamTarget.create(
                upstream_id=upstream.pk,
                dial=dial,
                weight=int(entry.get("weight", 1)) if isinstance(entry, dict) else 1,
            )
    await audit.record(
        action="upstream.created", resource_type="upstream", resource_id=upstream.pk,
        resource_label=upstream.name, actor=ctx.state.api_actor, origin="cli",
    )
    return json({"id": upstream.pk, "name": upstream.name}, status_code=201)


@endpoint("upstreams.write")
async def api_upstream_delete(ctx: HttpContext, upstream_id: int) -> Any:
    gateway = await _gateway(ctx)
    upstream = await Upstream.get_or_none(pk=upstream_id, gateway_id=gateway.pk)
    if upstream is None:
        raise ApiError(f"No upstream {upstream_id}.", 404)
    using = await GatewayRoute.filter(upstream_id=upstream.pk).count()
    if using:
        raise ApiError(f"{upstream.name} is used by {using} route(s).", 409)
    await audit.record(
        action="upstream.deleted", resource_type="upstream", resource_id=upstream.pk,
        resource_label=upstream.name, actor=ctx.state.api_actor, origin="cli",
    )
    name = upstream.name
    await upstream.delete()
    return json({"ok": True, "deleted": name})


@endpoint("upstreams.read")
async def api_upstream_health(ctx: HttpContext) -> Any:
    from app.services.health import refresh_upstream_health

    gateway = await _gateway(ctx)
    result = await refresh_upstream_health(gateway)
    targets = await UpstreamTarget.filter(upstream__gateway_id=gateway.pk).prefetch_related(
        "upstream"
    )
    return json(
        {
            "refresh": result,
            "targets": [
                {
                    "upstream": t.upstream.name, "dial": t.dial, "healthy": t.healthy,
                    "weight": t.weight, "enabled": t.enabled, "error": t.last_error,
                }
                for t in targets
            ],
        }
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@endpoint("configuration.read")
async def api_config_show(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    payload, warnings = await config_service.generate(gateway)
    return json({"config": payload, "warnings": warnings})


@endpoint("configuration.read")
async def api_config_validate(ctx: HttpContext) -> Any:
    from app.caddy.manager import manager_for
    from app.services.modules import build_for

    gateway = await _gateway(ctx)
    payload, warnings = await config_service.generate(gateway)
    build = await build_for(gateway)
    result = await manager_for(gateway).validate(
        payload, use_binary=build.source != "declared"
    )
    return json(
        {
            "ok": result.ok, "method": result.method, "error": result.error,
            "warnings": warnings + result.warnings,
        }
    )


@endpoint("configuration.read")
async def api_config_diff(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    payload, _ = await config_service.generate(gateway)
    active = (
        await ConfigVersion.get_or_none(pk=gateway.active_version_id)
        if gateway.active_version_id
        else None
    )
    diff = config_service.diff_versions(active.payload if active else None, payload)
    return json(
        {
            "diff": diff,
            "has_changes": bool(diff),
            "active_version": active.version if active else None,
        }
    )


@endpoint("configuration.write")
async def api_config_apply(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    data = await _body(ctx)
    result = await config_service.deploy(
        gateway,
        actor=ctx.state.api_actor,
        origin="cli",
        note=(data.get("note") or "").strip() or None,
        force=bool(data.get("force")),
    )
    return json(result.to_dict(), status_code=200 if result.ok else 422)


@endpoint("configuration.read")
async def api_config_history(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    rows = await config_service.history(gateway, limit=50)
    return json(
        {
            "versions": [
                {
                    "version": v.version, "status": v.status, "summary": v.summary,
                    "note": v.note, "origin": v.origin, "checksum": v.checksum[:12],
                    "routes": v.route_count,
                    "author": v.author.email if v.author else "system",
                    "created_at": v.created_at.isoformat(),
                    "activated_at": v.activated_at.isoformat() if v.activated_at else None,
                    "error": v.validation_error,
                }
                for v in rows
            ]
        }
    )


@endpoint("configuration.rollback")
async def api_config_rollback(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    data = await _body(ctx)
    version = int(data.get("version") or 0)
    result = await config_service.rollback(
        gateway, version, actor=ctx.state.api_actor, origin="cli"
    )
    return json(result.to_dict(), status_code=200 if result.ok else 422)


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


@endpoint("security.write")
async def api_security_block(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    data = await _body(ctx)
    minutes = int(data.get("minutes") or 0)
    try:
        rule = await security.block(
            gateway, data.get("cidr") or "", reason=data.get("reason") or "",
            duration=timedelta(minutes=minutes) if minutes else None,
            actor=ctx.state.api_actor, origin="cli",
        )
    except ValueError as error:
        raise ApiError(str(error)) from error
    return json({"ok": True, "cidr": rule.cidr, "action": "block"}, status_code=201)


@endpoint("security.write")
async def api_security_allow(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    data = await _body(ctx)
    try:
        rule = await security.allow(
            gateway, data.get("cidr") or "", reason=data.get("reason") or "",
            actor=ctx.state.api_actor, origin="cli",
        )
    except ValueError as error:
        raise ApiError(str(error)) from error
    return json({"ok": True, "cidr": rule.cidr, "action": "allow"}, status_code=201)


@endpoint("security.write")
async def api_security_unblock(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    data = await _body(ctx)
    try:
        removed = await security.unblock(
            gateway, data.get("cidr") or "", actor=ctx.state.api_actor, origin="cli"
        )
    except ValueError as error:
        raise ApiError(str(error)) from error
    return json({"ok": True, "removed": removed})


@endpoint("security.read")
async def api_security_rules(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    kind = ctx.query_params.get("action")
    query = IpRule.filter(gateway_id=gateway.pk)
    if kind in {"allow", "block"}:
        query = query.filter(action=kind)
    rows = await query.order_by("-created_at")
    return json(
        {
            "rules": [
                {
                    "id": r.pk, "action": r.action, "cidr": r.cidr, "reason": r.reason,
                    "permanent": r.is_permanent, "expired": r.is_expired,
                    "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        }
    )


@endpoint("security.read")
async def api_ratelimits(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    rows = await RateLimit.filter(gateway_id=gateway.pk).order_by("name")
    return json(
        {
            "rate_limits": [
                {
                    "id": r.pk, "name": r.name, "key": r.key, "rate": r.rate_label,
                    "limit": r.limit, "window_seconds": r.window_seconds,
                    "burst": r.burst, "enabled": r.enabled,
                }
                for r in rows
            ]
        }
    )


@endpoint("security.write")
async def api_ratelimit_create(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    data = await _body(ctx)
    name = (data.get("name") or "").strip()
    if not name:
        raise ApiError("A name is required.")
    limit = await RateLimit.create(
        gateway_id=gateway.pk, name=name,
        key=(data.get("key") or "ip"),
        limit=int(data.get("limit") or 100),
        window_seconds=int(data.get("window_seconds") or 60),
        burst=int(data.get("burst") or 0),
    )
    await audit.record(
        action="ratelimit.created", resource_type="rate_limit", resource_id=limit.pk,
        resource_label=f"{limit.name} ({limit.rate_label})", actor=ctx.state.api_actor,
        origin="cli",
    )
    return json({"id": limit.pk, "name": limit.name, "rate": limit.rate_label}, status_code=201)


@endpoint("security.write")
async def api_ratelimit_delete(ctx: HttpContext, limit_id: int) -> Any:
    limit = await RateLimit.get_or_none(pk=limit_id)
    if limit is None:
        raise ApiError(f"No rate limit {limit_id}.", 404)
    await audit.record(
        action="ratelimit.deleted", resource_type="rate_limit", resource_id=limit.pk,
        resource_label=limit.name, actor=ctx.state.api_actor, origin="cli",
    )
    name = limit.name
    await limit.delete()
    return json({"ok": True, "deleted": name})


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


@endpoint("apikeys.read")
async def api_keys_list(ctx: HttpContext) -> Any:
    rows = await ApiKey.all().order_by("-created_at")
    return json(
        {
            "keys": [
                {
                    "id": k.pk, "name": k.name, "prefix": k.prefix, "status": k.status,
                    "scopes": k.scope_list, "requests": k.request_count,
                    "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                    "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                }
                for k in rows
            ]
        }
    )


@endpoint("apikeys.write")
async def api_key_create(ctx: HttpContext) -> Any:
    data = await _body(ctx)
    name = (data.get("name") or "").strip()
    if not name:
        raise ApiError("A name is required.")
    days = int(data.get("expires_in_days") or 0)
    key, secret = await apikeys.create(
        name=name,
        scopes=(data.get("scopes") or ""),
        expires_in=timedelta(days=days) if days else None,
        actor=ctx.state.api_actor,
        origin="cli",
    )
    # The only response in the whole API that contains a secret, and the only
    # time this one exists outside the caller's hands.
    return json(
        {
            "id": key.pk, "name": key.name, "prefix": key.prefix, "secret": secret,
            "warning": "This secret is not stored and cannot be shown again.",
        },
        status_code=201,
    )


@endpoint("apikeys.write")
async def api_key_revoke(ctx: HttpContext, key_id: int) -> Any:
    key = await ApiKey.get_or_none(pk=key_id)
    if key is None:
        raise ApiError(f"No key {key_id}.", 404)
    data = await _body(ctx)
    await apikeys.revoke(
        key, reason=(data.get("reason") or ""), actor=ctx.state.api_actor, origin="cli"
    )
    return json({"ok": True, "revoked": key.name})


@endpoint("apikeys.write")
async def api_key_rotate(ctx: HttpContext, key_id: int) -> Any:
    key = await ApiKey.get_or_none(pk=key_id)
    if key is None:
        raise ApiError(f"No key {key_id}.", 404)
    replacement, secret = await apikeys.rotate(key, actor=ctx.state.api_actor, origin="cli")
    return json(
        {
            "id": replacement.pk, "name": replacement.name, "prefix": replacement.prefix,
            "secret": secret,
            "warning": "This secret is not stored and cannot be shown again.",
        }
    )


# ---------------------------------------------------------------------------
# Users and roles
# ---------------------------------------------------------------------------


@endpoint("users.read")
async def api_users(ctx: HttpContext) -> Any:
    rows = await User.all().order_by("email")
    out = []
    for user in rows:
        out.append(
            {
                "id": user.pk, "email": user.email, "name": user.name,
                "active": user.is_active, "superuser": user.is_superuser,
                "roles": sorted(await user.get_groups()),
                "last_login": user.last_login.isoformat() if user.last_login else None,
            }
        )
    return json({"users": out})


@endpoint("users.write")
async def api_user_create(ctx: HttpContext) -> Any:
    from sillo.permissions import Group

    data = await _body(ctx)
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or "@" not in email:
        raise ApiError("A valid email address is required.")
    if not password or len(password) < 10:
        raise ApiError("A password of at least 10 characters is required.")
    if await User.get_or_none(email=email):
        raise ApiError("That address already has an account.", 409)

    user = User(
        email=email,
        username=email,
        full_name=(data.get("name") or "").strip() or None,
        is_active=True,
        is_superuser=bool(data.get("superuser")),
    )
    user.set_password(password)
    await user.save()

    role_name = (data.get("role") or "Viewer").strip()
    role = await Group.get_or_none(name=role_name)
    if role is not None:
        await role.add_user(user)

    await audit.record(
        action="user.created", resource_type="user", resource_id=user.pk,
        resource_label=user.email, actor=ctx.state.api_actor,
        after={"email": user.email, "role": role_name}, origin="cli",
    )
    return json({"id": user.pk, "email": user.email, "role": role_name}, status_code=201)


@endpoint("users.write")
async def api_user_toggle(ctx: HttpContext) -> Any:
    data = await _body(ctx)
    user = await User.get_or_none(email=(data.get("email") or "").strip().lower())
    if user is None:
        raise ApiError("No such user.", 404)
    if user.pk == ctx.state.api_actor.pk:
        raise ApiError("You cannot disable your own account.", 409)

    if data.get("enabled"):
        await user.enable()
    else:
        await user.disable(reason=(data.get("reason") or "disabled from the CLI"))
    await audit.record(
        action="user.enabled" if user.is_active else "user.disabled",
        resource_type="user", resource_id=user.pk, resource_label=user.email,
        actor=ctx.state.api_actor, origin="cli",
    )
    return json({"ok": True, "email": user.email, "active": user.is_active})


@endpoint("users.write")
async def api_user_delete(ctx: HttpContext) -> Any:
    data = await _body(ctx)
    user = await User.get_or_none(email=(data.get("email") or "").strip().lower())
    if user is None:
        raise ApiError("No such user.", 404)
    if user.pk == ctx.state.api_actor.pk:
        raise ApiError("You cannot delete your own account.", 409)
    await audit.record(
        action="user.deleted", resource_type="user", resource_id=user.pk,
        resource_label=user.email, actor=ctx.state.api_actor,
        before={"email": user.email}, origin="cli",
    )
    email = user.email
    await user.delete()
    return json({"ok": True, "deleted": email})


@endpoint("roles.write")
async def api_user_role(ctx: HttpContext) -> Any:
    from sillo.permissions import Group

    data = await _body(ctx)
    user = await User.get_or_none(email=(data.get("email") or "").strip().lower())
    role = await Group.get_or_none(name=(data.get("role") or "").strip())
    if user is None:
        raise ApiError("No such user.", 404)
    if role is None:
        raise ApiError("No such role.", 404)

    before = sorted(await user.get_groups())
    for existing in await Group.of_user(user):
        await existing.remove_user(user)
    await role.add_user(user)
    await audit.record(
        action="user.role_changed", resource_type="user", resource_id=user.pk,
        resource_label=user.email, actor=ctx.state.api_actor,
        before={"roles": before}, after={"roles": [role.name]}, origin="cli",
    )
    return json({"ok": True, "email": user.email, "role": role.name})


@endpoint("roles.read")
async def api_roles(ctx: HttpContext) -> Any:
    from sillo.permissions import Group

    rows = await Group.all().order_by("name")
    out = []
    for role in rows:
        out.append(
            {
                "id": role.pk, "name": role.name, "description": role.description,
                "permissions": sorted(await role.get_permissions()),
                "members": await role.get_member_count(),
            }
        )
    return json({"roles": out})


@endpoint("roles.write")
async def api_role_create(ctx: HttpContext) -> Any:
    from sillo.permissions import Group

    data = await _body(ctx)
    name = (data.get("name") or "").strip()
    if not name:
        raise ApiError("A name is required.")
    role = await Group.get_or_create(name, (data.get("description") or "").strip() or None)
    permissions = [p for p in (data.get("permissions") or [])]
    if permissions:
        await role.add_permissions(*permissions)
    await audit.record(
        action="role.created", resource_type="role", resource_id=role.pk,
        resource_label=role.name, actor=ctx.state.api_actor, origin="cli",
    )
    return json({"id": role.pk, "name": role.name}, status_code=201)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def _window(ctx: HttpContext) -> analytics.Window:
    return analytics.range_bounds(
        ctx.query_params.get("range", "24h"),
        start=ctx.query_params.get("start"),
        end=ctx.query_params.get("end"),
    )


@endpoint("analytics.read")
async def api_analytics_overview(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    window = _window(ctx)
    return json({"range": window.to_dict(), "summary": await analytics.overview(gateway, window)})


@endpoint("analytics.read")
async def api_analytics_routes(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    window = _window(ctx)
    return json({"range": window.to_dict(), "routes": await analytics.route_table(gateway, window)})


@endpoint("analytics.read")
async def api_analytics_problematic(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    window = _window(ctx)
    return json(
        {
            "range": window.to_dict(),
            "routes": await analytics.problematic_routes(gateway, window, limit=20),
        }
    )


@endpoint("analytics.read")
async def api_analytics_performance(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    window = _window(ctx)
    return json(
        {"range": window.to_dict(), "routes": await analytics.slow_routes(gateway, window, limit=25)}
    )


@endpoint("analytics.read")
async def api_analytics_errors(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    window = _window(ctx)
    return json({"range": window.to_dict(), "errors": await analytics.error_breakdown(gateway, window)})


@endpoint("analytics.read")
async def api_analytics_clients(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    window = _window(ctx)
    return json(
        {
            "range": window.to_dict(),
            "clients": await analytics.client_breakdown(gateway, window, limit=50),
        }
    )


@endpoint("analytics.read")
async def api_analytics_traffic(ctx: HttpContext) -> Any:
    gateway = await _gateway(ctx)
    window = _window(ctx)
    return json(
        {"range": window.to_dict(), "traffic": await analytics.traffic_breakdown(gateway, window)}
    )


@endpoint("gateway.read")
async def api_modules(ctx: HttpContext) -> Any:
    from app.services.modules import overview

    gateway = await _gateway(ctx)
    adding = [k for k in (ctx.query_params.get("add") or "").split(",") if k]
    removing = [k for k in (ctx.query_params.get("remove") or "").split(",") if k]
    return json(await overview(gateway, adding, removing))


@endpoint("gateway.write")
async def api_modules_declare(ctx: HttpContext) -> Any:
    from app.services.modules import record_declared

    gateway = await _gateway(ctx)
    data = await _body(ctx)
    listed = [str(m).strip() for m in (data.get("modules") or []) if str(m).strip()]
    await record_declared(gateway, listed or None)
    await audit.record(
        action="gateway.modules_declared", resource_type="gateway",
        resource_id=gateway.pk, resource_label=gateway.name,
        actor=ctx.state.api_actor, after={"modules": listed}, origin="cli",
    )
    return json({"ok": True, "modules": listed})


@endpoint("audit.read")
async def api_audit(ctx: HttpContext) -> Any:
    rows = await AuditEvent.all().order_by("-created_at").limit(200)
    return json(
        {
            "events": [
                {
                    "action": e.action, "actor": e.actor_label, "resource": e.resource_type,
                    "label": e.resource_label, "origin": e.origin,
                    "at": e.created_at.isoformat(),
                }
                for e in rows
            ]
        }
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

for _route in (
    Route("/auth/login", handler=api_login, methods=["POST"], name="api.login"),
    Route("/auth/logout", handler=api_logout, methods=["POST"], name="api.logout"),
    Route("/auth/whoami", handler=api_whoami, methods=["GET"], name="api.whoami"),

    Route("/gateways", handler=api_gateways, methods=["GET"], name="api.gateways"),
    Route("/gateways", handler=api_gateway_create, methods=["POST"], name="api.gateways.create"),
    Route("/gateways/status", handler=api_gateway_status, methods=["GET"], name="api.gateways.status"),
    Route("/gateways/delete", handler=api_gateway_delete, methods=["POST"], name="api.gateways.delete"),

    Route("/routes", handler=api_routes, methods=["GET"], name="api.routes"),
    Route("/routes", handler=api_route_create, methods=["POST"], name="api.routes.create"),
    Route("/routes/{route_id:int}", handler=api_route_show, methods=["GET"], name="api.routes.show"),
    Route("/routes/{route_id:int}", handler=api_route_update, methods=["PATCH"], name="api.routes.update"),
    Route("/routes/{route_id:int}", handler=api_route_delete, methods=["DELETE"], name="api.routes.delete"),
    Route("/routes/{route_id:int}/toggle", handler=api_route_toggle, methods=["POST"], name="api.routes.toggle"),

    Route("/upstreams", handler=api_upstreams, methods=["GET"], name="api.upstreams"),
    Route("/upstreams", handler=api_upstream_create, methods=["POST"], name="api.upstreams.create"),
    Route("/upstreams/health", handler=api_upstream_health, methods=["GET"], name="api.upstreams.health"),
    Route("/upstreams/{upstream_id:int}", handler=api_upstream_delete, methods=["DELETE"], name="api.upstreams.delete"),

    Route("/config", handler=api_config_show, methods=["GET"], name="api.config"),
    Route("/config/validate", handler=api_config_validate, methods=["GET"], name="api.config.validate"),
    Route("/config/diff", handler=api_config_diff, methods=["GET"], name="api.config.diff"),
    Route("/config/apply", handler=api_config_apply, methods=["POST"], name="api.config.apply"),
    Route("/config/history", handler=api_config_history, methods=["GET"], name="api.config.history"),
    Route("/config/rollback", handler=api_config_rollback, methods=["POST"], name="api.config.rollback"),

    Route("/security/block", handler=api_security_block, methods=["POST"], name="api.security.block"),
    Route("/security/allow", handler=api_security_allow, methods=["POST"], name="api.security.allow"),
    Route("/security/unblock", handler=api_security_unblock, methods=["POST"], name="api.security.unblock"),
    Route("/security/rules", handler=api_security_rules, methods=["GET"], name="api.security.rules"),
    Route("/security/rate-limits", handler=api_ratelimits, methods=["GET"], name="api.ratelimits"),
    Route("/security/rate-limits", handler=api_ratelimit_create, methods=["POST"], name="api.ratelimits.create"),
    Route("/security/rate-limits/{limit_id:int}", handler=api_ratelimit_delete, methods=["DELETE"], name="api.ratelimits.delete"),

    Route("/keys", handler=api_keys_list, methods=["GET"], name="api.keys"),
    Route("/keys", handler=api_key_create, methods=["POST"], name="api.keys.create"),
    Route("/keys/{key_id:int}/revoke", handler=api_key_revoke, methods=["POST"], name="api.keys.revoke"),
    Route("/keys/{key_id:int}/rotate", handler=api_key_rotate, methods=["POST"], name="api.keys.rotate"),

    Route("/users", handler=api_users, methods=["GET"], name="api.users"),
    Route("/users", handler=api_user_create, methods=["POST"], name="api.users.create"),
    Route("/users/toggle", handler=api_user_toggle, methods=["POST"], name="api.users.toggle"),
    Route("/users/delete", handler=api_user_delete, methods=["POST"], name="api.users.delete"),
    Route("/users/role", handler=api_user_role, methods=["POST"], name="api.users.role"),
    Route("/roles", handler=api_roles, methods=["GET"], name="api.roles"),
    Route("/roles", handler=api_role_create, methods=["POST"], name="api.roles.create"),

    Route("/analytics/overview", handler=api_analytics_overview, methods=["GET"], name="api.analytics"),
    Route("/analytics/routes", handler=api_analytics_routes, methods=["GET"], name="api.analytics.routes"),
    Route("/analytics/problematic", handler=api_analytics_problematic, methods=["GET"], name="api.analytics.problematic"),
    Route("/analytics/performance", handler=api_analytics_performance, methods=["GET"], name="api.analytics.performance"),
    Route("/analytics/errors", handler=api_analytics_errors, methods=["GET"], name="api.analytics.errors"),
    Route("/analytics/clients", handler=api_analytics_clients, methods=["GET"], name="api.analytics.clients"),
    Route("/analytics/traffic", handler=api_analytics_traffic, methods=["GET"], name="api.analytics.traffic"),
    Route("/modules", handler=api_modules, methods=["GET"], name="api.modules"),
    Route("/modules/declare", handler=api_modules_declare, methods=["POST"], name="api.modules.declare"),
    Route("/audit", handler=api_audit, methods=["GET"], name="api.audit"),
):
    router.add_route(_route)
