"""Background work: ingestion, rollups, health, alerts and reconciliation.

Everything Janus does on a timer rather than on a request. Each job is small
and idempotent, because the scheduler makes no promise that one has finished
before the next tick — and a collector that double-counts on an overlap is a
collector that reports traffic that never happened.

None of these is in the request path of gateway traffic. They run beside it,
reading what Caddy produced and writing what the dashboard reads.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sillo.work.queue.job import Job

from app.config import config

__all__ = ["ALL_JOBS", "SCHEDULE", "run_all_once"]


class CollectAccessLogs(Job):
    """Read new access-log lines into request rows and rollups."""

    queue = "collector"

    async def handle(self) -> Any:
        from app.collector import ingest_file, load_state, save_state
        from database.models import Gateway

        total = 0
        for gateway in await Gateway.filter(enabled=True):
            state = load_state(gateway, gateway.access_log_path)
            state = await ingest_file(gateway, state)
            save_state(gateway, state)
            total += state.ingested
        return {"ingested": total}


class RefreshHealth(Job):
    """Pull upstream health from Caddy onto the target rows."""

    queue = "health"

    async def handle(self) -> Any:
        from app.services.health import refresh_upstream_health
        from database.models import Gateway

        results = []
        for gateway in await Gateway.filter(enabled=True):
            results.append(await refresh_upstream_health(gateway))
        return {"gateways": len(results)}


class DetectDrift(Job):
    """Compare each gateway against its active configuration version."""

    queue = "gateway"

    async def handle(self) -> Any:
        from app.services.configuration import detect_drift
        from database.models import Gateway

        drifted = 0
        for gateway in await Gateway.filter(enabled=True):
            found, _ = await detect_drift(gateway)
            drifted += int(found)
        return {"drifted": drifted}


class EvaluateAlerts(Job):
    """Open and resolve threshold alerts, then look for anomalies."""

    queue = "analytics"

    async def handle(self) -> Any:
        from app.services.alerts import detect_anomalies, evaluate
        from database.models import Gateway

        opened = resolved = anomalies = 0
        for gateway in await Gateway.filter(enabled=True):
            counts = await evaluate(gateway)
            opened += counts["opened"]
            resolved += counts["resolved"]
            anomalies += len(await detect_anomalies(gateway))
        return {"opened": opened, "resolved": resolved, "anomalies": anomalies}


class ExpireRules(Job):
    """Remove temporary blocks whose time has passed, and redeploy if any did.

    The redeploy is the point. An expired rule that is still in the running
    configuration is still blocking traffic, so expiry is only real once the
    gateway has been told.
    """

    queue = "gateway"

    async def handle(self) -> Any:
        from app.services.configuration import deploy
        from app.services.security import expire_due_rules
        from database.models import Gateway

        expired = 0
        for gateway in await Gateway.filter(enabled=True):
            count = await expire_due_rules(gateway)
            if count:
                expired += count
                await deploy(gateway, origin="system", note="Expired temporary rules.")
        return {"expired": expired}


class PruneAnalytics(Job):
    """Drop request rows past their retention, and old rollups past theirs.

    Two retentions because they cost differently. Request rows are large and
    kept for hours; rollups are small, are what every chart reads, and are kept
    for months.
    """

    queue = "analytics"

    async def handle(self) -> Any:
        from database.models import RequestLog, RouteRollup

        now = datetime.now(UTC)
        request_cutoff = now - timedelta(hours=config.request_retention_hours)
        rollup_cutoff = now - timedelta(days=config.rollup_retention_days)

        requests = await RequestLog.filter(occurred_at__lt=request_cutoff).delete()
        rollups = await RouteRollup.filter(minute__lt=rollup_cutoff).delete()
        return {"requests": requests, "rollups": rollups}


class UpdateClientCounters(Job):
    """Refresh the denormalised counters on the client rows.

    These exist so the client list can sort by traffic without aggregating the
    request table per row. Recomputed rather than incremented, so a crash
    during ingestion cannot leave them permanently wrong.
    """

    queue = "analytics"

    async def handle(self) -> Any:
        from tortoise.functions import Count, Sum

        from database.models import Client, RequestLog

        since = datetime.now(UTC) - timedelta(hours=config.request_retention_hours)
        rows = (
            await RequestLog.filter(occurred_at__gte=since)
            .group_by("client_ip")
            .annotate(requests=Count("id"), bytes_out=Sum("bytes_out"))
            .values("client_ip", "requests", "bytes_out")
        )
        totals = {r["client_ip"]: r for r in rows}
        updated = 0
        for client in await Client.filter(kind="ip"):
            row = totals.get(client.identifier)
            if row is None:
                continue
            client.request_count = row["requests"]
            client.bytes_out = int(row["bytes_out"] or 0)
            await client.save()
            updated += 1
        return {"clients": updated}


#: Every job class, for binding and for the test that asserts each is reachable.
ALL_JOBS: tuple[type[Job], ...] = (
    CollectAccessLogs,
    RefreshHealth,
    DetectDrift,
    EvaluateAlerts,
    ExpireRules,
    PruneAnalytics,
    UpdateClientCounters,
)

#: What the scheduler runs, and how often.
#:
#: Ingestion is the frequent one because it is what every other number depends
#: on. Drift and pruning are slow on purpose: drift detection is a read against
#: every gateway, and pruning is a large delete that should not compete with
#: ingestion.
SCHEDULE: tuple[tuple[type[Job], int], ...] = (
    (CollectAccessLogs, 15),
    (RefreshHealth, 30),
    (EvaluateAlerts, 60),
    (ExpireRules, 60),
    (DetectDrift, 300),
    (UpdateClientCounters, 300),
    (PruneAnalytics, 3600),
)


async def run_all_once() -> dict[str, Any]:
    """Run every scheduled job once, in order.

    What `janus work` calls. On a laptop this is the whole background system:
    running the jobs on demand is a better fit for development than a scheduler
    process that has to be remembered.
    """
    results: dict[str, Any] = {}
    for job_class, _ in SCHEDULE:
        try:
            results[job_class.__name__] = await job_class().handle()
        except Exception as error:  # noqa: BLE001
            # One failing job must not stop the rest. The failure is reported
            # in the result rather than raised, so `janus work` shows which of
            # seven jobs failed instead of stopping at the first.
            results[job_class.__name__] = {"error": str(error)}
    return results
