"""Gateway and upstream health, read from Caddy rather than guessed.

Janus does not probe backends. Caddy already does — it is in the request path
and Janus deliberately is not — so this module reads what Caddy's health
checkers concluded and caches it for the dashboard. A control plane that ran
its own probes would produce a second opinion that disagrees with the thing
actually routing traffic, and the disagreement would surface during an incident.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.caddy.manager import manager_for
from database.models import Gateway, Upstream, UpstreamTarget

__all__ = ["gateway_health", "refresh_upstream_health"]


async def refresh_upstream_health(gateway: Gateway) -> dict[str, Any]:
    """Pull upstream health from Caddy and cache it onto the target rows.

    Caddy's `/reverse_proxy/upstreams` reports per-dial-address numbers:
    requests in flight and how many failures it is currently counting. A target
    with failures at or above its pool's `max_fails` is one Caddy has taken out
    of rotation, which is the definition Janus reports rather than one of its
    own.
    """
    manager = manager_for(gateway)
    live = await manager.upstream_health()
    if not live:
        return {"available": False, "checked": 0, "unhealthy": 0}

    by_dial = {entry.get("address"): entry for entry in live if entry.get("address")}
    now = datetime.now(UTC)
    checked = unhealthy = 0

    for upstream in await Upstream.filter(gateway_id=gateway.pk).prefetch_related("targets"):
        for target in upstream.targets:
            entry = by_dial.get(target.dial)
            if entry is None:
                continue
            checked += 1
            fails = int(entry.get("fails") or 0)
            healthy = fails < max(1, upstream.max_fails)
            if not healthy:
                unhealthy += 1
            target.healthy = healthy
            target.last_checked_at = now
            target.last_error = None if healthy else f"{fails} recent failures"
            await target.save()

    return {"available": True, "checked": checked, "unhealthy": unhealthy}


async def gateway_health(gateway: Gateway) -> dict[str, Any]:
    """Everything the health screen shows for one gateway."""
    from app.services.modules import build_for

    manager = manager_for(gateway)
    status = await manager.status()
    # The gateway's own build, which respects a declaration for a remote Caddy.
    build = await build_for(gateway)

    targets = await UpstreamTarget.filter(upstream__gateway_id=gateway.pk)
    unhealthy = [t for t in targets if not t.healthy]

    if status.reachable:
        gateway.last_seen_at = datetime.now(UTC)
        gateway.caddy_version = status.version
        gateway.simulated = status.simulated
        await gateway.save()

    return {
        "reachable": status.reachable,
        "version": status.version,
        "error": status.error,
        "simulated": status.simulated,
        "sync_state": gateway.sync_state,
        "sync_error": gateway.sync_error,
        "last_synced_at": gateway.last_synced_at.isoformat() if gateway.last_synced_at else None,
        "targets_total": len(targets),
        "targets_unhealthy": len(unhealthy),
        "unhealthy": [
            {"dial": t.dial, "upstream_id": t.upstream_id, "error": t.last_error}
            for t in unhealthy
        ],
        "foreign_servers": await manager.read_foreign_servers() if status.reachable else [],
        "capabilities": sorted(build.modules),
        "capability_source": build.source,
        "rate_limiting_available": "http.handlers.rate_limit" in build.modules,
    }
