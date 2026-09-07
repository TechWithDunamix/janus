"""Security: clients, allowlist, blocklist, rate limits, API keys, policies."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sillo.core.http import HttpContext
from sillo.responses import not_found
from sillo_inertia import back, defer, render, set_flash

from app.gateways import current_gateway
from app.services import analytics, apikeys, audit, security
from database.models import (
    Alert,
    Anomaly,
    ApiKey,
    Client,
    IpRule,
    RateLimit,
    SecurityPolicy,
    normalise_cidr,
)
from routes.web._kit import action, client_ip, form, page

__all__ = [
    "allowlist", "apikey_create", "apikey_revoke", "apikey_rotate", "apikeys_index",
    "block_create", "block_remove", "blocklist", "client_detail", "clients",
    "policies", "policy_delete", "policy_save", "ratelimit_delete", "ratelimit_save",
    "ratelimits", "security_overview",
]

RATE_FIELDS = ("name", "key", "limit", "window_seconds", "burst", "enabled", "route_id")
POLICY_FIELDS = ("name", "subject", "operator", "value", "action", "priority", "enabled")


def _int(data: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(data.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _bool(data: dict[str, Any], key: str, default: bool = False) -> bool:
    value = data.get(key, default)
    return value if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes", "on"}


def _window(ctx: HttpContext) -> analytics.Window:
    return analytics.range_bounds(ctx.query_params.get("range", "24h"))


@page("security.read")
async def security_overview(ctx: HttpContext) -> Any:
    gateway = await current_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})
    window = _window(ctx)
    return await render(
        "security/Overview",
        {
            "range": window.to_dict(),
            "summary": await security.security_overview(gateway, window),
            "series": defer(lambda _: analytics.series(gateway, window), "charts"),
            "alerts": defer(lambda _: _alerts(gateway), "charts"),
            "anomalies": defer(lambda _: _anomalies(gateway), "charts"),
            "top_blocked": defer(lambda _: _top_blocked(gateway, window), "charts"),
        },
    )


async def _alerts(gateway: Any) -> list[dict[str, Any]]:
    rows = await Alert.filter(gateway_id=gateway.pk, status="open").order_by("-opened_at").limit(15)
    return [
        {
            "id": a.pk, "kind": a.kind, "severity": a.severity, "title": a.title,
            "detail": a.detail, "observed": a.observed, "threshold": a.threshold,
            "opened_at": a.opened_at.isoformat(),
        }
        for a in rows
    ]


async def _anomalies(gateway: Any) -> list[dict[str, Any]]:
    rows = (
        await Anomaly.filter(gateway_id=gateway.pk, verdict="unreviewed")
        .order_by("-detected_at")
        .limit(15)
    )
    return [
        {
            "id": a.pk, "kind": a.kind, "detail": a.detail, "observed": a.observed,
            "baseline": a.baseline, "deviation": a.deviation, "verdict": a.verdict,
            "detected_at": a.detected_at.isoformat(),
        }
        for a in rows
    ]


async def _top_blocked(gateway: Any, window: analytics.Window) -> list[dict[str, Any]]:
    """Clients receiving the most 403s — who is being blocked, and how hard."""
    from tortoise.functions import Count

    from database.models import RequestLog

    rows = (
        await RequestLog.filter(
            gateway_id=gateway.pk, occurred_at__gte=window.since, status=403
        )
        .group_by("client_ip")
        .annotate(blocked=Count("id"))
        .values("client_ip", "blocked")
    )
    return sorted(rows, key=lambda r: -r["blocked"])[:15]


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


@page("security.read")
async def clients(ctx: HttpContext) -> Any:
    query = (ctx.query_params.get("q") or "").strip()
    rows = Client.all()
    if query:
        rows = rows.filter(identifier__icontains=query)
    status = ctx.query_params.get("status")
    if status in {"active", "blocked", "allowed"}:
        rows = rows.filter(status=status)

    listed = await rows.order_by("-request_count").limit(200)
    return await render(
        "security/Clients",
        {
            "clients": [
                {
                    "id": c.pk, "kind": c.kind, "identifier": c.identifier, "label": c.label,
                    "organisation": c.organisation, "status": c.status,
                    "request_count": c.request_count, "error_count": c.error_count,
                    "blocked_count": c.blocked_count, "bytes_out": c.bytes_out,
                    "first_seen_at": c.first_seen_at.isoformat() if c.first_seen_at else None,
                    "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None,
                }
                for c in listed
            ],
            "filters": {"q": query, "status": status or ""},
        },
    )


@page("security.read")
async def client_detail(ctx: HttpContext, client_id: int) -> Any:
    gateway = await current_gateway(ctx)
    client = await Client.get_or_none(pk=client_id)
    if client is None or gateway is None:
        return not_found()

    window = _window(ctx)
    rules = await IpRule.filter(gateway_id=gateway.pk, cidr__startswith=client.identifier)

    return await render(
        "security/ClientDetail",
        {
            "range": window.to_dict(),
            "client": {
                "id": client.pk, "kind": client.kind, "identifier": client.identifier,
                "label": client.label, "notes": client.notes,
                "organisation": client.organisation, "status": client.status,
                "request_count": client.request_count, "error_count": client.error_count,
                "blocked_count": client.blocked_count,
                "rate_limited_count": client.rate_limited_count,
                "bytes_out": client.bytes_out,
                "first_seen_at": client.first_seen_at.isoformat() if client.first_seen_at else None,
                "last_seen_at": client.last_seen_at.isoformat() if client.last_seen_at else None,
                "effective_status": await security.effective_status(gateway, client.identifier),
            },
            "rules": [
                {
                    "id": r.pk, "action": r.action, "cidr": r.cidr, "reason": r.reason,
                    "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                }
                for r in rules
            ],
            "history": defer(lambda _: _client_history(gateway, client, window), "charts"),
        },
    )


async def _client_history(gateway: Any, client: Client, window: analytics.Window) -> dict[str, Any]:
    from tortoise.functions import Count

    from database.models import RequestLog

    base = RequestLog.filter(
        gateway_id=gateway.pk, client_ip=client.identifier, occurred_at__gte=window.since
    )
    by_status = (
        await base.group_by("status").annotate(count=Count("id")).values("status", "count")
    )
    recent = await base.order_by("-occurred_at").limit(50)
    return {
        "by_status": sorted(by_status, key=lambda r: -r["count"]),
        "recent": [
            {
                "at": r.occurred_at.isoformat(), "method": r.method, "path": r.path,
                "status": r.status, "latency_ms": round(r.latency_ms, 1),
                "edge_action": r.edge_action,
            }
            for r in recent
        ],
    }


# ---------------------------------------------------------------------------
# Allow / block lists
# ---------------------------------------------------------------------------


async def _rules_page(ctx: HttpContext, kind: str, component: str) -> Any:
    gateway = await current_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})

    rows = await IpRule.filter(gateway_id=gateway.pk, action=kind).order_by("-created_at")
    return await render(
        component,
        {
            "rules": [
                {
                    "id": r.pk, "action": r.action, "cidr": r.cidr, "reason": r.reason,
                    "notes": r.notes, "is_permanent": r.is_permanent, "is_expired": r.is_expired,
                    "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                    "created_at": r.created_at.isoformat(),
                    "scope": "route" if r.route_id else "domain" if r.domain_id else "global",
                }
                for r in rows
            ],
            "stats": defer(lambda _: _rule_stats(gateway), "stats"),
        },
    )


async def _rule_stats(gateway: Any) -> dict[str, Any]:
    window = analytics.range_bounds("24h")
    stats = await analytics.overview(gateway, window)
    return {
        "blocked_requests": stats["blocked"],
        "rate_limited": stats["rate_limited"],
        "top_blocked": await _top_blocked(gateway, window),
    }


@page("security.read")
async def blocklist(ctx: HttpContext) -> Any:
    return await _rules_page(ctx, "block", "security/Blocklist")


@page("security.read")
async def allowlist(ctx: HttpContext) -> Any:
    return await _rules_page(ctx, "allow", "security/Allowlist")


@action("security.write")
async def block_create(ctx: HttpContext) -> Any:
    data = await form(ctx)
    gateway = await current_gateway(ctx)
    if gateway is None:
        return not_found()

    kind = (data.get("action") or "block").strip()
    minutes = _int(data, "duration_minutes")
    try:
        if kind == "allow":
            rule = await security.allow(
                gateway, data.get("cidr") or "", reason=data.get("reason") or "",
                actor=ctx.state.auth_user, ip=client_ip(ctx),
            )
        else:
            rule = await security.block(
                gateway, data.get("cidr") or "", reason=data.get("reason") or "",
                duration=timedelta(minutes=minutes) if minutes else None,
                actor=ctx.state.auth_user, ip=client_ip(ctx),
            )
    except ValueError as error:
        set_flash(ctx, "error", str(error))
        return back(fallback="/security/blocklist", ctx=ctx)

    set_flash(
        ctx, "success",
        f"{'Allowed' if kind == 'allow' else 'Blocked'} {rule.cidr}. Deploy to apply it.",
    )
    return back(fallback="/security/blocklist", ctx=ctx)


@action("security.write")
async def block_remove(ctx: HttpContext) -> Any:
    data = await form(ctx)
    gateway = await current_gateway(ctx)
    rule = await IpRule.get_or_none(pk=_int(data, "id"))
    if rule is None or gateway is None:
        return not_found()

    await audit.record(
        action="client.rule_removed", resource_type="ip_rule", resource_id=rule.pk,
        resource_label=rule.cidr, actor=ctx.state.auth_user,
        before=audit.snapshot(rule, ("action", "cidr", "reason")), ip=client_ip(ctx),
    )
    cidr = rule.cidr
    await rule.delete()
    set_flash(ctx, "success", f"Rule for {cidr} removed. Deploy to apply it.")
    return back(fallback="/security/blocklist", ctx=ctx)


# ---------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------


@page("security.read")
async def ratelimits(ctx: HttpContext) -> Any:
    gateway = await current_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})

    from app.services.health import gateway_health

    rows = await RateLimit.filter(gateway_id=gateway.pk).order_by("name").prefetch_related("route")
    health = await gateway_health(gateway)
    window = _window(ctx)

    return await render(
        "security/RateLimits",
        {
            "range": window.to_dict(),
            "limits": [
                {
                    "id": r.pk, "name": r.name, "key": r.key, "limit": r.limit,
                    "window_seconds": r.window_seconds, "burst": r.burst,
                    "rate_label": r.rate_label, "enabled": r.enabled,
                    "response_status": r.response_status,
                    "route": r.route.name if r.route else None,
                    "route_id": r.route_id,
                }
                for r in rows
            ],
            # Stated on the screen rather than discovered when a deployment
            # fails: stock Caddy has no rate-limit handler, so a limit defined
            # against a build without the plugin is recorded and displayed but
            # not enforced. Saying so is the only honest option.
            "enforceable": health["rate_limiting_available"],
            "stats": defer(lambda _: _ratelimit_stats(gateway, window), "stats"),
        },
    )


async def _ratelimit_stats(gateway: Any, window: analytics.Window) -> dict[str, Any]:
    from tortoise.functions import Count

    from database.models import RequestLog

    stats = await analytics.overview(gateway, window)
    offenders = (
        await RequestLog.filter(gateway_id=gateway.pk, occurred_at__gte=window.since, status=429)
        .group_by("client_ip")
        .annotate(hits=Count("id"))
        .values("client_ip", "hits")
    )
    routes = await analytics.route_table(gateway, window)
    return {
        "total": stats["rate_limited"],
        "series": await analytics.series(gateway, window),
        "offenders": sorted(offenders, key=lambda r: -r["hits"])[:15],
        "routes": sorted(
            [r for r in routes if r["requests"]], key=lambda r: -r.get("rate_limited", 0)
        )[:10],
    }


@action("security.write")
async def ratelimit_save(ctx: HttpContext) -> Any:
    data = await form(ctx)
    gateway = await current_gateway(ctx)
    if gateway is None:
        return not_found()

    name = (data.get("name") or "").strip()
    if not name:
        set_flash(ctx, "error", "A rate limit needs a name.")
        return back(fallback="/security/rate-limits", ctx=ctx)

    limit_id = _int(data, "id")
    existing = await RateLimit.get_or_none(pk=limit_id, gateway_id=gateway.pk) if limit_id else None
    before = audit.snapshot(existing, RATE_FIELDS)

    values = {
        "name": name,
        "key": (data.get("key") or "ip").strip(),
        "limit": max(1, _int(data, "limit", 100)),
        "window_seconds": max(1, _int(data, "window_seconds", 60)),
        "burst": max(0, _int(data, "burst")),
        "response_status": _int(data, "response_status", 429),
        "response_body": data.get("response_body") or None,
        "send_retry_after": _bool(data, "send_retry_after", True),
        "route_id": _int(data, "route_id") or None,
        "domain_id": _int(data, "domain_id") or None,
        "enabled": _bool(data, "enabled", True),
    }

    if existing is None:
        limit = await RateLimit.create(gateway_id=gateway.pk, **values)
        act = "ratelimit.created"
    else:
        for key, value in values.items():
            setattr(existing, key, value)
        await existing.save()
        limit = existing
        act = "ratelimit.updated"

    await audit.record(
        action=act, resource_type="rate_limit", resource_id=limit.pk,
        resource_label=f"{limit.name} ({limit.rate_label})", actor=ctx.state.auth_user,
        before=before, after=audit.snapshot(limit, RATE_FIELDS), ip=client_ip(ctx),
    )
    set_flash(ctx, "success", f"Rate limit “{limit.name}” saved. Deploy to apply it.")
    return back(fallback="/security/rate-limits", ctx=ctx)


@action("security.write")
async def ratelimit_delete(ctx: HttpContext) -> Any:
    data = await form(ctx)
    limit = await RateLimit.get_or_none(pk=_int(data, "id"))
    if limit is None:
        return not_found()
    await audit.record(
        action="ratelimit.deleted", resource_type="rate_limit", resource_id=limit.pk,
        resource_label=limit.name, actor=ctx.state.auth_user,
        before=audit.snapshot(limit, RATE_FIELDS), ip=client_ip(ctx),
    )
    name = limit.name
    await limit.delete()
    set_flash(ctx, "success", f"Rate limit “{name}” deleted.")
    return back(fallback="/security/rate-limits", ctx=ctx)


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


@page("apikeys.read")
async def apikeys_index(ctx: HttpContext) -> Any:
    rows = await ApiKey.all().order_by("-created_at").prefetch_related("client", "rate_limit")
    return await render(
        "security/ApiKeys",
        {
            "keys": [
                {
                    "id": k.pk, "name": k.name,
                    # The prefix, never the secret. There is no code path in
                    # Janus that can return the secret after creation.
                    "prefix": k.prefix,
                    "status": k.status, "scopes": k.scope_list, "enabled": k.enabled,
                    "client": k.client.label or k.client.identifier if k.client else None,
                    "rate_limit": k.rate_limit.name if k.rate_limit else None,
                    "request_count": k.request_count,
                    "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                    "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                    "created_at": k.created_at.isoformat(),
                    "rotated_from_id": k.rotated_from_id,
                }
                for k in rows
            ],
            "clients": [
                {"id": c.pk, "label": c.label or c.identifier}
                for c in await Client.all().order_by("identifier").limit(200)
            ],
            "rate_limits": [
                {"id": r.pk, "name": r.name, "rate_label": r.rate_label}
                for r in await RateLimit.all().order_by("name")
            ],
        },
    )


@action("apikeys.write")
async def apikey_create(ctx: HttpContext) -> Any:
    data = await form(ctx)
    name = (data.get("name") or "").strip()
    if not name:
        set_flash(ctx, "error", "A key needs a name.")
        return back(fallback="/security/api-keys", ctx=ctx)

    days = _int(data, "expires_in_days")
    key, secret = await apikeys.create(
        name=name,
        client=await Client.get_or_none(pk=_int(data, "client_id")) if data.get("client_id") else None,
        scopes=(data.get("scopes") or "").strip(),
        expires_in=timedelta(days=days) if days else None,
        rate_limit_id=_int(data, "rate_limit_id") or None,
        actor=ctx.state.auth_user,
        ip=client_ip(ctx),
    )
    # The one and only time the secret exists outside the caller's hands. It
    # goes in the flash bag, which the session clears on read, and is never
    # written to a log or an audit row.
    set_flash(ctx, "secret", secret)
    set_flash(ctx, "success", f"Key “{key.name}” created. Copy it now — it is not shown again.")
    return back(fallback="/security/api-keys", ctx=ctx)


@action("apikeys.write")
async def apikey_rotate(ctx: HttpContext) -> Any:
    data = await form(ctx)
    key = await ApiKey.get_or_none(pk=_int(data, "id"))
    if key is None:
        return not_found()
    replacement, secret = await apikeys.rotate(key, actor=ctx.state.auth_user, ip=client_ip(ctx))
    set_flash(ctx, "secret", secret)
    set_flash(ctx, "success", f"Key “{replacement.name}” rotated. Copy the new secret now.")
    return back(fallback="/security/api-keys", ctx=ctx)


@action("apikeys.write")
async def apikey_revoke(ctx: HttpContext) -> Any:
    data = await form(ctx)
    key = await ApiKey.get_or_none(pk=_int(data, "id"))
    if key is None:
        return not_found()
    await apikeys.revoke(
        key, reason=data.get("reason") or "", actor=ctx.state.auth_user, ip=client_ip(ctx)
    )
    set_flash(ctx, "success", f"Key “{key.name}” revoked.")
    return back(fallback="/security/api-keys", ctx=ctx)


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


@page("security.read")
async def policies(ctx: HttpContext) -> Any:
    gateway = await current_gateway(ctx)
    if gateway is None:
        return await render("gateway/Empty", {})

    from database.models import Domain, GatewayRoute

    rows = (
        await SecurityPolicy.filter(gateway_id=gateway.pk)
        .order_by("-priority")
        .prefetch_related("domain", "route", "rate_limit")
    )
    return await render(
        "security/Policies",
        {
            "policies": [
                {
                    "id": p.pk, "name": p.name, "description": p.description,
                    "subject": p.subject, "operator": p.operator, "value": p.value,
                    "action": p.action, "priority": p.priority, "enabled": p.enabled,
                    "sentence": p.sentence, "match_count": p.match_count,
                    "domain": p.domain.hostname if p.domain else None,
                    "route": p.route.name if p.route else None,
                    "rate_limit": p.rate_limit.name if p.rate_limit else None,
                    "scope": "route" if p.route_id else "domain" if p.domain_id else "global",
                }
                for p in rows
            ],
            "routes": [
                {"id": r.pk, "name": r.name}
                for r in await GatewayRoute.filter(gateway_id=gateway.pk).order_by("name")
            ],
            "domains": [
                {"id": d.pk, "hostname": d.hostname}
                for d in await Domain.filter(gateway_id=gateway.pk).order_by("hostname")
            ],
            "rate_limits": [
                {"id": r.pk, "name": r.name, "rate_label": r.rate_label}
                for r in await RateLimit.filter(gateway_id=gateway.pk).order_by("name")
            ],
        },
    )


@action("security.write")
async def policy_save(ctx: HttpContext) -> Any:
    data = await form(ctx)
    gateway = await current_gateway(ctx)
    if gateway is None:
        return not_found()

    name = (data.get("name") or "").strip()
    value = (data.get("value") or "").strip()
    if not name or not value:
        set_flash(ctx, "error", "A policy needs a name and a value to match on.")
        return back(fallback="/security/policies", ctx=ctx)

    subject = (data.get("subject") or "ip").strip()
    if subject in {"ip", "cidr", "client"}:
        try:
            value = normalise_cidr(value)
        except ValueError as error:
            set_flash(ctx, "error", str(error))
            return back(fallback="/security/policies", ctx=ctx)

    policy_id = _int(data, "id")
    existing = (
        await SecurityPolicy.get_or_none(pk=policy_id, gateway_id=gateway.pk) if policy_id else None
    )
    before = audit.snapshot(existing, POLICY_FIELDS)

    values = {
        "name": name,
        "description": data.get("description") or None,
        "subject": subject,
        "operator": (data.get("operator") or "equals").strip(),
        "value": value,
        "action": (data.get("action") or "block").strip(),
        "priority": _int(data, "priority"),
        "enabled": _bool(data, "enabled", True),
        "domain_id": _int(data, "domain_id") or None,
        "route_id": _int(data, "route_id") or None,
        "rate_limit_id": _int(data, "rate_limit_id") or None,
    }

    if existing is None:
        policy = await SecurityPolicy.create(gateway_id=gateway.pk, **values)
        act = "policy.created"
    else:
        for key, val in values.items():
            setattr(existing, key, val)
        await existing.save()
        policy = existing
        act = "policy.updated"

    await audit.record(
        action=act, resource_type="policy", resource_id=policy.pk,
        resource_label=policy.sentence, actor=ctx.state.auth_user, before=before,
        after=audit.snapshot(policy, POLICY_FIELDS), ip=client_ip(ctx),
    )
    set_flash(ctx, "success", f"Policy “{policy.name}” saved. Deploy to apply it.")
    return back(fallback="/security/policies", ctx=ctx)


@action("security.write")
async def policy_delete(ctx: HttpContext) -> Any:
    data = await form(ctx)
    policy = await SecurityPolicy.get_or_none(pk=_int(data, "id"))
    if policy is None:
        return not_found()
    await audit.record(
        action="policy.deleted", resource_type="policy", resource_id=policy.pk,
        resource_label=policy.name, actor=ctx.state.auth_user,
        before=audit.snapshot(policy, POLICY_FIELDS), ip=client_ip(ctx),
    )
    name = policy.name
    await policy.delete()
    set_flash(ctx, "success", f"Policy “{name}” deleted. Deploy to apply it.")
    return back(fallback="/security/policies", ctx=ctx)
