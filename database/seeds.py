"""Development data.

Everything this module creates is marked `simulated=True` on the rows where
that flag exists, and the gateway it creates is a simulated one. That is not a
formality: an observability platform showing invented traffic as though it were
observation would be worse than one showing nothing, so the flag travels with
the data into the analytics queries and out to the UI, which says so on screen.

The traffic it generates is shaped rather than random. A real gateway has a
daily curve, a few routes carrying most of the volume, one route quietly
failing, one route slow at the tail, a scanner probing for `.env`, and a client
hammering a rate limit. Uniform random noise would exercise the queries and
demonstrate nothing, because every ranking would come out flat.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from sillo.permissions import Group

from app.authz import ensure_roles
from app.services import apikeys
from database.models import (
    LATENCY_BUCKETS_MS,
    ApiKey,
    AuditEvent,
    Client,
    Domain,
    Gateway,
    GatewayRoute,
    IpRule,
    RateLimit,
    RequestLog,
    RouteRollup,
    SecurityPolicy,
    Upstream,
    UpstreamTarget,
    User,
    bucket_for,
)

__all__ = ["seed", "seed_traffic"]

#: Fixed, so a demo looks the same twice and a screenshot can be reproduced.
SEED = 20260905


ROUTE_SPECS: tuple[dict[str, Any], ...] = (
    # (name, path, methods, upstream, share of traffic, 5xx rate, latency profile)
    {
        "name": "List products", "path": "/api/products*", "methods": "GET",
        "upstream": "catalog-service", "share": 0.30, "error_rate": 0.004,
        "latency": (35, 0.55), "priority": 100, "auth_policy": "api_key",
    },
    {
        "name": "Create order", "path": "/api/orders*", "methods": "POST,GET",
        "upstream": "orders-service", "share": 0.18, "error_rate": 0.124,
        "latency": (420, 1.10), "priority": 100, "auth_policy": "api_key",
    },
    {
        "name": "Customer accounts", "path": "/api/accounts*", "methods": "",
        "upstream": "accounts-service", "share": 0.14, "error_rate": 0.006,
        "latency": (60, 0.6), "priority": 90, "auth_policy": "jwt",
    },
    {
        "name": "Search", "path": "/api/search*", "methods": "GET",
        "upstream": "search-service", "share": 0.12, "error_rate": 0.018,
        "latency": (240, 0.95), "priority": 90, "auth_policy": "api_key",
    },
    {
        "name": "Payments webhook", "path": "/hooks/payments*", "methods": "POST",
        "upstream": "payments-service", "share": 0.05, "error_rate": 0.031,
        "latency": (95, 0.7), "priority": 120, "auth_policy": "public",
    },
    {
        "name": "Media", "path": "/media/*", "methods": "GET",
        "upstream": "media-service", "share": 0.15, "error_rate": 0.002,
        "latency": (22, 0.5), "priority": 80, "auth_policy": "public",
    },
    {
        "name": "Health", "path": "/healthz", "methods": "GET",
        "upstream": "catalog-service", "share": 0.06, "error_rate": 0.0,
        "latency": (3, 0.3), "priority": 200, "auth_policy": "public",
    },
)

UPSTREAM_SPECS: tuple[tuple[str, tuple[tuple[str, int], ...], str | None], ...] = (
    ("catalog-service", (("10.0.0.11:8000", 5), ("10.0.0.12:8000", 3), ("10.0.0.13:8000", 2)), "/healthz"),
    ("orders-service", (("10.0.1.11:8000", 1), ("10.0.1.12:8000", 1)), "/healthz"),
    ("accounts-service", (("10.0.2.11:8000", 1), ("10.0.2.12:8000", 1)), "/healthz"),
    ("search-service", (("10.0.3.11:9200", 1),), None),
    ("payments-service", (("10.0.4.11:8000", 1), ("10.0.4.12:8000", 1)), "/healthz"),
    ("media-service", (("10.0.5.11:8080", 4), ("10.0.5.12:8080", 4)), None),
    ("orders-canary", (("10.0.1.90:8000", 1),), "/healthz"),
)

CLIENT_SPECS: tuple[tuple[str, str, float], ...] = (
    ("203.0.113.10", "Acme Retail (partner)", 0.22),
    ("203.0.113.11", "Acme Retail (partner)", 0.14),
    ("198.51.100.24", "Northwind mobile app", 0.18),
    ("198.51.100.25", "Northwind web", 0.12),
    ("192.0.2.77", "Internal batch importer", 0.10),
    ("203.0.113.200", "Unidentified scanner", 0.08),
    ("198.51.100.90", "Aggressive integration client", 0.16),
)


async def seed(*, fresh: bool = False, hours: int = 48) -> dict[str, Any]:
    """Create the demo estate. Idempotent unless `fresh` is passed."""
    random.seed(SEED)

    if fresh:
        for model in (
            RequestLog, RouteRollup, AuditEvent, ApiKey, SecurityPolicy, RateLimit,
            IpRule, Client, GatewayRoute, UpstreamTarget, Upstream, Domain, Gateway,
        ):
            await model.all().delete()

    roles = await ensure_roles()
    owner = await _owner(roles)
    gateway = await _gateway()
    domains = await _domains(gateway)
    upstreams = await _upstreams(gateway)
    routes = await _routes(gateway, domains, upstreams)
    await _security(gateway, routes, owner)
    await _keys(owner)
    counts = await seed_traffic(gateway, routes, hours=hours)

    return {
        "owner": owner.email,
        "gateway": gateway.name,
        "routes": len(routes),
        "upstreams": len(upstreams),
        **counts,
    }


async def _owner(roles: dict[str, Group]) -> User:
    user = await User.get_or_none(email="owner@janus.local")
    if user is None:
        user = User(
            email="owner@janus.local",
            username="owner@janus.local",
            full_name="Demo Owner",
            title="Platform SRE",
            is_active=True,
            is_superuser=True,
        )
        user.set_password("janus-development-owner")
        await user.save()
        await roles["Owner"].add_user(user)

    # One account per role, so the RBAC can actually be tried out rather than
    # described. Every one carries the same obvious development password.
    for role_name, email in (
        ("Administrator", "admin@janus.local"),
        ("Operator", "operator@janus.local"),
        ("Analyst", "analyst@janus.local"),
        ("Viewer", "viewer@janus.local"),
    ):
        if await User.get_or_none(email=email):
            continue
        member = User(
            email=email, username=email, full_name=f"Demo {role_name}", is_active=True
        )
        member.set_password(f"janus-development-{role_name.lower()}")
        await member.save()
        await roles[role_name].add_user(member)

    return user


async def _gateway() -> Gateway:
    gateway = await Gateway.get_or_none(slug="edge")
    if gateway is None:
        gateway = await Gateway.create(
            name="Edge gateway",
            slug="edge",
            description="Public API edge. Demo data.",
            listen=":8080",
            region="eu-west",
            enabled=True,
            simulated=True,
        )
    return gateway


async def _domains(gateway: Gateway) -> dict[str, Domain]:
    out: dict[str, Domain] = {}
    for hostname, mode in (("api.janus.local", "internal"), ("cdn.janus.local", "internal")):
        domain = await Domain.get_or_none(gateway_id=gateway.pk, hostname=hostname)
        if domain is None:
            domain = await Domain.create(
                gateway_id=gateway.pk, hostname=hostname, tls_mode=mode,
                tls_status="issued", tls_issuer="Caddy Local Authority",
                tls_expires_at=datetime.now(UTC) + timedelta(days=60),
            )
        out[hostname] = domain
    return out


async def _upstreams(gateway: Gateway) -> dict[str, Upstream]:
    out: dict[str, Upstream] = {}
    for name, targets, health in UPSTREAM_SPECS:
        upstream = await Upstream.get_or_none(gateway_id=gateway.pk, slug=name)
        if upstream is None:
            upstream = await Upstream.create(
                gateway_id=gateway.pk, name=name, slug=name,
                policy="weighted_round_robin", health_path=health,
                max_fails=3, fail_duration_seconds=30,
            )
            for dial, weight in targets:
                await UpstreamTarget.create(
                    upstream_id=upstream.pk, dial=dial, weight=weight,
                    # One target down, so the health screen and the upstream
                    # alert have something true to show.
                    healthy=dial != "10.0.1.12:8000",
                    last_error="3 recent failures" if dial == "10.0.1.12:8000" else None,
                    last_checked_at=datetime.now(UTC),
                )
        out[name] = upstream
    return out


async def _routes(
    gateway: Gateway, domains: dict[str, Domain], upstreams: dict[str, Upstream]
) -> list[GatewayRoute]:
    api = domains["api.janus.local"]
    cdn = domains["cdn.janus.local"]
    out: list[GatewayRoute] = []

    for spec in ROUTE_SPECS:
        slug = spec["name"].lower().replace(" ", "-")
        route = await GatewayRoute.get_or_none(gateway_id=gateway.pk, slug=slug)
        if route is None:
            route = await GatewayRoute.create(
                gateway_id=gateway.pk,
                name=spec["name"],
                slug=slug,
                path=spec["path"],
                methods=spec["methods"],
                priority=spec["priority"],
                domain_id=(cdn.pk if spec["name"] == "Media" else api.pk),
                upstream_id=upstreams[spec["upstream"]].pk,
                auth_policy=spec["auth_policy"],
                timeout_seconds=30,
                retries=2 if spec["name"] == "Create order" else 0,
                strip_prefix="/api" if spec["path"].startswith("/api") else None,
                # A canary on the failing route, so traffic splitting is
                # visible on a route where it would actually be used.
                canary_upstream_id=(
                    upstreams["orders-canary"].pk if spec["name"] == "Create order" else None
                ),
                canary_percent=10 if spec["name"] == "Create order" else 0,
            )
        out.append(route)

    # A catch-all, last, so unmatched traffic is answered rather than falling
    # through to whatever Caddy would do.
    catch_all = await GatewayRoute.get_or_none(gateway_id=gateway.pk, slug="catch-all")
    if catch_all is None:
        await GatewayRoute.create(
            gateway_id=gateway.pk, name="Catch-all", slug="catch-all", path="/*",
            priority=0, action="static", static_status=404,
            static_body="No route matches this request.",
        )
    return out


async def _security(
    gateway: Gateway, routes: list[GatewayRoute], owner: User
) -> None:
    for identifier, label, _ in CLIENT_SPECS:
        await Client.get_or_create(
            kind="ip",
            identifier=identifier,
            defaults={
                "label": label,
                "first_seen_at": datetime.now(UTC) - timedelta(days=30),
                "last_seen_at": datetime.now(UTC),
            },
        )

    if not await IpRule.filter(gateway_id=gateway.pk).exists():
        await IpRule.create(
            gateway_id=gateway.pk, action="block", cidr="203.0.113.200/32",
            reason="Repeated probing for /.env and /admin", created_by_id=owner.pk,
        )
        await IpRule.create(
            gateway_id=gateway.pk, action="block", cidr="198.51.100.90/32",
            reason="Sustained rate-limit abuse",
            expires_at=datetime.now(UTC) + timedelta(hours=6), created_by_id=owner.pk,
        )
        # Allow rules are deliberately *route-scoped* here, and that is not a
        # detail. An allow rule makes its scope deny-by-default for everything
        # outside the allowed ranges — that is what an allowlist means — so a
        # *global* one on a demo gateway would fence off every caller and the
        # whole estate would answer 403. Scoped to an internal route, the fence
        # is exactly what an operator wants and nothing else is affected.
        internal = next((r for r in routes if r.slug == "customer-accounts"), None)
        if internal is not None:
            await IpRule.create(
                gateway_id=gateway.pk, action="allow", cidr="10.0.0.0/8",
                reason="Internal network only", route_id=internal.pk,
                created_by_id=owner.pk,
            )
            # Both loopbacks. A browser or curl reaching `localhost` on a
            # modern machine arrives as `::1`, not `127.0.0.1`, so an
            # allowlist with only the IPv4 range refuses every local request —
            # and looks, from the outside, exactly like a broken rule.
            for loopback in ("127.0.0.0/8", "::1/128"):
                await IpRule.create(
                    gateway_id=gateway.pk, action="allow", cidr=loopback,
                    reason="Loopback, for local testing", route_id=internal.pk,
                    created_by_id=owner.pk,
                )

    if not await RateLimit.filter(gateway_id=gateway.pk).exists():
        orders = next((r for r in routes if r.slug == "create-order"), None)
        await RateLimit.create(
            gateway_id=gateway.pk, name="Per-key default", key="api_key",
            limit=1000, window_seconds=60, burst=100,
        )
        await RateLimit.create(
            gateway_id=gateway.pk, name="Order creation", key="api_key",
            limit=10, window_seconds=1, burst=5,
            route_id=orders.pk if orders else None,
        )
        await RateLimit.create(
            gateway_id=gateway.pk, name="Anonymous per-IP", key="ip",
            limit=100, window_seconds=60,
        )

    if not await SecurityPolicy.filter(gateway_id=gateway.pk).exists():
        await SecurityPolicy.create(
            gateway_id=gateway.pk, name="Block dotfile probing",
            description="Scanners fishing for leaked configuration.",
            subject="path", operator="matches", value="/.env*", action="block",
            priority=100, created_by_id=owner.pk,
        )
        await SecurityPolicy.create(
            gateway_id=gateway.pk, name="Block admin probing",
            subject="path", operator="matches", value="/wp-admin*", action="block",
            priority=99, created_by_id=owner.pk,
        )
        await SecurityPolicy.create(
            gateway_id=gateway.pk, name="Allow internal network",
            subject="cidr", operator="in_cidr", value="10.0.0.0/8", action="allow",
            priority=200, created_by_id=owner.pk,
        )


async def _keys(owner: User) -> None:
    if await ApiKey.all().exists():
        return
    for name, scopes in (
        ("Acme Retail — production", "products.read,orders.write"),
        ("Northwind mobile", "products.read,accounts.read"),
        ("Internal batch importer", "products.write,orders.read"),
    ):
        client = await Client.filter(label__icontains=name.split(" ")[0]).first()
        # The secret is discarded here on purpose. A seeder that stored demo
        # secrets would be a seeder that put working credentials in a fixture.
        await apikeys.create(name=name, client=client, scopes=scopes, actor=owner)


# ---------------------------------------------------------------------------
# Traffic
# ---------------------------------------------------------------------------


async def seed_traffic(
    gateway: Gateway, routes: list[GatewayRoute], *, hours: int = 48
) -> dict[str, int]:
    """Generate shaped traffic, as rollups plus a slice of request rows.

    Rollups cover the whole range because that is what the charts read.
    Request rows cover only the last few hours, matching what a real retention
    policy keeps — so the screens that fall back to request-level detail behave
    the way they will in production rather than having perfect data forever.
    """
    if await RouteRollup.filter(gateway_id=gateway.pk).exists():
        return {"rollups": 0, "requests": 0}

    random.seed(SEED)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    start = now - timedelta(hours=hours)

    rollups: list[RouteRollup] = []
    requests: list[RequestLog] = []
    clients = {c.identifier: c for c in await Client.all()}
    request_cutoff = now - timedelta(hours=6)

    minutes = int((now - start).total_seconds() // 60)
    for step in range(minutes):
        minute = start + timedelta(minutes=step)
        # A daily curve: quiet at 04:00, busy at 14:00. Plus a burst two hours
        # ago, so "traffic spike" anomaly detection has something to find.
        hour_factor = 0.35 + 0.65 * math.sin((minute.hour - 4) / 24 * 2 * math.pi) ** 2
        burst = 2.4 if timedelta(hours=1, minutes=50) < (now - minute) < timedelta(hours=2) else 1.0
        base = int(220 * hour_factor * burst * random.uniform(0.85, 1.15))

        for spec, route in zip(ROUTE_SPECS, routes, strict=False):
            count = max(0, int(base * spec["share"] * random.uniform(0.8, 1.2)))
            if count == 0:
                continue

            # The failing route gets worse over the last four hours, so the
            # "errors increasing" question has a visible answer.
            degradation = 1.0
            if spec["name"] == "Create order" and (now - minute) < timedelta(hours=4):
                degradation = 2.2

            errors_5xx = _binomial(count, min(0.95, spec["error_rate"] * degradation))
            errors_4xx = _binomial(count - errors_5xx, 0.03)
            rate_limited = _binomial(count, 0.012 if spec["share"] > 0.15 else 0.002)
            blocked = _binomial(count, 0.004)
            ok = max(0, count - errors_5xx - errors_4xx)

            median, spread = spec["latency"]
            if spec["name"] == "Create order" and degradation > 1:
                median *= 2.0
            histogram = [0] * (len(LATENCY_BUCKETS_MS) + 1)
            latency_sum = 0.0
            latency_max = 0.0
            for _ in range(count):
                value = random.lognormvariate(math.log(max(median, 1)), spread)
                latency_sum += value
                latency_max = max(latency_max, value)
                histogram[bucket_for(value)] += 1

            rollups.append(
                RouteRollup(
                    gateway_id=gateway.pk, route_id=route.pk, minute=minute,
                    requests=count,
                    status_2xx=ok, status_3xx=0,
                    status_4xx=errors_4xx + rate_limited + blocked,
                    status_5xx=errors_5xx,
                    status_401=_binomial(errors_4xx, 0.2),
                    status_403=blocked,
                    status_404=_binomial(errors_4xx, 0.35),
                    status_429=rate_limited,
                    status_502=_binomial(errors_5xx, 0.5),
                    status_503=_binomial(errors_5xx, 0.2),
                    status_504=_binomial(errors_5xx, 0.15),
                    blocked=blocked, rate_limited=rate_limited,
                    timeouts=_binomial(errors_5xx, 0.15),
                    bytes_in=count * random.randint(200, 900),
                    bytes_out=count * random.randint(800, 12000),
                    latency_sum_ms=latency_sum, latency_max_ms=latency_max,
                    latency_buckets=histogram,
                    distinct_clients=min(len(CLIENT_SPECS), max(1, count // 12)),
                    simulated=True,
                )
            )

            if minute >= request_cutoff:
                requests.extend(
                    _request_rows(gateway, route, spec, minute, count, clients)
                )

    # Scanner traffic against no route at all, which is what a 404-scanning
    # check has to be able to see.
    for step in range(0, minutes, 7):
        minute = start + timedelta(minutes=step)
        hits = random.randint(3, 18)
        rollups.append(
            RouteRollup(
                gateway_id=gateway.pk, route_id=None, minute=minute,
                requests=hits, status_4xx=hits, status_404=hits,
                bytes_in=hits * 120, bytes_out=hits * 300,
                latency_sum_ms=hits * 2.0, latency_max_ms=6.0,
                latency_buckets=[hits] + [0] * len(LATENCY_BUCKETS_MS),
                distinct_clients=1, simulated=True,
            )
        )

    await RouteRollup.bulk_create(rollups, batch_size=500)
    await RequestLog.bulk_create(requests, batch_size=500)
    return {"rollups": len(rollups), "requests": len(requests)}


def _request_rows(
    gateway: Gateway,
    route: GatewayRoute,
    spec: dict[str, Any],
    minute: datetime,
    count: int,
    clients: dict[str, Client],
) -> list[RequestLog]:
    """A sample of individual requests for one route-minute.

    Sampled rather than complete: writing every request row for two days of
    demo traffic would be millions of rows to show a table that displays fifty.
    The rollups carry the true totals; these exist so the request-level screens
    have something real to page through.
    """
    rows: list[RequestLog] = []
    sample = min(count, 6)
    identifiers = [c[0] for c in CLIENT_SPECS]
    weights = [c[2] for c in CLIENT_SPECS]

    for index in range(sample):
        ip = random.choices(identifiers, weights=weights, k=1)[0]
        roll = random.random()
        if roll < spec["error_rate"]:
            status = random.choice((500, 502, 503, 504))
            edge = ""
        elif roll < spec["error_rate"] + 0.012:
            status, edge = 429, "rate_limited"
        elif ip == "203.0.113.200":
            status, edge = 403, "blocked"
        else:
            status, edge = 200, ""

        median, spread = spec["latency"]
        latency = random.lognormvariate(math.log(max(median, 1)), spread)
        method = (spec["methods"].split(",")[0] if spec["methods"] else "GET") or "GET"

        rows.append(
            RequestLog(
                gateway_id=gateway.pk,
                route_id=route.pk,
                upstream_id=route.upstream_id,
                client_id=clients[ip].pk if ip in clients else None,
                occurred_at=minute + timedelta(seconds=index * 7),
                method=method,
                host="cdn.janus.local" if spec["name"] == "Media" else "api.janus.local",
                path=spec["path"].replace("*", str(random.randint(1000, 9999))),
                status=status,
                latency_ms=latency,
                bytes_in=random.randint(200, 900),
                bytes_out=random.randint(800, 12000),
                client_ip=ip,
                user_agent=random.choice(
                    (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                        "NorthwindApp/4.2 (iOS 18.2)",
                        "python-httpx/0.28.1",
                        "curl/8.7.1",
                    )
                ),
                protocol="HTTP/2.0",
                edge_action=edge,
                simulated=True,
            )
        )
    return rows


def _binomial(trials: int, probability: float) -> int:
    """A binomial draw without numpy.

    Exact for the small counts this seeder deals in, and it keeps the project's
    dependency list to what the running application actually needs.
    """
    if trials <= 0 or probability <= 0:
        return 0
    return sum(1 for _ in range(trials) if random.random() < probability)
