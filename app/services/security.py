"""Blocking, allowing and the client register.

Every function here changes *desired state* and then leaves it to
`app/services/configuration.py` to reach the gateway. Blocking an address is
not an operation Janus performs on live traffic — it is a rule written down,
deployed into Caddy, and enforced there. That is why :func:`block` returns
without waiting for a deployment and why the UI shows a rule as pending until
one has run.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.services import audit
from database.models import Client, Gateway, IpRule, normalise_cidr

__all__ = [
    "allow",
    "block",
    "effective_status",
    "expire_due_rules",
    "next_expiry",
    "register_client",
    "security_overview",
    "unblock",
]

#: Fields recorded in the audit trail when a rule changes.
RULE_FIELDS = ("action", "cidr", "reason", "expires_at", "domain_id", "route_id")


async def register_client(kind: str, identifier: str, **defaults: Any) -> Client:
    """Find or create the client row for an identifier."""
    client, created = await Client.get_or_create(
        kind=kind,
        identifier=identifier,
        defaults={"first_seen_at": datetime.now(UTC), **defaults},
    )
    if not created and defaults:
        for key, value in defaults.items():
            if value is not None:
                setattr(client, key, value)
        await client.save()
    return client


async def block(
    gateway: Gateway,
    cidr: str,
    *,
    reason: str = "",
    duration: timedelta | None = None,
    domain_id: int | None = None,
    route_id: int | None = None,
    actor: Any = None,
    origin: str = "web",
    ip: str | None = None,
) -> IpRule:
    """Add a block rule.

    Args:
        duration: `None` blocks permanently. A duration sets `expires_at`, and
            expiry is enforced by regenerating the configuration once it passes
            — Caddy has no concept of a rule with an end time, so a temporary
            block that nothing redeploys would be a permanent one.

    Raises:
        ValueError: The address or range is not parseable.
    """
    canonical = normalise_cidr(cidr)
    expires_at = datetime.now(UTC) + duration if duration else None

    existing = await IpRule.get_or_none(
        gateway_id=gateway.pk, cidr=canonical, action="block",
        domain_id=domain_id, route_id=route_id,
    )
    before = audit.snapshot(existing, RULE_FIELDS) if existing else None

    if existing is not None:
        existing.reason = reason or existing.reason
        existing.expires_at = expires_at
        await existing.save()
        rule = existing
    else:
        rule = await IpRule.create(
            gateway_id=gateway.pk,
            action="block",
            cidr=canonical,
            reason=reason or None,
            expires_at=expires_at,
            domain_id=domain_id,
            route_id=route_id,
            created_by_id=getattr(actor, "pk", None),
        )

    await _sync_client_status(canonical)
    await audit.record(
        action="client.blocked",
        resource_type="ip_rule",
        resource_id=rule.pk,
        resource_label=canonical,
        actor=actor,
        before=before,
        after=audit.snapshot(rule, RULE_FIELDS),
        origin=origin,
        ip=ip,
    )
    return rule


async def allow(
    gateway: Gateway,
    cidr: str,
    *,
    reason: str = "",
    domain_id: int | None = None,
    route_id: int | None = None,
    actor: Any = None,
    origin: str = "web",
    ip: str | None = None,
) -> IpRule:
    """Add an allow rule.

    An allow rule does two things, and the second surprises people: it exempts
    the range from block rules, *and* — if any allow rule exists at that scope —
    it turns that scope into deny-by-default. That is what an allowlist means,
    and `app/caddy/config_builder.py` emits exactly that, but it is worth
    knowing before adding the first one to a public gateway.
    """
    canonical = normalise_cidr(cidr)
    existing = await IpRule.get_or_none(
        gateway_id=gateway.pk, cidr=canonical, action="allow",
        domain_id=domain_id, route_id=route_id,
    )
    if existing is not None:
        return existing

    rule = await IpRule.create(
        gateway_id=gateway.pk,
        action="allow",
        cidr=canonical,
        reason=reason or None,
        domain_id=domain_id,
        route_id=route_id,
        created_by_id=getattr(actor, "pk", None),
    )
    await _sync_client_status(canonical)
    await audit.record(
        action="client.allowed",
        resource_type="ip_rule",
        resource_id=rule.pk,
        resource_label=canonical,
        actor=actor,
        after=audit.snapshot(rule, RULE_FIELDS),
        origin=origin,
        ip=ip,
    )
    return rule


async def unblock(
    gateway: Gateway,
    cidr: str,
    *,
    actor: Any = None,
    origin: str = "web",
    ip: str | None = None,
) -> int:
    """Remove every block rule for a range. Returns how many were removed."""
    canonical = normalise_cidr(cidr)
    rules = await IpRule.filter(gateway_id=gateway.pk, cidr=canonical, action="block")
    for rule in rules:
        await audit.record(
            action="client.unblocked",
            resource_type="ip_rule",
            resource_id=rule.pk,
            resource_label=canonical,
            actor=actor,
            before=audit.snapshot(rule, RULE_FIELDS),
            origin=origin,
            ip=ip,
        )
        await rule.delete()
    await _sync_client_status(canonical)
    return len(rules)


async def effective_status(gateway: Gateway, address: str) -> str:
    """What the rules currently say about one address.

    Allow wins over block, and the narrower rule wins over the broader one —
    which is the ordering `config_builder` emits, so this answers what the
    gateway would actually do rather than a second opinion about it.
    """
    import ipaddress

    try:
        target = ipaddress.ip_address(address)
    except ValueError:
        return "unknown"

    now = datetime.now(UTC)
    best_action, best_prefix = "active", -1
    for rule in await IpRule.filter(gateway_id=gateway.pk):
        if rule.expires_at is not None and rule.expires_at <= now:
            continue
        try:
            network = ipaddress.ip_network(rule.cidr)
        except ValueError:
            continue
        if target not in network:
            continue
        # A longer prefix is a more specific rule. At equal specificity allow
        # wins, so an explicit allow inside a blocked range gets through.
        if network.prefixlen > best_prefix or (
            network.prefixlen == best_prefix and rule.action == "allow"
        ):
            best_prefix = network.prefixlen
            best_action = "allowed" if rule.action == "allow" else "blocked"
    return best_action


async def _sync_client_status(cidr: str) -> None:
    """Update the cached `status` on matching client rows.

    A cache with one writer. The rules are the authority; this exists so the
    client list can be rendered without evaluating every rule per row.
    """
    import ipaddress

    try:
        network = ipaddress.ip_network(cidr)
    except ValueError:
        return

    for client in await Client.filter(kind="ip"):
        try:
            address = ipaddress.ip_address(client.identifier)
        except ValueError:
            continue
        if address in network:
            gateway = await Gateway.first()
            if gateway is not None:
                client.status = await effective_status(gateway, client.identifier)
                await client.save()


async def expire_due_rules(gateway: Gateway) -> int:
    """Delete temporary rules whose time has passed. Returns how many.

    Called by the scheduled job before a reconciling deployment, so an expired
    block leaves the gateway at the next sync rather than at some unpredictable
    later moment.
    """
    now = datetime.now(UTC)
    due = await IpRule.filter(gateway_id=gateway.pk, expires_at__lte=now)
    for rule in due:
        await audit.record(
            action="client.block_expired",
            resource_type="ip_rule",
            resource_id=rule.pk,
            resource_label=rule.cidr,
            actor=None,
            actor_label="system",
            before=audit.snapshot(rule, RULE_FIELDS),
            origin="system",
        )
        await rule.delete()
    return len(due)


async def next_expiry(gateway: Gateway) -> datetime | None:
    """When the soonest temporary rule expires, if any."""
    rule = (
        await IpRule.filter(gateway_id=gateway.pk, expires_at__not_isnull=True)
        .order_by("expires_at")
        .first()
    )
    return rule.expires_at if rule else None


async def security_overview(gateway: Gateway, window: Any) -> dict[str, Any]:
    """The numbers on the security dashboard."""
    from app.services import analytics
    from database.models import Alert, Anomaly, LoginEvent, SecurityPolicy

    stats = await analytics.overview(gateway, window)
    now = datetime.now(UTC)

    return {
        "blocked": stats["blocked"],
        "rate_limited": stats["rate_limited"],
        "unauthorized": stats.get("errors_4xx", 0),
        "failed_logins": await LoginEvent.filter(
            successful=False, created_at__gte=window.since
        ).count(),
        "active_blocks": await IpRule.filter(
            gateway_id=gateway.pk, action="block"
        ).count(),
        "temporary_blocks": await IpRule.filter(
            gateway_id=gateway.pk, action="block", expires_at__gt=now
        ).count(),
        "allowlisted": await IpRule.filter(gateway_id=gateway.pk, action="allow").count(),
        "policies": await SecurityPolicy.filter(gateway_id=gateway.pk, enabled=True).count(),
        "open_alerts": await Alert.filter(gateway_id=gateway.pk, status="open").count(),
        "unreviewed_anomalies": await Anomaly.filter(
            gateway_id=gateway.pk, verdict="unreviewed"
        ).count(),
    }
