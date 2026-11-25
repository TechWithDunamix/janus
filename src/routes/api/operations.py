"""The API for what operators watch and change day to day from the terminal.

Domains, auth and access policies, upstream edits, gateway maintenance, the request log, security
clients and the security overview. Same guard as the rest of ``/api``: :func:`routes.api.endpoint`
authenticates the bearer token and checks the permission before a handler runs. Changes to what Caddy
serves are saved as desired state; ``POST /api/config/apply`` deploys them (maintenance deploys at once).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sillo.core.http import HttpContext
from sillo.core.routing import Route
from sillo.responses import json
from tortoise.functions import Count

from database.models import (
    Alert,
    Anomaly,
    Client,
    Deployment,
    Domain,
    Gateway,
    GatewayRoute,
    IpRule,
    RateLimit,
    RequestLog,
    SecurityPolicy,
    Upstream,
    UpstreamTarget,
)
from database.models.security import normalise_cidr
from modules.services import analytics, audit, security
from modules.services import configuration as config_service
from modules.services.health import gateway_health
from routes.api import ApiError, _body, _gateway, _window, endpoint, router

DOMAIN_FIELDS = ("hostname", "tls_mode", "enabled")
POLICY_FIELDS = ("name", "subject", "operator", "value", "action", "priority", "enabled")
UPSTREAM_FIELDS = ("name", "policy", "health_path", "enabled", "maintenance")
TLS_MODES = ("auto", "internal", "off")
SUBJECTS = ("ip", "cidr", "api_key", "client", "path", "method", "header", "country", "rate")
OPERATORS = ("equals", "in_cidr", "matches", "exceeds")
ACTIONS = ("allow", "block", "rate_limit", "require_auth", "log")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _limit(ctx: HttpContext, default: int = 100, most: int = 500) -> int:
    try:
        return max(1, min(most, int(ctx.query_params.get("limit") or default)))
    except ValueError as exc:
        raise ApiError("limit must be a number.") from exc


def _choice(value: Any, allowed: tuple[str, ...], name: str) -> str:
    text = str(value).strip()
    if text not in allowed:
        raise ApiError(f"{name} must be one of: {', '.join(allowed)}.")
    return text


def _bool(value: Any) -> bool:
    return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@endpoint("gateway.read")
async def api_overview(ctx: HttpContext) -> Any:
    """``GET /api/overview`` (``gateway.read``) — the gateway's health, open alerts, recent deploys and traffic."""
    gateway = await _gateway(ctx)
    window = _window(ctx)
    alerts = await Alert.filter(gateway_id=gateway.pk, status="open").order_by("-opened_at").limit(10)
    deploys = (
        await Deployment.filter(gateway_id=gateway.pk).order_by("-started_at").limit(8)
        .prefetch_related("version", "actor")
    )
    return json({
        "gateway": {"id": gateway.pk, "name": gateway.name, "slug": gateway.slug,
                    "maintenance": gateway.maintenance},
        "range": window.to_dict(),
        "health": await gateway_health(gateway),
        "traffic": await analytics.overview(gateway, window),
        "alerts": [
            {"id": a.pk, "kind": a.kind, "severity": a.severity, "title": a.title, "detail": a.detail,
             "opened_at": _iso(a.opened_at)}
            for a in alerts
        ],
        "deployments": [
            {"id": d.pk, "version": d.version.version if d.version else None,
             "summary": d.version.summary if d.version else None, "status": d.status, "stage": d.stage,
             "error": d.error, "origin": d.origin, "actor": d.actor.email if d.actor else "system",
             "started_at": _iso(d.started_at), "duration_ms": d.duration_ms}
            for d in deploys
        ],
    })


# ---------------------------------------------------------------------------
# Request log
# ---------------------------------------------------------------------------


