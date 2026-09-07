"""The main dashboard, and the analytics screens.

Every page here defers its heavy props. That is not decoration: on a gateway
with real volume the aggregate queries are the slow part, and deferring means
the shell, the range picker and the headline numbers paint while the series is
still being computed.
"""

from __future__ import annotations

from typing import Any

from sillo.core.http import HttpContext
from sillo.responses import not_found
from sillo_inertia import defer, render

from app.gateways import current_gateway
from app.services import analytics
from database.models import Alert, Deployment, GatewayRoute
from routes.web._kit import page

__all__ = [
    "analytics_bandwidth",
    "analytics_clients",
    "analytics_errors",
    "analytics_overview",
    "analytics_performance",
    "analytics_requests",
    "analytics_routes",
    "overview",
    "route_analytics",
]


def _window(ctx: HttpContext) -> analytics.Window:
    return analytics.range_bounds(
        ctx.query_params.get("range", "24h"),
        start=ctx.query_params.get("start"),
        end=ctx.query_params.get("end"),
    )


async def _require_gateway(ctx: HttpContext):
    gateway = await current_gateway(ctx)
    return gateway


@page("analytics.read")
async def overview(ctx: HttpContext) -> Any:
    """The answer-in-five-seconds screen.

    The prop list is the twelve questions the dashboard has to answer, in the
    order the page asks them: how much traffic, is it healthy, are errors
    rising, what is failing, what is slow, what is unhealthy, who is loudest,
    who is blocked, what changed.
    """
    gateway = await _require_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})

    window = _window(ctx)
    return await render(
        "Overview",
        {
            "range": window.to_dict(),
            "summary": await analytics.overview(gateway, window),
            "series": defer(lambda _: analytics.series(gateway, window), "charts"),
            "problematic": defer(
                lambda _: analytics.problematic_routes(gateway, window, limit=6), "charts"
            ),
            "slow": defer(lambda _: analytics.slow_routes(gateway, window, limit=6), "charts"),
            "status_distribution": defer(
                lambda _: analytics.status_distribution(gateway, window), "charts"
            ),
            "top_clients": defer(
                lambda _: analytics.client_breakdown(gateway, window, limit=8), "charts"
            ),
            "alerts": defer(lambda _: _alerts_prop(gateway), "activity"),
            "recent_changes": defer(lambda _: _recent_changes(gateway), "activity"),
            "health": defer(lambda _: _health_prop(gateway), "activity"),
        },
    )


async def _alerts_prop(gateway: Any) -> list[dict[str, Any]]:
    rows = await Alert.filter(gateway_id=gateway.pk, status="open").order_by("-opened_at").limit(8)
    return [
        {
            "id": a.pk,
            "kind": a.kind,
            "severity": a.severity,
            "title": a.title,
            "detail": a.detail,
            "observed": a.observed,
            "threshold": a.threshold,
            "opened_at": a.opened_at.isoformat(),
        }
        for a in rows
    ]


async def _recent_changes(gateway: Any) -> list[dict[str, Any]]:
    """What changed recently — question 10 on the dashboard."""
    rows = (
        await Deployment.filter(gateway_id=gateway.pk)
        .order_by("-started_at")
        .limit(6)
        .prefetch_related("version", "actor")
    )
    return [
        {
            "id": d.pk,
            "version": d.version.version if d.version else None,
            "summary": d.version.summary if d.version else None,
            "status": d.status,
            "stage": d.stage,
            "error": d.error,
            "origin": d.origin,
            "actor": d.actor.email if d.actor else "system",
            "started_at": d.started_at.isoformat(),
            "duration_ms": d.duration_ms,
        }
        for d in rows
    ]


async def _health_prop(gateway: Any) -> dict[str, Any]:
    from app.services.health import gateway_health

    return await gateway_health(gateway)


# ---------------------------------------------------------------------------
# Analytics section
# ---------------------------------------------------------------------------


