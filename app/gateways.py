"""Which gateway the current request is acting on.

Janus manages more than one Caddy instance, so almost every screen needs a
selected gateway. It is resolved from an explicit query parameter, then the
session, then the first enabled gateway — and the resolution is stored back on
the session so navigating between screens keeps the selection.

Deliberately *not* a tenant boundary. Unlike a multi-tenant product, every
operator here can see every gateway; the selection is a convenience, and no
authorization decision is made from it. Anything that did would be a boundary
enforced by a query parameter, which is not a boundary.
"""

from __future__ import annotations

from typing import Any

from sillo.core.http import HttpContext

from database.models import Gateway

__all__ = ["current_gateway", "current_gateway_id", "gateway_options", "select_gateway"]

SESSION_KEY = "janus_gateway_id"


async def gateway_options() -> list[dict[str, Any]]:
    """Every gateway, for the picker."""
    return [
        {
            "id": g.pk,
            "name": g.name,
            "slug": g.slug,
            "region": g.region,
            "enabled": g.enabled,
            "sync_state": g.sync_state,
            "sync_error": g.sync_error,
            "maintenance": g.maintenance,
            "simulated": g.simulated,
            "listen": g.listen,
            "last_synced_at": g.last_synced_at.isoformat() if g.last_synced_at else None,
        }
        for g in await Gateway.all().order_by("name")
    ]


async def current_gateway_id(ctx: HttpContext) -> int | None:
    requested = ctx.query_params.get("gateway")
    if requested and requested.isdigit():
        return int(requested)
    session = getattr(ctx, "session", None)
    if session is not None:
        stored = session.get(SESSION_KEY)
        if stored:
            return int(stored)
    first = await Gateway.filter(enabled=True).order_by("name").first()
    return first.pk if first else None


async def current_gateway(ctx: HttpContext) -> Gateway | None:
    """The selected gateway, remembering the choice on the session."""
    gateway_id = await current_gateway_id(ctx)
    if gateway_id is None:
        return None
    gateway = await Gateway.get_or_none(pk=gateway_id)
    if gateway is None:
        gateway = await Gateway.filter(enabled=True).order_by("name").first()
    if gateway is not None:
        session = getattr(ctx, "session", None)
        if session is not None:
            session[SESSION_KEY] = gateway.pk
    return gateway


async def select_gateway(ctx: HttpContext, gateway_id: int) -> None:
    session = getattr(ctx, "session", None)
    if session is not None:
        session[SESSION_KEY] = gateway_id