@endpoint("analytics.read")
async def api_requests(ctx: HttpContext) -> Any:
    """``GET /api/requests`` (``analytics.read``) — the latest requests through the gateway, newest first.

    Filters: ``status`` (``404`` or a class like ``5xx``), ``method``, ``contains`` (in the path), ``client``
    (an address), ``host``, ``since_id`` (only rows after this id, for following), ``limit`` (1-500).
    """
    gateway = await _gateway(ctx)
    q = ctx.query_params
    rows = RequestLog.filter(gateway_id=gateway.pk)
    status = (q.get("status") or "").lower()
    if status.endswith("xx") and status[:1].isdigit():
        low = int(status[0]) * 100
        rows = rows.filter(status__gte=low, status__lt=low + 100)
    elif status.isdigit():
        rows = rows.filter(status=int(status))
    elif status:
        raise ApiError("status must be a code like 404 or a class like 5xx.")
    if q.get("method"):
        rows = rows.filter(method=q["method"].upper())
    if q.get("contains"):
        rows = rows.filter(path__icontains=q["contains"])
    if q.get("client"):
        rows = rows.filter(client_ip=q["client"])
    if q.get("host"):
        rows = rows.filter(host=q["host"])
    if (q.get("since_id") or "").isdigit():
        rows = rows.filter(id__gt=int(q["since_id"]))
    found = await rows.order_by("-id").limit(_limit(ctx))
    return json({
        "requests": [
            {"id": r.pk, "at": _iso(r.occurred_at), "method": r.method, "host": r.host, "path": r.path,
             "status": r.status, "latency_ms": round(r.latency_ms, 1), "bytes_out": r.bytes_out,
             "client_ip": r.client_ip, "edge_action": r.edge_action or None, "user_agent": r.user_agent}
            for r in found
        ]
    })


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------


async def _domain_json(domain: Domain) -> dict[str, Any]:
    return {
        "id": domain.pk, "hostname": domain.hostname, "tls_mode": domain.tls_mode,
        "tls_status": domain.tls_status, "tls_issuer": domain.tls_issuer,
        "tls_expires_at": _iso(domain.tls_expires_at), "enabled": domain.enabled,
        "routes": await GatewayRoute.filter(domain_id=domain.pk).count(),
    }


@endpoint("domains.read")
async def api_domains(ctx: HttpContext) -> Any:
    """``GET /api/domains`` (``domains.read``) — the gateway's hostnames with TLS state."""
    gateway = await _gateway(ctx)
    rows = await Domain.filter(gateway_id=gateway.pk).order_by("hostname")
    return json({"domains": [await _domain_json(d) for d in rows]})


@endpoint("domains.write")
async def api_domain_create(ctx: HttpContext) -> Any:
    """``POST /api/domains`` (``domains.write``) — add a hostname. Body: ``{"hostname", "tls_mode"?, "enabled"?}``."""
    gateway = await _gateway(ctx)
    data = await _body(ctx)
    hostname = (data.get("hostname") or "").strip().lower()
    if not hostname:
        raise ApiError("A hostname is required.")
    if await Domain.filter(gateway_id=gateway.pk, hostname=hostname).exists():
        raise ApiError(f"{hostname} is already on this gateway.", 409)
    domain = await Domain.create(
        gateway_id=gateway.pk, hostname=hostname,
        tls_mode=_choice(data.get("tls_mode") or "auto", TLS_MODES, "tls_mode"),
        enabled=_bool(data.get("enabled", True)),
    )
    await audit.record(action="domain.created", resource_type="domain", resource_id=domain.pk,
                       resource_label=hostname, actor=ctx.state.api_actor,
                       after=audit.snapshot(domain, DOMAIN_FIELDS), origin="cli")
    return json(await _domain_json(domain), status_code=201)


async def _domain(ctx: HttpContext, domain_id: int) -> Domain:
    gateway = await _gateway(ctx)
    domain = await Domain.get_or_none(pk=domain_id, gateway_id=gateway.pk)
    if domain is None:
        raise ApiError(f"No domain {domain_id}.", 404)
    return domain