@page("analytics.read")
async def analytics_overview(ctx: HttpContext) -> Any:
    gateway = await _require_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})
    window = _window(ctx)
    return await render(
        "analytics/Overview",
        {
            "range": window.to_dict(),
            "summary": await analytics.overview(gateway, window),
            "series": defer(lambda _: analytics.series(gateway, window), "charts"),
            "status_distribution": defer(
                lambda _: analytics.status_distribution(gateway, window), "charts"
            ),
            "traffic": defer(lambda _: analytics.traffic_breakdown(gateway, window), "charts"),
        },
    )


@page("analytics.read")
async def analytics_requests(ctx: HttpContext) -> Any:
    gateway = await _require_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})
    window = _window(ctx)
    return await render(
        "analytics/Requests",
        {
            "range": window.to_dict(),
            "summary": await analytics.overview(gateway, window),
            "series": defer(lambda _: analytics.series(gateway, window), "charts"),
            "traffic": defer(lambda _: analytics.traffic_breakdown(gateway, window), "charts"),
        },
    )


@page("analytics.read")
async def analytics_routes(ctx: HttpContext) -> Any:
    gateway = await _require_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})
    window = _window(ctx)
    return await render(
        "analytics/Routes",
        {
            "range": window.to_dict(),
            "routes": defer(lambda _: analytics.route_table(gateway, window), "table"),
            "problematic": defer(
                lambda _: analytics.problematic_routes(gateway, window, limit=10), "table"
            ),
        },
    )


@page("analytics.read")
async def route_analytics(ctx: HttpContext, route_id: int) -> Any:
    """One route's complete investigation view."""
    gateway = await _require_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})
    route = await GatewayRoute.get_or_none(pk=route_id, gateway_id=gateway.pk)
    if route is None:
        return not_found()
    await route.fetch_related("upstream", "domain")

    window = _window(ctx)
    return await render(
        "analytics/RouteDetail",
        {
            "range": window.to_dict(),
            "route": {
                "id": route.pk,
                "name": route.name,
                "slug": route.slug,
                "label": f"{route.method_label} {route.path}",
                "path": route.path,
                "method": route.method_label,
                "enabled": route.enabled,
                "domain": route.domain.hostname if route.domain else None,
                "upstream": route.upstream.name if route.upstream else None,
                "auth_policy": route.auth_policy,
                "priority": route.priority,
            },
            "detail": defer(lambda _: analytics.route_detail(gateway, route, window), "charts"),
        },
    )


@page("analytics.read")
async def analytics_errors(ctx: HttpContext) -> Any:
    gateway = await _require_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})
    window = _window(ctx)
    return await render(
        "analytics/Errors",
        {
            "range": window.to_dict(),
            "summary": await analytics.overview(gateway, window),
            "errors": defer(lambda _: analytics.error_breakdown(gateway, window), "charts"),
            "series": defer(lambda _: analytics.series(gateway, window), "charts"),
        },
    )


@page("analytics.read")
async def analytics_performance(ctx: HttpContext) -> Any:
    gateway = await _require_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})
    window = _window(ctx)
    return await render(
        "analytics/Performance",
        {
            "range": window.to_dict(),
            "summary": await analytics.overview(gateway, window),
            "slow": defer(lambda _: analytics.slow_routes(gateway, window, limit=20), "table"),
            "series": defer(lambda _: analytics.series(gateway, window), "charts"),
        },
    )


@page("analytics.read")
async def analytics_clients(ctx: HttpContext) -> Any:
    gateway = await _require_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})
    window = _window(ctx)
    return await render(
        "analytics/Clients",
        {
            "range": window.to_dict(),
            "clients": defer(
                lambda _: analytics.client_breakdown(gateway, window, limit=50), "table"
            ),
            "traffic": defer(lambda _: analytics.traffic_breakdown(gateway, window), "table"),
        },
    )


@page("analytics.read")
async def analytics_bandwidth(ctx: HttpContext) -> Any:
    gateway = await _require_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})
    window = _window(ctx)
    return await render(
        "analytics/Bandwidth",
        {
            "range": window.to_dict(),
            "summary": await analytics.overview(gateway, window),
            "series": defer(lambda _: analytics.series(gateway, window), "charts"),
            "traffic": defer(lambda _: analytics.traffic_breakdown(gateway, window), "charts"),
        },
    )
