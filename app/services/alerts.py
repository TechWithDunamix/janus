"""Threshold alerts, and statistical anomaly detection kept separate from them.

The distinction is the point of this module. An **alert** is a rule someone
wrote with a threshold they chose: 5xx above 5%, P95 above a second. An
**anomaly** is Janus observing that the last few minutes do not look like the
preceding hour. The first is a statement about intent; the second is a
statement about statistics, and it is often nothing at all — traffic doubles at
09:00 every weekday.

Presenting the second as though it were the first is how a dashboard teaches
its users to ignore it. So they are different tables, different screens,
different words, and an anomaly is only ever promoted to an incident by a
person.

Alerts are **stateful**: the evaluator opens one when a condition starts
holding and resolves it when it stops. Emitting a row per evaluation would turn
a five-minute outage into five alerts at a one-minute interval, which is the
same mistake in a different place.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import config
from app.services import analytics
from database.models import Alert, Anomaly, Gateway, GatewayRoute, RouteRollup, UpstreamTarget

__all__ = ["ALERT_RULES", "detect_anomalies", "evaluate", "open_alerts", "resolve"]


#: The conditions Janus evaluates, and what each one means.
#:
#: `window` is how much traffic the condition is judged over — short enough to
#: notice quickly, long enough that a handful of requests cannot trip it. Each
#: rule also carries a floor: a route with nine requests and three errors is a
#: 33% error rate and not news.
ALERT_RULES: tuple[dict[str, Any], ...] = (
    {
        "kind": "error_rate_5xx",
        "title": "Server error rate is high",
        "window": timedelta(minutes=15),
        "min_requests": 50,
        "severity": "critical",
    },
    {
        "kind": "error_rate_4xx",
        "title": "Client error rate is high",
        "window": timedelta(minutes=15),
        "min_requests": 100,
        "severity": "warning",
    },
    {
        "kind": "latency_p95",
        "title": "P95 latency is high",
        "window": timedelta(minutes=15),
        "min_requests": 50,
        "severity": "warning",
    },
)


async def evaluate(gateway: Gateway) -> dict[str, int]:
    """Run every alert rule and reconcile open alerts against what is true now.

    Returns counts of what it opened and resolved. Reconciling rather than
    appending is what keeps the alert list a description of the present rather
    than a log of the past.
    """
    opened = resolved = 0
    window = analytics.range_bounds("15m")
    seen: set[tuple[str, int | None]] = set()

    table = await analytics.route_table(gateway, window)
    for row in table:
        if row["id"] is None or row["requests"] < 50:
            continue
        route_key = row["id"]

        if row["server_error_rate"] >= config.error_rate_critical:
            seen.add(("error_rate_5xx", route_key))
            if await _open(
                gateway, "error_rate_5xx", "critical",
                f"{row['label']} — {row['server_error_rate']:.1%} 5xx",
                observed=row["server_error_rate"] * 100,
                threshold=config.error_rate_critical * 100,
                route_id=route_key,
                detail=f"{row['errors_5xx']:,} server errors in {row['requests']:,} requests.",
            ):
                opened += 1

        if row["p95"] >= config.latency_p95_critical_ms:
            seen.add(("latency_p95", route_key))
            if await _open(
                gateway, "latency_p95", "warning",
                f"{row['label']} — P95 {row['p95']:.0f}ms",
                observed=row["p95"],
                threshold=float(config.latency_p95_critical_ms),
                route_id=route_key,
                detail=f"P99 is {row['p99']:.0f}ms over {row['requests']:,} requests.",
            ):
                opened += 1

    # Unhealthy upstream targets, which come from Caddy's own health checkers.
    for target in await UpstreamTarget.filter(upstream__gateway_id=gateway.pk, healthy=False):
        seen.add(("upstream_unhealthy", target.upstream_id))
        if await _open(
            gateway, "upstream_unhealthy", "critical",
            f"Upstream target {target.dial} is unhealthy",
            upstream_id=target.upstream_id,
            detail=target.last_error or "Caddy has taken this target out of rotation.",
        ):
            opened += 1

    if gateway.sync_state in {"FAILED", "DRIFTED"}:
        seen.add(("sync_failed", None))
        if await _open(
            gateway, "sync_failed",
            "critical" if gateway.sync_state == "FAILED" else "warning",
            f"Gateway configuration is {gateway.sync_state}",
            detail=gateway.sync_error or "",
        ):
            opened += 1

    # Anything open that is no longer true gets resolved.
    for alert in await Alert.filter(gateway_id=gateway.pk, status="open"):
        key = (alert.kind, alert.route_id or alert.upstream_id)
        if key not in seen and (alert.kind, None) not in seen:
            alert.status = "resolved"
            alert.resolved_at = datetime.now(UTC)
            await alert.save()
            resolved += 1

    return {"opened": opened, "resolved": resolved}


async def _open(
    gateway: Gateway,
    kind: str,
    severity: str,
    title: str,
    *,
    observed: float | None = None,
    threshold: float | None = None,
    route_id: int | None = None,
    upstream_id: int | None = None,
    detail: str = "",
) -> bool:
    """Open an alert unless an equivalent one is already open.

    Returns whether a new row was created, so the caller can report how many
    conditions are newly true rather than how many are true.
    """
    existing = await Alert.get_or_none(
        gateway_id=gateway.pk, kind=kind, route_id=route_id,
        upstream_id=upstream_id, status="open",
    )
    if existing is not None:
        # Keep the numbers current on the open alert; an operator looking at it
        # wants what it is now, not what it was when it opened.
        existing.observed = observed
        existing.detail = detail or existing.detail
        await existing.save()
        return False

    await Alert.create(
        gateway_id=gateway.pk,
        kind=kind,
        severity=severity,
        title=title,
        detail=detail or None,
        observed=observed,
        threshold=threshold,
        route_id=route_id,
        upstream_id=upstream_id,
    )
    return True


async def resolve(alert: Alert, *, actor: Any = None) -> Alert:
    alert.status = "resolved"
    alert.resolved_at = datetime.now(UTC)
    if actor is not None:
        alert.acknowledged_at = datetime.now(UTC)
        alert.acknowledged_by_id = actor.pk
    await alert.save()
    return alert


async def open_alerts(gateway: Gateway) -> list[Alert]:
    return (
        await Alert.filter(gateway_id=gateway.pk, status="open")
        .order_by("-severity", "-opened_at")
        .prefetch_related("route", "upstream")
    )


# ---------------------------------------------------------------------------
# Anomalies
# ---------------------------------------------------------------------------


async def detect_anomalies(gateway: Gateway) -> list[Anomaly]:
    """Compare the last few minutes against the preceding hour.

    The method is deliberately simple and stated plainly on screen: a z-score
    against the mean and standard deviation of the preceding baseline, flagged
    above three deviations. It is not a model, it does not learn, and it knows
    nothing about the time of day — which is exactly why its output is called
    an anomaly and filed separately from alerts.

    A minimum baseline standard deviation guards the degenerate case: perfectly
    flat traffic has σ≈0, and any change at all is then infinitely many
    deviations from the mean.
    """
    now = datetime.now(UTC)
    recent_from = now - timedelta(minutes=5)
    baseline_from = now - timedelta(minutes=65)

    rows = await RouteRollup.filter(gateway_id=gateway.pk, minute__gte=baseline_from)
    if len(rows) < 20:
        return []

    found: list[Anomaly] = []
    per_route: dict[int | None, list[RouteRollup]] = {}
    for row in rows:
        per_route.setdefault(row.route_id, []).append(row)

    routes = {r.pk: r for r in await GatewayRoute.filter(gateway_id=gateway.pk)}

    for route_id, group in per_route.items():
        baseline = [r for r in group if r.minute < recent_from]
        recent = [r for r in group if r.minute >= recent_from]
        if len(baseline) < 15 or not recent:
            continue

        label = routes[route_id].name if route_id in routes else "unmatched traffic"

        anomaly = await _check_metric(
            gateway, route_id, label, "traffic_spike",
            [r.requests for r in baseline],
            statistics.mean([r.requests for r in recent]),
            recent_from, now,
            "requests per minute",
        )
        if anomaly:
            found.append(anomaly)

        baseline_errors = [
            (r.status_5xx / r.requests * 100) if r.requests else 0.0 for r in baseline
        ]
        recent_errors = [
            (r.status_5xx / r.requests * 100) if r.requests else 0.0 for r in recent
        ]
        anomaly = await _check_metric(
            gateway, route_id, label, "error_surge",
            baseline_errors, statistics.mean(recent_errors), recent_from, now,
            "% 5xx",
        )
        if anomaly:
            found.append(anomaly)

        baseline_latency = [
            (r.latency_sum_ms / r.requests) if r.requests else 0.0 for r in baseline
        ]
        recent_latency = [
            (r.latency_sum_ms / r.requests) if r.requests else 0.0 for r in recent
        ]
        anomaly = await _check_metric(
            gateway, route_id, label, "latency_spike",
            baseline_latency, statistics.mean(recent_latency), recent_from, now,
            "ms mean latency",
        )
        if anomaly:
            found.append(anomaly)

    return found


async def _check_metric(
    gateway: Gateway,
    route_id: int | None,
    label: str,
    kind: str,
    baseline: list[float],
    observed: float,
    window_start: datetime,
    window_end: datetime,
    unit: str,
) -> Anomaly | None:
    """Flag one metric if it is more than three deviations from its baseline."""
    if len(baseline) < 15:
        return None
    mean = statistics.mean(baseline)
    try:
        deviation = statistics.stdev(baseline)
    except statistics.StatisticsError:
        return None

    # Flat traffic has σ≈0 and would make every change infinitely anomalous.
    floor = max(mean * 0.1, 1.0)
    deviation = max(deviation, floor)

    z = (observed - mean) / deviation
    if z < 3.0:
        return None

    # One row per kind per route per hour, so a sustained spike is one finding
    # rather than a new one every time the detector runs.
    since = datetime.now(UTC) - timedelta(hours=1)
    if await Anomaly.filter(
        gateway_id=gateway.pk, route_id=route_id, kind=kind, detected_at__gte=since
    ).exists():
        return None

    return await Anomaly.create(
        gateway_id=gateway.pk,
        route_id=route_id,
        kind=kind,
        detail=(
            f"{label}: {observed:,.1f} {unit} against a baseline of "
            f"{mean:,.1f} (σ {deviation:,.1f}) over the preceding hour."
        ),
        observed=observed,
        baseline=mean,
        deviation=round(z, 2),
        window_start=window_start,
        window_end=window_end,
    )