@endpoint("domains.write")
async def api_domain_update(ctx: HttpContext, domain_id: int) -> Any:
    """``PATCH /api/domains/{id}`` (``domains.write``) — change ``tls_mode`` or ``enabled``."""
    domain = await _domain(ctx, domain_id)
    data = await _body(ctx)
    before = audit.snapshot(domain, DOMAIN_FIELDS)
    if "tls_mode" in data:
        domain.tls_mode = _choice(data["tls_mode"], TLS_MODES, "tls_mode")
    if "enabled" in data:
        domain.enabled = _bool(data["enabled"])
    await domain.save()
    await audit.record(action="domain.updated", resource_type="domain", resource_id=domain.pk,
                       resource_label=domain.hostname, actor=ctx.state.api_actor, before=before,
                       after=audit.snapshot(domain, DOMAIN_FIELDS), origin="cli")
    return json(await _domain_json(domain))


@endpoint("domains.write")
async def api_domain_delete(ctx: HttpContext, domain_id: int) -> Any:
    """``DELETE /api/domains/{id}`` (``domains.write``) — remove a hostname no route uses."""
    domain = await _domain(ctx, domain_id)
    using = await GatewayRoute.filter(domain_id=domain.pk).count()
    if using:
        raise ApiError(f"{domain.hostname} is used by {using} route(s).", 409)
    await audit.record(action="domain.deleted", resource_type="domain", resource_id=domain.pk,
                       resource_label=domain.hostname, actor=ctx.state.api_actor,
                       before=audit.snapshot(domain, DOMAIN_FIELDS), origin="cli")
    hostname = domain.hostname
    await domain.delete()
    return json({"ok": True, "deleted": hostname})


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


def _policy_json(p: SecurityPolicy) -> dict[str, Any]:
    return {
        "id": p.pk, "name": p.name, "description": p.description, "subject": p.subject,
        "operator": p.operator, "value": p.value, "action": p.action, "priority": p.priority,
        "enabled": p.enabled, "sentence": p.sentence, "match_count": p.match_count,
        "last_matched_at": _iso(p.last_matched_at),
        "domain": p.domain.hostname if p.domain else None,
        "route": p.route.name if p.route else None,
        "rate_limit": p.rate_limit.name if p.rate_limit else None,
        "scope": "route" if p.route_id else "domain" if p.domain_id else "gateway",
    }


@endpoint("security.read")
async def api_policies(ctx: HttpContext) -> Any:
    """``GET /api/policies`` (``security.read``) — policies, highest priority first."""
    gateway = await _gateway(ctx)
    rows = await SecurityPolicy.filter(gateway_id=gateway.pk).order_by("-priority", "name").prefetch_related(
        "domain", "route", "rate_limit")
    return json({"policies": [_policy_json(p) for p in rows]})


async def _policy_values(gateway: Gateway, data: dict[str, Any], current: SecurityPolicy | None) -> dict[str, Any]:
    """Validated policy fields from a request body (missing fields keep their current values)."""

    def pick(name: str, default: Any) -> Any:
        return data[name] if name in data else (getattr(current, name) if current else default)

    values: dict[str, Any] = {
        "name": str(pick("name", "")).strip(),
        "description": pick("description", None) or None,
        "subject": _choice(pick("subject", "ip"), SUBJECTS, "subject"),
        "operator": _choice(pick("operator", "equals"), OPERATORS, "operator"),
        "value": str(pick("value", "")).strip(),
        "action": _choice(pick("action", "block"), ACTIONS, "action"),
        "priority": int(pick("priority", 0) or 0),
        "enabled": _bool(pick("enabled", True)),
    }
    if not values["name"] or not values["value"]:
        raise ApiError("A policy needs a name and a value to match on.")
    if values["subject"] in ("ip", "cidr", "client"):
        try:
            values["value"] = normalise_cidr(values["value"])
        except ValueError as exc:
            raise ApiError(str(exc)) from exc
    for field, model, label in (("domain", Domain, "hostname"), ("route", GatewayRoute, "name"),
                                ("rate_limit", RateLimit, "name")):
        if field not in data:
            continue
        wanted = data[field]
        if wanted in (None, ""):
            values[f"{field}_id"] = None
            continue
        found = await model.filter(gateway_id=gateway.pk, **{label: wanted}).first()
        if found is None and str(wanted).isdigit():
            found = await model.get_or_none(pk=int(wanted), gateway_id=gateway.pk)
        if found is None:
            raise ApiError(f"No {field.replace('_', ' ')} '{wanted}' on this gateway.", 404)
        values[f"{field}_id"] = found.pk
    if values["action"] == "rate_limit" and not (values.get("rate_limit_id") or (current and current.rate_limit_id)):
        raise ApiError("A rate_limit policy needs a rate_limit (its name).")
    return values


