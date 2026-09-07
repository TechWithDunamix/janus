"""Issuing, rotating and revoking gateway API keys.

The rule this module exists to enforce: **the secret is returned exactly once,
at creation, and is never recoverable afterwards.** Not from the database, not
from the API, not from the CLI, not from a log. What is stored is a SHA-256
digest for verification and a short prefix for identification, and every screen
that names a key names the prefix.

Key generation itself is :func:`sillo.auth.apikey.generate_api_key` — the
framework's, not a hand-rolled one. What this module adds is the part that is
specific to a gateway consumer rather than a user's personal token: an owning
client, a rate limit, edge scopes, and rotation that preserves the row's
identity and its usage history across a new secret.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sillo.auth.apikey.models import generate_api_key

from app.services import audit
from database.models import ApiKey, Client

__all__ = ["KEY_FIELDS", "create", "revoke", "rotate", "set_enabled", "verify"]

#: The prefix every Janus-issued key carries. Recognisable in a log or a paste,
#: and specific enough that a leaked key can be searched for.
KEY_PREFIX = "jan"

#: Audited fields. `key_hash` is deliberately absent — see `audit.REDACTED_FIELDS`.
KEY_FIELDS = ("name", "prefix", "scopes", "enabled", "expires_at", "client_id", "rate_limit_id")


async def create(
    *,
    name: str,
    client: Client | None = None,
    scopes: str = "",
    expires_in: timedelta | None = None,
    rate_limit_id: int | None = None,
    actor: Any = None,
    origin: str = "web",
    ip: str | None = None,
) -> tuple[ApiKey, str]:
    """Issue a key.

    Returns:
        The row and the full secret. **The secret is not stored and cannot be
        retrieved again** — a caller that does not show it to the operator now
        has lost it, which is the intended property.
    """
    full_key, _, digest = generate_api_key(prefix=KEY_PREFIX)
    # Enough to recognise, far too little to authenticate with: the secret is
    # 43 url-safe characters of entropy after the prefix.
    display_prefix = full_key[: ApiKey.PREFIX_LENGTH]

    key = await ApiKey.create(
        name=name,
        key_hash=digest,
        prefix=display_prefix,
        client_id=getattr(client, "pk", None),
        scopes=scopes or "",
        rate_limit_id=rate_limit_id,
        expires_at=(datetime.now(UTC) + expires_in) if expires_in else None,
        created_by_id=getattr(actor, "pk", None),
    )
    await audit.record(
        action="apikey.created",
        resource_type="api_key",
        resource_id=key.pk,
        resource_label=f"{name} ({display_prefix}…)",
        actor=actor,
        after=audit.snapshot(key, KEY_FIELDS),
        origin=origin,
        ip=ip,
    )
    return key, full_key


async def rotate(
    key: ApiKey,
    *,
    actor: Any = None,
    origin: str = "web",
    ip: str | None = None,
) -> tuple[ApiKey, str]:
    """Replace a key's secret, keeping its identity and history.

    A new row is created carrying `rotated_from_id`, and the old one is
    revoked. Two rows rather than an in-place update, because analytics rows
    reference the key that served them: overwriting the digest would silently
    re-attribute every historical request to the new secret.
    """
    full_key, _, digest = generate_api_key(prefix=KEY_PREFIX)
    display_prefix = full_key[: ApiKey.PREFIX_LENGTH]

    replacement = await ApiKey.create(
        name=key.name,
        key_hash=digest,
        prefix=display_prefix,
        client_id=key.client_id,
        scopes=key.scopes,
        rate_limit_id=key.rate_limit_id,
        expires_at=key.expires_at,
        created_by_id=getattr(actor, "pk", None),
        rotated_from_id=key.pk,
        rotated_at=datetime.now(UTC),
    )
    key.enabled = False
    key.revoked_at = datetime.now(UTC)
    key.revoked_reason = f"Rotated to key {replacement.pk}."
    await key.save()

    await audit.record(
        action="apikey.rotated",
        resource_type="api_key",
        resource_id=replacement.pk,
        resource_label=f"{key.name} ({display_prefix}…)",
        actor=actor,
        before={"prefix": key.prefix},
        after={"prefix": display_prefix, "rotated_from": key.pk},
        origin=origin,
        ip=ip,
    )
    return replacement, full_key


async def revoke(
    key: ApiKey,
    *,
    reason: str = "",
    actor: Any = None,
    origin: str = "web",
    ip: str | None = None,
) -> ApiKey:
    """Revoke a key permanently. Revocation is not reversible by design."""
    before = audit.snapshot(key, KEY_FIELDS)
    key.enabled = False
    key.revoked_at = datetime.now(UTC)
    key.revoked_reason = reason or "Revoked by an administrator."
    await key.save()
    await audit.record(
        action="apikey.revoked",
        resource_type="api_key",
        resource_id=key.pk,
        resource_label=f"{key.name} ({key.prefix}…)",
        actor=actor,
        before=before,
        after=audit.snapshot(key, KEY_FIELDS),
        origin=origin,
        ip=ip,
    )
    return key


async def set_enabled(
    key: ApiKey,
    enabled: bool,
    *,
    actor: Any = None,
    origin: str = "web",
    ip: str | None = None,
) -> ApiKey:
    """Disable or re-enable a key. A revoked key stays revoked."""
    if key.revoked_at is not None:
        return key
    before = audit.snapshot(key, KEY_FIELDS)
    key.enabled = enabled
    await key.save()
    await audit.record(
        action="apikey.enabled" if enabled else "apikey.disabled",
        resource_type="api_key",
        resource_id=key.pk,
        resource_label=f"{key.name} ({key.prefix}…)",
        actor=actor,
        before=before,
        after=audit.snapshot(key, KEY_FIELDS),
        origin=origin,
        ip=ip,
    )
    return key


async def verify(raw_key: str) -> ApiKey | None:
    """The key a secret belongs to, or `None`.

    Looks up by digest, so the secret is hashed once and compared by the
    database's index rather than by scanning rows and comparing in Python.
    Returns `None` for a key that exists but is revoked, disabled or expired —
    the caller should not have to know the difference, and telling it apart
    would be an oracle.
    """
    if not raw_key:
        return None
    key = await ApiKey.get_or_none(key_hash=ApiKey.digest(raw_key))
    if key is None or not key.is_live:
        return None
    key.last_used_at = datetime.now(UTC)
    key.request_count += 1
    await key.save()
    return key
