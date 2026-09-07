"""Queries behind every number Janus shows.

The whole module reads :class:`~database.models.RouteRollup` where it can and
:class:`~database.models.RequestLog` only where it must. That split is the
performance story: a 30-day chart over rollups touches a few thousand rows, and
the same chart over request rows touches every request the gateway served.
Request rows are used for exactly the questions rollups cannot answer — which
client, which user agent, which individual request — and those are always
bounded by a short window.

**Percentiles.** Rollups carry a latency histogram, not an average, because
averages do not compose: the mean of per-minute means is not the mean, and a
P95 of P95s is not a percentile at all. :func:`percentiles` sums the histograms
across whatever range is asked for and interpolates inside the bucket the
target rank falls in. The answer is therefore accurate to bucket width, which
:data:`LATENCY_BUCKETS_MS` chooses to be narrow exactly where an API gateway
lives. Anywhere a percentile is shown, it is a percentile of the whole range,
computed once.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from tortoise.functions import Count, Max, Sum

from app.config import config
from database.models import (
    LATENCY_BUCKETS_MS,
    Client,
    Gateway,
    GatewayRoute,
    RequestLog,
    RouteRollup,
    Upstream,
)

__all__ = [
    "RANGES",
    "Window",
    "begin_request",
    "bandwidth_series",
    "client_breakdown",
    "error_breakdown",
    "overview",
    "percentiles",
    "problematic_routes",
    "range_bounds",
    "route_detail",
    "route_table",
    "series",
    "slow_routes",
    "status_distribution",
    "traffic_breakdown",
]


#: The ranges the UI offers, with the bucket width each one draws at.
#:
#: The widths are chosen so every range produces between 60 and 180 points: too
#: few and a spike disappears into a bucket, too many and the chart is noise
#: and the query is slow. 30 days at one hour is 720 points, which is the one
#: deliberate exception — a month of hourly detail is worth the extra pixels.
RANGES: dict[str, tuple[str, timedelta, timedelta]] = {
    "15m": ("Last 15 minutes", timedelta(minutes=15), timedelta(minutes=1)),
    "1h": ("Last hour", timedelta(hours=1), timedelta(minutes=1)),
    "6h": ("Last 6 hours", timedelta(hours=6), timedelta(minutes=5)),
    "24h": ("Last 24 hours", timedelta(days=1), timedelta(minutes=15)),
    "7d": ("Last 7 days", timedelta(days=7), timedelta(hours=1)),
    "30d": ("Last 30 days", timedelta(days=30), timedelta(hours=1)),
}


@dataclass
class Window:
    """A resolved time range."""

    key: str
    label: str
    since: datetime
    until: datetime
    bucket: timedelta

    @property
    def seconds(self) -> float:
        return max(1.0, (self.until - self.since).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "since": self.since.isoformat(),
            "until": self.until.isoformat(),
            "bucket_seconds": int(self.bucket.total_seconds()),
        }


def range_bounds(key: str = "24h", *, start: str | None = None, end: str | None = None) -> Window:
    """Resolve a range key, or a custom pair of ISO timestamps.

    An unparseable custom range falls back to 24 hours rather than raising: a
    malformed query string in a URL someone shared should show a dashboard, not
    an error page.
    """
    if key == "custom" and start and end:
        try:
            since = datetime.fromisoformat(start)
            until = datetime.fromisoformat(end)
            if since.tzinfo is None:
                since = since.replace(tzinfo=UTC)
            if until.tzinfo is None:
                until = until.replace(tzinfo=UTC)
            if until > since:
                span = until - since
                bucket = _bucket_for_span(span)
                return Window("custom", _span_label(since, until), since, until, bucket)
        except ValueError:
            pass
        key = "24h"

    label, span, bucket = RANGES.get(key, RANGES["24h"])
    until = datetime.now(UTC)
    return Window(key if key in RANGES else "24h", label, until - span, until, bucket)


def _bucket_for_span(span: timedelta) -> timedelta:
    """A bucket width giving a custom range a sensible number of points."""
    seconds = span.total_seconds()
    for width in (60, 300, 900, 3600, 21600, 86400):
        if seconds / width <= 180:
            return timedelta(seconds=width)
    return timedelta(days=1)


def _span_label(since: datetime, until: datetime) -> str:
    return f"{since:%d %b %H:%M} – {until:%d %b %H:%M}"


# ---------------------------------------------------------------------------
# Percentiles
# ---------------------------------------------------------------------------


def percentiles(histogram: list[int], targets: tuple[float, ...] = (50, 95, 99)) -> dict[str, float]:
    """Latency percentiles, in milliseconds, from a summed histogram.

    Linear interpolation inside the bucket the target rank falls into. The
    first bucket is treated as spanning zero to its bound, and the overflow
    bucket reports its lower bound — an honest floor, since a request slower
    than the largest bound could be any amount slower and pretending to know
    would be inventing precision.
    """
    total = sum(histogram or [])
    if not total:
        return {f"p{int(t)}": 0.0 for t in targets}

    bounds = list(LATENCY_BUCKETS_MS)
    out: dict[str, float] = {}
    for target in targets:
        rank = total * (target / 100.0)
        cumulative = 0
        value = float(bounds[-1])
        for index, count in enumerate(histogram):
            if cumulative + count >= rank:
                lower = 0.0 if index == 0 else float(bounds[index - 1])
                if index >= len(bounds):
                    value = float(bounds[-1])
                else:
                    upper = float(bounds[index])
                    within = (rank - cumulative) / count if count else 0.0
                    value = lower + (upper - lower) * within
                break
            cumulative += count
        out[f"p{int(target)}"] = round(value, 2)
    return out


def _sum_histograms(rows: list[dict[str, Any]]) -> list[int]:
    total = [0] * (len(LATENCY_BUCKETS_MS) + 1)
    for row in rows:
        buckets = row["latency_buckets"] or []
        for index, count in enumerate(buckets[: len(total)]):
            total[index] += int(count or 0)
    return total


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


async def overview(gateway: Gateway, window: Window) -> dict[str, Any]:
    """The headline numbers, and how each compares with the previous window.

    Every figure carries its comparison because a rate without a direction is
    half a fact: 2.3% errors is fine or an incident depending entirely on what
    it was an hour ago. The previous window is the same length immediately
    before this one.
    """
    rows = await _rollups(gateway, window)
    previous_window = Window(
        window.key,
        window.label,
        window.since - (window.until - window.since),
        window.since,
        window.bucket,
    )
    previous = await _rollups(gateway, previous_window)

    current = _aggregate(rows, window.seconds)
    prior = _aggregate(previous, previous_window.seconds)

    active_routes = await GatewayRoute.filter(gateway_id=gateway.pk, enabled=True).count()
    total_routes = await GatewayRoute.filter(gateway_id=gateway.pk).count()
    active_upstreams = await Upstream.filter(gateway_id=gateway.pk, enabled=True).count()

    return {
        **current,
        "active_routes": active_routes,
        "total_routes": total_routes,
        "active_upstreams": active_upstreams,
        "previous": prior,
        "deltas": {
            key: _delta(current.get(key), prior.get(key))
            for key in ("requests", "error_rate", "rps", "p95", "avg_latency_ms", "bytes_out")
        },
        # True when any rollup in the range came from seeded demo data, so the
        # UI can say so on the screen rather than presenting invented traffic
        # as observation.
        "simulated": any(row["simulated"] for row in rows),
    }


def _aggregate(rows: list[dict[str, Any]], seconds: float) -> dict[str, Any]:
    requests = sum(r["requests"] for r in rows)
    errors_5xx = sum(r["status_5xx"] for r in rows)
    errors_4xx = sum(r["status_4xx"] for r in rows)
    successful = sum(r["status_2xx"] for r in rows) + sum(r["status_3xx"] for r in rows)
    latency_sum = sum(r["latency_sum_ms"] for r in rows)
    histogram = _sum_histograms(rows)

    return {
        "requests": requests,
        "successful": successful,
        "errors_4xx": errors_4xx,
        "errors_5xx": errors_5xx,
        "error_rate": round((errors_4xx + errors_5xx) / requests, 5) if requests else 0.0,
        "server_error_rate": round(errors_5xx / requests, 5) if requests else 0.0,
        "rps": round(requests / seconds, 3),
        "avg_latency_ms": round(latency_sum / requests, 2) if requests else 0.0,
        "max_latency_ms": round(max((r["latency_max_ms"] for r in rows), default=0.0), 2),
        **percentiles(histogram),
        "bytes_in": sum(r["bytes_in"] for r in rows),
        "bytes_out": sum(r["bytes_out"] for r in rows),
        "blocked": sum(r["blocked"] for r in rows),
        "rate_limited": sum(r["rate_limited"] for r in rows),
        "timeouts": sum(r["timeouts"] for r in rows),
    }


def _delta(current: Any, previous: Any) -> dict[str, Any] | None:
    """Change against the previous window, as an absolute and a percentage.

    `None` when there is no previous figure to compare against — showing
    "+100%" because the previous window was empty is a number that means
    nothing and reads as though something doubled.
    """
    if current is None or previous is None:
        return None
    try:
        current_value, previous_value = float(current), float(previous)
    except (TypeError, ValueError):
        return None
    if previous_value == 0:
        return {"absolute": round(current_value, 4), "percent": None}
    return {
        "absolute": round(current_value - previous_value, 4),
        "percent": round((current_value - previous_value) / previous_value * 100, 1),
    }


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------


async def series(gateway: Gateway, window: Window, route: GatewayRoute | None = None) -> list[dict]:
    """Traffic over time, bucketed to the window's resolution.

    Buckets with no traffic are emitted as zeros rather than omitted. A line
    chart that skips empty buckets draws a straight line across an outage,
    which is the single most misleading thing a traffic chart can do.
    """
    rows = await _rollups(gateway, window, route=route)
    width = int(window.bucket.total_seconds())
    buckets: dict[int, dict[str, Any]] = {}

    start = int(window.since.timestamp() // width) * width
    end = int(window.until.timestamp() // width) * width
    for stamp in range(start, end + width, width):
        buckets[stamp] = {
            "t": datetime.fromtimestamp(stamp, tz=UTC).isoformat(),
            "requests": 0, "errors_4xx": 0, "errors_5xx": 0, "successful": 0,
            "bytes_out": 0, "blocked": 0, "rate_limited": 0,
            "latency_sum": 0.0, "histogram": [0] * (len(LATENCY_BUCKETS_MS) + 1),
        }

    for row in rows:
        stamp = int(row["minute"].timestamp() // width) * width
        bucket = buckets.get(stamp)
        if bucket is None:
            continue
        bucket["requests"] += row["requests"]
        bucket["errors_4xx"] += row["status_4xx"]
        bucket["errors_5xx"] += row["status_5xx"]
        bucket["successful"] += row["status_2xx"] + row["status_3xx"]
        bucket["bytes_out"] += row["bytes_out"]
        bucket["blocked"] += row["blocked"]
        bucket["rate_limited"] += row["rate_limited"]
        bucket["latency_sum"] += row["latency_sum_ms"]
        for index, count in enumerate((row["latency_buckets"] or [])[: len(bucket["histogram"])]):
            bucket["histogram"][index] += int(count or 0)

    out: list[dict[str, Any]] = []
    for stamp in sorted(buckets):
        bucket = buckets[stamp]
        requests = bucket["requests"]
        pct = percentiles(bucket["histogram"])
        out.append(
            {
                "t": bucket["t"],
                "requests": requests,
                "rps": round(requests / width, 3),
                "successful": bucket["successful"],
                "errors_4xx": bucket["errors_4xx"],
                "errors_5xx": bucket["errors_5xx"],
                "error_rate": round(
                    (bucket["errors_4xx"] + bucket["errors_5xx"]) / requests, 5
                ) if requests else 0.0,
                "avg_latency_ms": round(bucket["latency_sum"] / requests, 2) if requests else 0.0,
                "p95": pct["p95"],
                "p99": pct["p99"],
                "bytes_out": bucket["bytes_out"],
                "blocked": bucket["blocked"],
                "rate_limited": bucket["rate_limited"],
            }
        )
    return out


async def bandwidth_series(gateway: Gateway, window: Window) -> list[dict[str, Any]]:
    points = await series(gateway, window)
    return [{"t": p["t"], "bytes_out": p["bytes_out"]} for p in points]


# ---------------------------------------------------------------------------
# Route rankings
# ---------------------------------------------------------------------------


async def route_table(gateway: Gateway, window: Window) -> list[dict[str, Any]]:
    """Every route with traffic in the window, with its full statistics."""
    rows = await _rollups(gateway, window)
    routes = {r.pk: r for r in await GatewayRoute.filter(gateway_id=gateway.pk).prefetch_related("upstream", "domain")}

    grouped: dict[int | None, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["route_id"], []).append(row)

    out: list[dict[str, Any]] = []
    for route_id, group in grouped.items():
        route = routes.get(route_id) if route_id else None
        stats = _aggregate(group, window.seconds)
        out.append(
            {
                "id": route_id,
                "name": route.name if route else "Unmatched traffic",
                "slug": route.slug if route else None,
                "label": (
                    f"{route.method_label} {route.path}" if route else "No matching route"
                ),
                "method": route.method_label if route else "—",
                "path": route.path if route else "—",
                "domain": route.domain.hostname if route and route.domain else None,
                "upstream": route.upstream.name if route and route.upstream else None,
                "enabled": route.enabled if route else False,
                **stats,
                "health": _health_of(stats),
            }
        )
    out.sort(key=lambda r: -r["requests"])
    return out


def _health_of(stats: dict[str, Any]) -> str:
    """A route's verdict: `critical`, `warning` or `healthy`.

    Thresholds come from configuration, because "bad" is deployment-specific.
    A route with too little traffic to judge is `healthy` rather than being
    ranked on a rate computed from nine requests.
    """
    if stats["requests"] < 20:
        return "healthy"
    if (
        stats["server_error_rate"] >= config.error_rate_critical
        or stats["p95"] >= config.latency_p95_critical_ms
    ):
        return "critical"
    if (
        stats["server_error_rate"] >= config.error_rate_critical / 2
        or stats["error_rate"] >= config.error_rate_critical * 2
        or stats["p95"] >= config.latency_p95_critical_ms / 2
    ):
        return "warning"
    return "healthy"


async def problematic_routes(
    gateway: Gateway, window: Window, limit: int = 10
) -> list[dict[str, Any]]:
    """Routes ranked by how much trouble they are in.

    The score deliberately blends several signals rather than sorting on error
    rate alone. A route at 100% errors and eleven requests is not the estate's
    biggest problem, and one at 3% errors and four million requests usually is —
    so error rate is weighted by traffic volume, and latency and timeouts
    contribute on their own terms. The components are returned alongside the
    score so the ranking can be explained on screen rather than asserted.
    """
    table = await route_table(gateway, window)
    total_requests = sum(row["requests"] for row in table) or 1

    scored: list[dict[str, Any]] = []
    for row in table:
        if row["requests"] < 20:
            continue
        share = row["requests"] / total_requests
        # Each component is normalised to roughly 0–1 before weighting, so the
        # weights below are the actual editorial judgement and not an artefact
        # of one component being measured in milliseconds.
        server_errors = min(1.0, row["server_error_rate"] / max(config.error_rate_critical, 1e-9))
        client_errors = min(1.0, row["error_rate"] / max(config.error_rate_critical * 4, 1e-9))
        latency = min(1.0, row["p95"] / max(config.latency_p95_critical_ms, 1))
        timeouts = min(1.0, row["timeouts"] / max(row["requests"], 1) * 100)

        score = (
            server_errors * 0.45
            + latency * 0.25
            + client_errors * 0.15
            + timeouts * 0.15
        ) * (0.35 + 0.65 * share ** 0.4)

        scored.append(
            {
                **row,
                "score": round(score, 5),
                "components": {
                    "server_errors": round(server_errors, 3),
                    "latency": round(latency, 3),
                    "client_errors": round(client_errors, 3),
                    "timeouts": round(timeouts, 3),
                    "traffic_share": round(share, 4),
                },
                "status": "CRITICAL" if row["health"] == "critical"
                else "WARNING" if row["health"] == "warning" else "OK",
            }
        )

    scored.sort(key=lambda r: -r["score"])
    return [row for row in scored if row["score"] > 0][:limit]


async def slow_routes(gateway: Gateway, window: Window, limit: int = 15) -> list[dict[str, Any]]:
    """Routes ranked by P95, which is the number an operator acts on.

    Not by mean: a route with a fast median and a slow tail is the one users
    complain about, and a mean hides exactly that.
    """
    table = await route_table(gateway, window)
    ranked = [row for row in table if row["requests"] >= 20]
    ranked.sort(key=lambda r: (-r["p95"], -r["p99"]))
    return ranked[:limit]


async def route_detail(
    gateway: Gateway, route: GatewayRoute, window: Window
) -> dict[str, Any]:
    """Everything the route investigation screen needs."""
    rows = await _rollups(gateway, window, route=route)
    stats = _aggregate(rows, window.seconds)
    return {
        "stats": stats,
        "health": _health_of(stats),
        "series": await series(gateway, window, route=route),
        "status_distribution": _status_distribution(rows),
        "clients": await client_breakdown(gateway, window, route=route, limit=10),
        "upstream": (
            {"id": route.upstream_id, "name": route.upstream.name}
            if route.upstream_id and route.upstream
            else None
        ),
    }


# ---------------------------------------------------------------------------
# Breakdowns
# ---------------------------------------------------------------------------


def _status_distribution(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    classes = {
        "2xx": sum(r["status_2xx"] for r in rows),
        "3xx": sum(r["status_3xx"] for r in rows),
        "4xx": sum(r["status_4xx"] for r in rows),
        "5xx": sum(r["status_5xx"] for r in rows),
    }
    total = sum(classes.values()) or 1
    return [
        {"label": label, "count": count, "share": round(count / total, 4)}
        for label, count in classes.items()
    ]


async def status_distribution(gateway: Gateway, window: Window) -> list[dict[str, Any]]:
    return _status_distribution(await _rollups(gateway, window))


async def error_breakdown(gateway: Gateway, window: Window) -> dict[str, Any]:
    """Errors by status code, and by the dimensions an investigation needs.

    The per-code totals come from rollups. The dimensional breakdowns — by
    client, by user agent — come from request rows, and are therefore capped to
    the retention window; the UI says so when the requested range is longer
    than what request-level data covers.
    """
    rows = await _rollups(gateway, window)
    codes = {
        "401": sum(r["status_401"] for r in rows),
        "403": sum(r["status_403"] for r in rows),
        "404": sum(r["status_404"] for r in rows),
        "429": sum(r["status_429"] for r in rows),
        "502": sum(r["status_502"] for r in rows),
        "503": sum(r["status_503"] for r in rows),
        "504": sum(r["status_504"] for r in rows),
    }
    other_4xx = max(0, sum(r["status_4xx"] for r in rows) - sum(
        codes[c] for c in ("401", "403", "404", "429")))
    other_5xx = max(0, sum(r["status_5xx"] for r in rows) - sum(
        codes[c] for c in ("502", "503", "504")))

    detail_cutoff = datetime.now(UTC) - timedelta(hours=config.request_retention_hours)
    detailed_since = max(window.since, detail_cutoff)

    by_route = [
        {**row, "errors": row["errors_4xx"] + row["errors_5xx"]}
        for row in await route_table(gateway, window)
    ]
    by_route = sorted(
        [r for r in by_route if r["errors"] > 0], key=lambda r: -r["errors"]
    )[:15]

    return {
        "codes": [
            {"code": code, "count": count} for code, count in sorted(codes.items()) if count
        ]
        + ([{"code": "other 4xx", "count": other_4xx}] if other_4xx else [])
        + ([{"code": "other 5xx", "count": other_5xx}] if other_5xx else []),
        "by_route": by_route,
        "by_client": await _error_clients(gateway, detailed_since, window.until),
        "by_method": await _error_methods(gateway, detailed_since, window.until),
        "detail_since": detailed_since.isoformat(),
        "detail_truncated": detailed_since > window.since,
    }


async def _error_clients(gateway: Gateway, since: datetime, until: datetime) -> list[dict]:
    rows = (
        await RequestLog.filter(
            gateway_id=gateway.pk, occurred_at__gte=since, occurred_at__lte=until, status__gte=400
        )
        .group_by("client_ip")
        .annotate(errors=Count("id"))
        .values("client_ip", "errors")
    )
    return sorted(rows, key=lambda r: -r["errors"])[:15]


async def _error_methods(gateway: Gateway, since: datetime, until: datetime) -> list[dict]:
    rows = (
        await RequestLog.filter(
            gateway_id=gateway.pk, occurred_at__gte=since, occurred_at__lte=until, status__gte=400
        )
        .group_by("method")
        .annotate(errors=Count("id"))
        .values("method", "errors")
    )
    return sorted(rows, key=lambda r: -r["errors"])


async def traffic_breakdown(gateway: Gateway, window: Window) -> dict[str, Any]:
    """Traffic sliced by every dimension the traffic screen offers."""
    detail_cutoff = datetime.now(UTC) - timedelta(hours=config.request_retention_hours)
    since = max(window.since, detail_cutoff)
    base = RequestLog.filter(
        gateway_id=gateway.pk, occurred_at__gte=since, occurred_at__lte=window.until
    )

    async def grouped(field: str, label: str) -> list[dict[str, Any]]:
        rows = (
            await base.group_by(field)
            .annotate(requests=Count("id"), bytes_out=Sum("bytes_out"))
            .values(field, "requests", "bytes_out")
        )
        return sorted(
            [
                {
                    "label": str(row[field] or "—"),
                    "requests": row["requests"],
                    "bytes_out": int(row["bytes_out"] or 0),
                }
                for row in rows
            ],
            key=lambda r: -r["requests"],
        )[:15]

    return {
        "by_domain": await grouped("host", "Domain"),
        "by_method": await grouped("method", "Method"),
        "by_client": await grouped("client_ip", "Client"),
        "by_route": (await route_table(gateway, window))[:15],
        "detail_since": since.isoformat(),
        "detail_truncated": since > window.since,
    }


async def client_breakdown(
    gateway: Gateway,
    window: Window,
    *,
    route: GatewayRoute | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Who the traffic came from, from request rows.

    Bounded by the request-retention window by nature. The client *list* screen
    reads the denormalised counters on `Client` instead, which cover all time;
    this is the windowed view an investigation needs.
    """
    detail_cutoff = datetime.now(UTC) - timedelta(hours=config.request_retention_hours)
    query = RequestLog.filter(
        gateway_id=gateway.pk,
        occurred_at__gte=max(window.since, detail_cutoff),
        occurred_at__lte=window.until,
    )
    if route is not None:
        query = query.filter(route_id=route.pk)

    rows = (
        await query.group_by("client_ip")
        .annotate(
            requests=Count("id"), bytes_out=Sum("bytes_out"), last_seen=Max("occurred_at")
        )
        .values("client_ip", "requests", "bytes_out", "last_seen")
    )
    rows.sort(key=lambda r: -r["requests"])
    rows = rows[:limit]

    known = {
        c.identifier: c
        for c in await Client.filter(identifier__in=[r["client_ip"] for r in rows])
    }
    return [
        {
            "ip": row["client_ip"],
            "requests": row["requests"],
            "bytes_out": int(row["bytes_out"] or 0),
            "last_seen": row["last_seen"].isoformat() if row["last_seen"] else None,
            "label": getattr(known.get(row["client_ip"]), "label", None),
            "status": getattr(known.get(row["client_ip"]), "status", "active"),
            "client_id": getattr(known.get(row["client_ip"]), "pk", None),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


#: The columns every aggregate reads. Named explicitly so the fetch stays a
#: projection: `RouteRollup.filter(...)` builds 6,000 model instances for a
#: 24-hour window and `.values()` builds 6,000 dictionaries, which measured
#: three times faster on the same rows.
_ROLLUP_COLUMNS = (
    "route_id", "minute", "requests",
    "status_1xx", "status_2xx", "status_3xx", "status_4xx", "status_5xx",
    "status_401", "status_403", "status_404", "status_429",
    "status_502", "status_503", "status_504",
    "blocked", "rate_limited", "timeouts", "bytes_in", "bytes_out",
    "latency_sum_ms", "latency_max_ms", "latency_buckets",
    "distinct_clients", "simulated",
)

#: Per-request memo for rollup fetches.
#:
#: One overview page resolves six deferred props, and four of them want the
#: same window: `overview`, `series`, `route_table`, and `route_table` again
#: from inside `problematic_routes` and `slow_routes`. Without this the page
#: ran the identical 6,000-row query six times and took five seconds.
#:
#: A `ContextVar` rather than a module-level dict, and reset per request by
#: `app/bootstrap.py`: a process-wide cache would serve one operator's numbers
#: to the next, and a time-based one would keep showing a route as healthy
#: after it started failing. Scoped to the request, the cache cannot outlive
#: the answer it belongs to.
_rollup_cache: ContextVar[dict[tuple, list[dict[str, Any]]] | None] = ContextVar(
    "janus_rollup_cache", default=None
)


def begin_request() -> None:
    """Start a fresh memo. Called once per request, before any handler runs."""
    _rollup_cache.set({})


async def _rollups(
    gateway: Gateway, window: Window, route: GatewayRoute | None = None
) -> list[dict[str, Any]]:
    key = (gateway.pk, window.since, window.until, route.pk if route else None)
    cache = _rollup_cache.get()
    if cache is not None and key in cache:
        return cache[key]

    query = RouteRollup.filter(
        gateway_id=gateway.pk, minute__gte=window.since, minute__lte=window.until
    )
    if route is not None:
        query = query.filter(route_id=route.pk)
    rows = await query.values(*_ROLLUP_COLUMNS)

    if cache is not None:
        cache[key] = rows
    return rows