@endpoint("security.write")
async def api_policy_create(ctx: HttpContext) -> Any:
    """``POST /api/policies`` (``security.write``) — add a policy: ``IF subject operator value THEN action``.

    Body: ``{"name", "subject", "operator", "value", "action", "priority"?, "enabled"?, "description"?,
    "domain"?, "route"?, "rate_limit"?}`` (domain by hostname, route and rate limit by name).
    """
    gateway = await _gateway(ctx)
    values = await _policy_values(gateway, await _body(ctx), None)
    policy = await SecurityPolicy.create(gateway_id=gateway.pk, created_by_id=ctx.state.api_actor.pk, **values)
    await policy.fetch_related("domain", "route", "rate_limit")
    await audit.record(action="policy.created", resource_type="policy", resource_id=policy.pk,
                       resource_label=policy.sentence, actor=ctx.state.api_actor,
                       after=audit.snapshot(policy, POLICY_FIELDS), origin="cli")
    return json(_policy_json(policy), status_code=201)


async def _policy(ctx: HttpContext, policy_id: int) -> tuple[Gateway, SecurityPolicy]:
    gateway = await _gateway(ctx)
    policy = await SecurityPolicy.get_or_none(pk=policy_id, gateway_id=gateway.pk)
    if policy is None:
        raise ApiError(f"No policy {policy_id}.", 404)
    return gateway, policy


@endpoint("security.write")
async def api_policy_update(ctx: HttpContext, policy_id: int) -> Any:
    """``PATCH /api/policies/{id}`` (``security.write``) — change any policy field; others stay."""
    gateway, policy = await _policy(ctx, policy_id)
    before = audit.snapshot(policy, POLICY_FIELDS)
    for key, value in (await _policy_values(gateway, await _body(ctx), policy)).items():
        setattr(policy, key, value)
    await policy.save()
    await policy.fetch_related("domain", "route", "rate_limit")
    await audit.record(action="policy.updated", resource_type="policy", resource_id=policy.pk,
                       resource_label=policy.sentence, actor=ctx.state.api_actor, before=before,
                       after=audit.snapshot(policy, POLICY_FIELDS), origin="cli")
    return json(_policy_json(policy))


@endpoint("security.write")
async def api_policy_delete(ctx: HttpContext, policy_id: int) -> Any:
    """``DELETE /api/policies/{id}`` (``security.write``) — remove a policy."""
    _, policy = await _policy(ctx, policy_id)
    await audit.record(action="policy.deleted", resource_type="policy", resource_id=policy.pk,
                       resource_label=policy.name, actor=ctx.state.api_actor,
                       before=audit.snapshot(policy, POLICY_FIELDS), origin="cli")
    name = policy.name
    await policy.delete()
    return json({"ok": True, "deleted": name})


# ---------------------------------------------------------------------------
# Upstream edits and gateway maintenance
# ---------------------------------------------------------------------------


