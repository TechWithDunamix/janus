"""Users, roles, sessions, audit log and system settings."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sillo.core.http import HttpContext
from sillo.permissions import Group
from sillo.responses import not_found
from sillo_inertia import back, defer, render, set_flash

from app.authz import ALL_PERMISSIONS, DEFAULT_ROLES, PERMISSION_NAMES
from app.gateways import current_gateway
from app.services import audit
from database.models import AuditEvent, LoginEvent, User, UserSession
from routes.web._kit import action, client_ip, form, page

__all__ = [
    "audit_log", "health", "role_permissions", "role_save", "roles", "session_revoke",
    "settings", "user_invite", "user_role", "user_toggle", "users",
]

USER_FIELDS = ("email", "full_name", "title", "is_active", "is_superuser")


def _int(data: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(data.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _bool(data: dict[str, Any], key: str, default: bool = False) -> bool:
    value = data.get(key, default)
    return value if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@page("users.read")
async def users(ctx: HttpContext) -> Any:
    rows = await User.all().order_by("email")
    memberships: dict[int, list[str]] = {}
    for user in rows:
        memberships[user.pk] = sorted(await user.get_groups())

    return await render(
        "team/Users",
        {
            "users": [
                {
                    "id": u.pk, "email": u.email, "name": u.name, "title": u.title,
                    "is_active": u.is_active, "is_superuser": u.is_superuser,
                    "roles": memberships.get(u.pk, []),
                    "pending_invite": u.is_pending_invite,
                    "disabled_reason": u.disabled_reason,
                    "last_login": u.last_login.isoformat() if u.last_login else None,
                    "created_at": u.created_at.isoformat() if hasattr(u, "created_at") else None,
                }
                for u in rows
            ],
            "roles": [
                {"id": g.pk, "name": g.name, "description": g.description}
                for g in await Group.all().order_by("name")
            ],
            "sessions": defer(lambda _: _sessions_prop(), "detail"),
            "logins": defer(lambda _: _logins_prop(), "detail"),
        },
    )


async def _sessions_prop() -> list[dict[str, Any]]:
    rows = (
        await UserSession.filter(revoked_at=None)
        .order_by("-created_at")
        .limit(100)
        .prefetch_related("user")
    )
    return [
        {
            "id": s.pk, "user": s.user.email if s.user else None, "kind": s.kind,
            "ip": s.ip, "user_agent": s.user_agent, "is_live": s.is_live,
            "created_at": s.created_at.isoformat(),
            "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None,
            "expires_at": s.expires_at.isoformat() if s.expires_at else None,
        }
        for s in rows
    ]


async def _logins_prop() -> list[dict[str, Any]]:
    rows = await LoginEvent.all().order_by("-created_at").limit(60)
    return [
        {
            "id": e.pk, "email": e.email, "successful": e.successful, "reason": e.reason,
            "kind": e.kind, "ip": e.ip, "created_at": e.created_at.isoformat(),
        }
        for e in rows
    ]


@action("users.write")
async def user_invite(ctx: HttpContext) -> Any:
    """Create an account with an unusable password.

    The invited account cannot sign in until a password is set — there is no
    "temporary password" here, because a temporary password is a real
    credential that gets emailed, reused and never rotated. An administrator
    sets one through `janus users create`, or the invitee is given a link.
    """
    data = await form(ctx)
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        set_flash(ctx, "error", "A valid email address is required.")
        return back(fallback="/team/users", ctx=ctx)
    if await User.get_or_none(email=email):
        set_flash(ctx, "error", "That address already has an account.")
        return back(fallback="/team/users", ctx=ctx)

    user = User(
        email=email,
        username=email,
        full_name=(data.get("full_name") or "").strip() or None,
        title=(data.get("title") or "").strip() or None,
        invited_at=datetime.now(UTC),
        invited_by_id=ctx.state.auth_user.pk,
        is_active=True,
    )
    # A random unguessable value rather than a blank: `set_unusable_password`
    # is the framework's way of saying "no password will ever match", and a
    # blank string would hash to something a blank submission could match.
    user.set_unusable_password()
    await user.save()

    role_name = (data.get("role") or "Viewer").strip()
    role = await Group.get_or_none(name=role_name)
    if role is not None:
        await role.add_user(user)

    await audit.record(
        action="user.invited", resource_type="user", resource_id=user.pk,
        resource_label=user.email, actor=ctx.state.auth_user,
        after={"email": user.email, "role": role_name}, ip=client_ip(ctx),
    )
    set_flash(ctx, "success", f"Invited {email} as {role_name}.")
    return back(fallback="/team/users", ctx=ctx)


@action("users.write")
async def user_toggle(ctx: HttpContext) -> Any:
    data = await form(ctx)
    user = await User.get_or_none(pk=_int(data, "id"))
    if user is None:
        return not_found()
    if user.pk == ctx.state.auth_user.pk:
        set_flash(ctx, "error", "You cannot disable your own account.")
        return back(fallback="/team/users", ctx=ctx)

    before = audit.snapshot(user, USER_FIELDS)
    if user.is_active:
        await user.disable(reason=(data.get("reason") or "").strip())
    else:
        await user.enable()

    await audit.record(
        action="user.disabled" if not user.is_active else "user.enabled",
        resource_type="user", resource_id=user.pk, resource_label=user.email,
        actor=ctx.state.auth_user, before=before,
        after=audit.snapshot(user, USER_FIELDS), ip=client_ip(ctx),
    )
    set_flash(
        ctx, "success",
        f"{user.email} {'enabled' if user.is_active else 'disabled — their sessions were revoked'}.",
    )
    return back(fallback="/team/users", ctx=ctx)


@action("roles.write")
async def user_role(ctx: HttpContext) -> Any:
    data = await form(ctx)
    user = await User.get_or_none(pk=_int(data, "id"))
    role = await Group.get_or_none(name=(data.get("role") or "").strip())
    if user is None or role is None:
        return not_found()

    before = sorted(await user.get_groups())
    for existing in await Group.of_user(user):
        await existing.remove_user(user)
    await role.add_user(user)

    await audit.record(
        action="user.role_changed", resource_type="user", resource_id=user.pk,
        resource_label=user.email, actor=ctx.state.auth_user,
        before={"roles": before}, after={"roles": [role.name]}, ip=client_ip(ctx),
    )
    set_flash(ctx, "success", f"{user.email} is now {role.name}.")
    return back(fallback="/team/users", ctx=ctx)


@action("users.write")
async def session_revoke(ctx: HttpContext) -> Any:
    data = await form(ctx)
    session_row = await UserSession.get_or_none(pk=_int(data, "id"))
    if session_row is None:
        return not_found()
    await session_row.fetch_related("user")

    session_row.revoked_at = datetime.now(UTC)
    session_row.revoked_reason = "revoked by an administrator"
    await session_row.save()

    await audit.record(
        action="session.revoked", resource_type="session", resource_id=session_row.pk,
        resource_label=session_row.user.email if session_row.user else None,
        actor=ctx.state.auth_user, ip=client_ip(ctx),
    )
    set_flash(ctx, "success", "Session revoked. It stops working on its next request.")
    return back(fallback="/team/users", ctx=ctx)


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


@page("roles.read")
async def roles(ctx: HttpContext) -> Any:
    groups = await Group.all().order_by("name")
    return await render(
        "team/Roles",
        {
            "roles": [
                {
                    "id": g.pk, "name": g.name, "description": g.description,
                    "permissions": sorted(await g.get_permissions()),
                    "members": await g.get_member_count(),
                    "is_default": g.name in DEFAULT_ROLES,
                }
                for g in groups
            ],
            "catalogue": [
                {
                    "group": label,
                    "permissions": [{"name": n, "description": d} for n, d in entries],
                }
                for label, entries in ALL_PERMISSIONS.items()
            ],
        },
    )


@action("roles.write")
async def role_save(ctx: HttpContext) -> Any:
    data = await form(ctx)
    name = (data.get("name") or "").strip()
    if not name:
        set_flash(ctx, "error", "A role needs a name.")
        return back(fallback="/team/roles", ctx=ctx)

    role = await Group.get_or_create(name, (data.get("description") or "").strip() or None)
    await audit.record(
        action="role.created", resource_type="role", resource_id=role.pk,
        resource_label=role.name, actor=ctx.state.auth_user,
        after={"name": role.name}, ip=client_ip(ctx),
    )
    set_flash(ctx, "success", f"Role “{name}” saved.")
    return back(fallback="/team/roles", ctx=ctx)


@action("roles.write")
async def role_permissions(ctx: HttpContext) -> Any:
    """Replace a role's permission set.

    Owner is refused, deliberately. It is the recovery path for an
    installation whose roles have been misconfigured, and a control plane that
    lets you remove your own last route back in is one bad afternoon away from
    needing database access to fix.
    """
    data = await form(ctx)
    role = await Group.get_or_none(pk=_int(data, "id"))
    if role is None:
        return not_found()
    if role.name == "Owner":
        set_flash(ctx, "error", "The Owner role cannot be narrowed — it is the recovery path.")
        return back(fallback="/team/roles", ctx=ctx)

    wanted = {p for p in (data.get("permissions") or []) if p in PERMISSION_NAMES}
    held = set(await role.get_permissions())

    if wanted - held:
        await role.add_permissions(*(wanted - held))
    if held - wanted:
        await role.remove_permissions(*(held - wanted))

    await audit.record(
        action="role.permissions_changed", resource_type="role", resource_id=role.pk,
        resource_label=role.name, actor=ctx.state.auth_user,
        before={"permissions": sorted(held)}, after={"permissions": sorted(wanted)},
        ip=client_ip(ctx),
    )
    set_flash(ctx, "success", f"Permissions for “{role.name}” updated.")
    return back(fallback="/team/roles", ctx=ctx)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@page("audit.read")
async def audit_log(ctx: HttpContext) -> Any:
    query = AuditEvent.all()
    resource = ctx.query_params.get("resource")
    actor = ctx.query_params.get("actor")
    search = (ctx.query_params.get("q") or "").strip()

    if resource:
        query = query.filter(resource_type=resource)
    if actor:
        query = query.filter(actor_label__icontains=actor)
    if search:
        query = query.filter(action__icontains=search)

    rows = await query.order_by("-created_at").limit(300).prefetch_related("actor")
    return await render(
        "team/AuditLog",
        {
            "events": [
                {
                    "id": e.pk, "action": e.action, "actor": e.actor_label,
                    "resource_type": e.resource_type, "resource_id": e.resource_id,
                    "resource_label": e.resource_label, "origin": e.origin, "ip": e.ip,
                    "before": e.before, "after": e.after,
                    "created_at": e.created_at.isoformat(),
                }
                for e in rows
            ],
            "filters": {"resource": resource or "", "actor": actor or "", "q": search},
            "resource_types": sorted(
                {e.resource_type for e in await AuditEvent.all().limit(1000)}
            ),
        },
    )


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


@page("gateway.read")
async def health(ctx: HttpContext) -> Any:
    from app.services.health import gateway_health

    gateway = await current_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})
    return await render(
        "system/Health",
        {
            "gateway": {
                "id": gateway.pk, "name": gateway.name, "listen": gateway.listen,
                "admin_url": gateway.admin_url, "sync_state": gateway.sync_state,
            },
            "health": defer(lambda _: gateway_health(gateway), "health"),
        },
    )


@page("gateway.read")
async def modules(ctx: HttpContext) -> Any:
    """Which Caddy plugins this gateway's build has, and what is missing."""
    from app.services.modules import overview

    gateway = await current_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})

    adding = [k for k in (ctx.query_params.get("add") or "").split(",") if k]
    removing = [k for k in (ctx.query_params.get("remove") or "").split(",") if k]
    return await render(
        "system/Modules",
        {
            "gateway": {"id": gateway.pk, "name": gateway.name},
            "adding": adding,
            "removing": removing,
            "modules": defer(lambda _: overview(gateway, adding, removing), "modules"),
        },
    )


