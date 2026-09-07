"""The audit trail.

One function, :func:`record`, and it is the only writer of `audit_events`.
Everything that changes state calls it, and the reason it is a service rather
than a model method is that the interesting arguments — who, from where,
through which front door — live on the request or the CLI session, not on the
row being changed.

What makes the trail useful is `before` and `after`. "An operator changed a
rate limit" answers nothing during an incident; "changed it from 100/min to
10000/min at 03:12 from 10.2.4.9" answers most of it. So :func:`snapshot`
serialises the fields that matter and the callers pass the value from *before*
they mutated it.
"""

from __future__ import annotations

from typing import Any

from database.models import AuditEvent

__all__ = ["record", "snapshot"]

#: Never written to the audit log, whatever a caller passes. A secret that
#: reaches an audit row is a secret in a table the whole team can read, and the
#: audit log outlives the credential's rotation.
REDACTED_FIELDS = frozenset(
    {
        "password",
        "password_hash",
        "secret",
        "secret_key",
        "key_hash",
        "token",
        "api_key",
        "authorization",
        "private_key",
    }
)


def snapshot(instance: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    """The named fields of a model row, as plain JSON-safe values.

    Explicit field lists rather than `to_dict()`: a column added to a model
    should not silently start appearing in the audit log, and some of the
    columns that might be added are exactly the ones that must never.
    """
    if instance is None:
        return {}
    out: dict[str, Any] = {}
    for field in fields:
        if field in REDACTED_FIELDS:
            continue
        value = getattr(instance, field, None)
        if value is None or isinstance(value, str | int | float | bool | list | dict):
            out[field] = value
        else:
            out[field] = str(value)
    return out


async def record(
    *,
    action: str,
    resource_type: str,
    resource_id: Any = None,
    resource_label: str | None = None,
    actor: Any = None,
    actor_label: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    origin: str = "web",
) -> AuditEvent:
    """Write one audit row.

    Args:
        action: Namespaced — `route.created`, `client.blocked`.
        resource_type: The noun: `route`, `upstream`, `client`.
        actor: The user, or `None` for something Janus did itself.
        actor_label: Overrides the derived label. Passed by the CLI so a
            command run as a service account reads as such.
        origin: `web`, `cli`, `api` or `system`. Recorded because the same
            person acting through two front doors is two different facts.
    """
    label = actor_label
    if label is None:
        label = getattr(actor, "email", None) or "system"

    return await AuditEvent.create(
        actor_id=getattr(actor, "pk", None),
        actor_label=label,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        resource_label=resource_label,
        before=_clean(before),
        after=_clean(after),
        ip=ip,
        user_agent=(user_agent or "")[:400] or None,
        origin=origin,
    )


def _clean(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop redacted keys, whatever the caller assembled."""
    if not payload:
        return None
    return {k: v for k, v in payload.items() if k.lower() not in REDACTED_FIELDS}