@endpoint("upstreams.write")
async def api_upstream_update(ctx: HttpContext, upstream_id: int) -> Any:
    """``PATCH /api/upstreams/{id}`` (``upstreams.write``) — change a pool.

    Body: any of ``policy``, ``health_path``, ``enabled``, ``maintenance``, and ``targets`` (replaces the
    list: ``["host:port", {"dial": "host:port", "weight": 3}, …]``).
    """
    gateway = await _gateway(ctx)
    upstream = await Upstream.get_or_none(pk=upstream_id, gateway_id=gateway.pk)
    if upstream is None:
        raise ApiError(f"No upstream {upstream_id}.", 404)
    data = await _body(ctx)
    before = audit.snapshot(upstream, UPSTREAM_FIELDS)
    if "policy" in data:
        upstream.policy = str(data["policy"]).strip() or upstream.policy
    if "health_path" in data:
        upstream.health_path = (str(data["health_path"] or "").strip() or None)
    for flag in ("enabled", "maintenance"):
        if flag in data:
            setattr(upstream, flag, _bool(data[flag]))
    await upstream.save()
    if "targets" in data:
        targets = []
        for entry in data["targets"] or []:
            dial = (entry.get("dial") if isinstance(entry, dict) else str(entry)).strip()
            weight = int(entry.get("weight", 1)) if isinstance(entry, dict) else 1
            if dial:
                targets.append((dial, max(1, weight)))
        if not targets:
            raise ApiError("An upstream needs at least one target.")
        await UpstreamTarget.filter(upstream_id=upstream.pk).delete()
        for dial, weight in targets:
            await UpstreamTarget.create(upstream_id=upstream.pk, dial=dial, weight=weight)
    await audit.record(action="upstream.updated", resource_type="upstream", resource_id=upstream.pk,
                       resource_label=upstream.name, actor=ctx.state.api_actor, before=before,
                       after=audit.snapshot(upstream, UPSTREAM_FIELDS), origin="cli")
    await upstream.fetch_related("targets")
    return json({
        "id": upstream.pk, "name": upstream.name, "policy": upstream.policy, "enabled": upstream.enabled,
        "maintenance": upstream.maintenance, "health_path": upstream.health_path,
        "targets": [{"dial": t.dial, "weight": t.weight} for t in sorted(upstream.targets, key=lambda t: t.pk)],
    })


@endpoint("gateway.write", "configuration.write")
async def api_gateway_maintenance(ctx: HttpContext) -> Any:
    """``POST /api/gateways/maintenance`` — turn maintenance on or off and deploy at once.

    Body: ``{"on": true|false, "status"?: 503, "body"?: "We'll be right back."}``. The one change that
    deploys by itself: whoever turns it on is responding to something now.
    """
    gateway = await _gateway(ctx)
    data = await _body(ctx)
    if "on" not in data:
        raise ApiError('Send {"on": true} or {"on": false}.')
    before = audit.snapshot(gateway, ("maintenance", "maintenance_status"))
    gateway.maintenance = _bool(data["on"])
    if data.get("status"):
        gateway.maintenance_status = int(data["status"])
    if data.get("body"):
        gateway.maintenance_body = str(data["body"])
    await gateway.save()
    result = await config_service.deploy(
        gateway, actor=ctx.state.api_actor, origin="cli", force=True,
        note="Maintenance mode " + ("enabled" if gateway.maintenance else "disabled"),
    )
    await audit.record(action="gateway.maintenance", resource_type="gateway", resource_id=gateway.pk,
                       resource_label=gateway.name, actor=ctx.state.api_actor, before=before,
                       after=audit.snapshot(gateway, ("maintenance", "maintenance_status")), origin="cli")
    return json({"maintenance": gateway.maintenance, "deploy": result.to_dict()},
                status_code=200 if result.ok else 422)


# ---------------------------------------------------------------------------
# Security: overview and clients
# ---------------------------------------------------------------------------


@endpoint("security.read")
async def api_security_overview(ctx: HttpContext) -> Any:
    """``GET /api/security/overview`` (``security.read``) — blocked and limited traffic, alerts, anomalies."""
    gateway = await _gateway(ctx)
    window = _window(ctx)
    anomalies = await Anomaly.filter(gateway_id=gateway.pk, verdict="unreviewed").order_by("-detected_at").limit(15)
    blocked = (
        await RequestLog.filter(gateway_id=gateway.pk, occurred_at__gte=window.since, status=403)
        .group_by("client_ip").annotate(blocked=Count("id")).values("client_ip", "blocked")
    )
    return json({
        "range": window.to_dict(),
        "summary": await security.security_overview(gateway, window),
        "anomalies": [
            {"id": a.pk, "kind": a.kind, "detail": a.detail, "observed": a.observed, "baseline": a.baseline,
             "deviation": a.deviation, "detected_at": _iso(a.detected_at)}
            for a in anomalies
        ],
        "top_blocked": sorted(blocked, key=lambda r: -r["blocked"])[:15],
    })