@action("gateway.write")
async def modules_declare(ctx: HttpContext) -> Any:
    """Record the modules a remote gateway's build has."""
    from app.services.modules import record_declared

    gateway = await current_gateway(ctx)
    if gateway is None:
        return not_found()

    data = await form(ctx)
    raw = data.get("modules")
    listed = (
        [m.strip() for m in raw.split(",") if m.strip()]
        if isinstance(raw, str)
        else [str(m).strip() for m in (raw or []) if str(m).strip()]
    )
    await record_declared(gateway, listed or None)
    await audit.record(
        action="gateway.modules_declared", resource_type="gateway",
        resource_id=gateway.pk, resource_label=gateway.name,
        actor=ctx.state.auth_user, after={"modules": listed}, ip=client_ip(ctx),
    )
    set_flash(
        ctx, "success",
        f"Recorded {len(listed)} module(s) for “{gateway.name}”."
        if listed else "Cleared the declaration; Janus will detect locally again.",
    )
    return back(fallback="/system/modules", ctx=ctx)


@page("gateway.read")
async def settings(ctx: HttpContext) -> Any:
    from app.config import config

    return await render(
        "system/Settings",
        {
            "settings": {
                "app_name": config.app_name,
                "app_env": config.app_env,
                "app_url": config.app_url,
                "caddy_admin_url": config.caddy_admin_url,
                "caddy_server_name": config.caddy_server_name,
                "caddy_access_log": config.caddy_access_log,
                "caddy_simulate": config.caddy_simulate,
                "queue_backend": config.queue_backend,
                "database": config.database_url.split("://")[0],
                "request_retention_hours": config.request_retention_hours,
                "rollup_retention_days": config.rollup_retention_days,
                "error_rate_critical": config.error_rate_critical,
                "latency_p95_critical_ms": config.latency_p95_critical_ms,
                # Never the secret itself, only whether it is still the
                # development default — which is the only thing a settings
                # screen needs to tell an operator about it.
                "secret_is_default": config.secret_key == "dev-only-insecure-secret-key",
            }
        },
    )