def _client_json(c: Client) -> dict[str, Any]:
    return {
        "id": c.pk, "kind": c.kind, "identifier": c.identifier, "label": c.label,
        "organisation": c.organisation, "status": c.status, "request_count": c.request_count,
        "error_count": c.error_count, "blocked_count": c.blocked_count,
        "rate_limited_count": c.rate_limited_count, "bytes_out": c.bytes_out,
        "first_seen_at": _iso(c.first_seen_at), "last_seen_at": _iso(c.last_seen_at),
    }


@endpoint("security.read")
async def api_clients(ctx: HttpContext) -> Any:
    """``GET /api/security/clients`` (``security.read``) — clients by traffic. ``?q=`` searches, ``?status=`` filters."""
    rows = Client.all()
    if ctx.query_params.get("q"):
        rows = rows.filter(identifier__icontains=ctx.query_params["q"].strip())
    status = ctx.query_params.get("status")
    if status:
        rows = rows.filter(status=_choice(status, ("active", "blocked", "allowed"), "status"))
    return json({"clients": [_client_json(c) for c in await rows.order_by("-request_count").limit(_limit(ctx, 200))]})


@endpoint("security.read")
async def api_client_show(ctx: HttpContext, client_id: int) -> Any:
    """``GET /api/security/clients/{id}`` (``security.read``) — one client: totals, rules, status mix, latest requests."""
    gateway = await _gateway(ctx)
    client = await Client.get_or_none(pk=client_id)
    if client is None:
        raise ApiError(f"No client {client_id}.", 404)
    window = _window(ctx)
    base = RequestLog.filter(gateway_id=gateway.pk, client_ip=client.identifier, occurred_at__gte=window.since)
    by_status = await base.group_by("status").annotate(count=Count("id")).values("status", "count")
    recent = await base.order_by("-occurred_at").limit(50)
    rules = await IpRule.filter(gateway_id=gateway.pk, cidr__startswith=client.identifier)
    return json({
        "client": {**_client_json(client), "notes": client.notes,
                   "effective_status": await security.effective_status(gateway, client.identifier)},
        "rules": [{"id": r.pk, "action": r.action, "cidr": r.cidr, "reason": r.reason,
                   "expires_at": _iso(r.expires_at)} for r in rules],
        "by_status": sorted(by_status, key=lambda r: -r["count"]),
        "recent": [{"at": _iso(r.occurred_at), "method": r.method, "path": r.path, "status": r.status,
                    "latency_ms": round(r.latency_ms, 1), "edge_action": r.edge_action or None} for r in recent],
    })


ROUTES = (
    Route("/overview", handler=api_overview, methods=["GET"], name="api.overview"),
    Route("/requests", handler=api_requests, methods=["GET"], name="api.requests"),
    Route("/domains", handler=api_domains, methods=["GET"], name="api.domains"),
    Route("/domains", handler=api_domain_create, methods=["POST"], name="api.domains.create"),
    Route("/domains/{domain_id:int}", handler=api_domain_update, methods=["PATCH"], name="api.domains.update"),
    Route("/domains/{domain_id:int}", handler=api_domain_delete, methods=["DELETE"], name="api.domains.delete"),
    Route("/policies", handler=api_policies, methods=["GET"], name="api.policies"),
    Route("/policies", handler=api_policy_create, methods=["POST"], name="api.policies.create"),
    Route("/policies/{policy_id:int}", handler=api_policy_update, methods=["PATCH"], name="api.policies.update"),
    Route("/policies/{policy_id:int}", handler=api_policy_delete, methods=["DELETE"], name="api.policies.delete"),
    Route("/upstreams/{upstream_id:int}", handler=api_upstream_update, methods=["PATCH"],
          name="api.upstreams.update"),
    Route("/gateways/maintenance", handler=api_gateway_maintenance, methods=["POST"], name="api.gateways.maintenance"),
    Route("/security/overview", handler=api_security_overview, methods=["GET"], name="api.security.overview"),
    Route("/security/clients", handler=api_clients, methods=["GET"], name="api.security.clients"),
    Route("/security/clients/{client_id:int}", handler=api_client_show, methods=["GET"],
          name="api.security.clients.show"),
)

for _route in ROUTES:
    router.add_route(_route)
