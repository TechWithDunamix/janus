"""Access control at the edge: clients, IP rules, rate limits, keys, policies.

Everything in this file describes policy that Caddy enforces. Janus decides
*what* the rule is and writes it into the generated configuration; the request
that gets blocked is blocked by Caddy, in the data plane, without a round trip
to this database. That distinction is the whole performance argument for the
architecture and it is why none of these models has a "check this request"
method on it.
"""

from __future__ import annotations

import hashlib
import ipaddress
from datetime import UTC, datetime

from sillo.record import Model
from tortoise import fields

__all__ = [
    "ApiKey",
    "Client",
    "IpRule",
    "RateLimit",
    "SecurityPolicy",
    "normalise_cidr",
]


def normalise_cidr(value: str) -> str:
    """Canonicalise an address or range, or raise `ValueError`.

    A single address is stored as a `/32` (or `/128`) network so that one
    column and one comparison covers both cases — an allowlist holding
    `203.0.113.42` and `10.0.0.0/8` should not need two code paths.

    `strict=False` because operators write `192.168.1.42/24` meaning "that
    address's network", and rejecting it teaches them nothing useful.
    """
    text = (value or "").strip()
    if not text:
        raise ValueError("An address or CIDR is required.")
    return str(ipaddress.ip_network(text, strict=False))


class Client(Model):
    """An identified consumer of the gateway.

    A client is whatever the estate uses to mean "one caller": an address, a
    range, an API key, or an authenticated identity. Making that a `kind` plus
    an `identifier` rather than four nullable columns keeps every screen that
    lists or blocks clients written against one shape.

    Rows are created two ways: by an administrator naming a client, and by the
    collector when traffic arrives from an identifier it has not seen. The
    second is why `first_seen_at` exists and why `label` is nullable.
    """

    id = fields.IntField(pk=True)

    #: `ip`, `cidr`, `api_key`, `user`, `label`.
    kind = fields.CharField(max_length=16, default="ip", index=True)
    #: The address, network, key prefix, user identity or opaque client id.
    #: Never a key *secret* — see :class:`ApiKey` for why only the prefix is
    #: ever stored anywhere readable.
    identifier = fields.CharField(max_length=255, index=True)

    label = fields.CharField(max_length=120, null=True)
    notes = fields.TextField(null=True)
    organisation = fields.CharField(max_length=120, null=True)

    #: Denormalised counters maintained by the collector. They exist so the
    #: client list can sort by traffic without aggregating the request table on
    #: every page load; the request table remains the authority for any figure
    #: with a time range attached to it.
    request_count = fields.BigIntField(default=0)
    error_count = fields.BigIntField(default=0)
    blocked_count = fields.BigIntField(default=0)
    rate_limited_count = fields.BigIntField(default=0)
    bytes_out = fields.BigIntField(default=0)

    first_seen_at = fields.DatetimeField(null=True)
    last_seen_at = fields.DatetimeField(null=True, index=True)

    #: Set from the active :class:`IpRule` by the security service, so the
    #: client list can show state without a join per row. The rule is the
    #: authority; this is a cache with one writer.
    status = fields.CharField(max_length=16, default="active", index=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "clients"
        unique_together = (("kind", "identifier"),)

    def __str__(self) -> str:
        return self.label or self.identifier


class IpRule(Model):
    """An allowlist or blocklist entry.

    One model for both because they are the same rule with opposite verbs, and
    because evaluation order matters between them: `app/services/security.py`
    resolves allow before block, so an explicitly allowed address inside a
    blocked range still gets through. Two tables would have made that ordering
    implicit in which query ran first.
    """

    id = fields.IntField(pk=True)
    gateway = fields.ForeignKeyField(
        "models.Gateway", related_name="ip_rules", null=True, on_delete=fields.CASCADE
    )

    #: `allow` or `block`.
    action = fields.CharField(max_length=10, default="block", index=True)
    #: Canonical CIDR, always — see :func:`normalise_cidr`.
    cidr = fields.CharField(max_length=64, index=True)

    #: Scope. All three null means global; setting `domain` or `route` narrows
    #: the rule to that surface, which is what "route-specific allowlist"
    #: means in the generated configuration.
    domain = fields.ForeignKeyField(
        "models.Domain", related_name="ip_rules", null=True, on_delete=fields.CASCADE
    )
    route = fields.ForeignKeyField(
        "models.GatewayRoute", related_name="ip_rules", null=True, on_delete=fields.CASCADE
    )

    reason = fields.CharField(max_length=255, null=True)
    notes = fields.TextField(null=True)

    #: Null means permanent. A temporary block sets this, and
    #: `app/services/security.py::expire_rules` removes it from the generated
    #: configuration once it passes — expiry is enforced by regenerating, not
    #: by Caddy, because Caddy has no concept of a rule with an end time.
    expires_at = fields.DatetimeField(null=True, index=True)

    created_by = fields.ForeignKeyField(
        "models.User", related_name="ip_rules", null=True, on_delete=fields.SET_NULL
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "ip_rules"
        indexes = (("action", "expires_at"),)

    def __str__(self) -> str:
        return f"{self.action} {self.cidr}"

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.now(UTC)

    @property
    def is_permanent(self) -> bool:
        return self.expires_at is None


class RateLimit(Model):
    """A request ceiling, applied to some key over some window."""

    id = fields.IntField(pk=True)
    gateway = fields.ForeignKeyField(
        "models.Gateway", related_name="rate_limits", null=True, on_delete=fields.CASCADE
    )
    name = fields.CharField(max_length=120)

    #: What the counter is kept per: `ip`, `api_key`, `user`, `route`,
    #: `domain`, `client` or `global`.
    key = fields.CharField(max_length=16, default="ip", index=True)

    #: Scope — which traffic the limit applies to. Null on both means every
    #: request the gateway serves.
    domain = fields.ForeignKeyField(
        "models.Domain", related_name="rate_limits", null=True, on_delete=fields.CASCADE
    )
    route = fields.ForeignKeyField(
        "models.GatewayRoute", related_name="rate_limits", null=True, on_delete=fields.CASCADE
    )

    #: The limit itself. `window_seconds` carries every documented rate — 1 for
    #: per-second, 60 per-minute, 3600 per-hour, 86400 per-day — rather than an
    #: enum of unit names, because the generator needs the number anyway.
    limit = fields.IntField(default=100)
    window_seconds = fields.IntField(default=60)
    #: Momentary allowance above the sustained rate. 0 means no burst.
    burst = fields.IntField(default=0)

    #: What a limited caller receives.
    response_status = fields.IntField(default=429)
    response_body = fields.TextField(null=True)
    #: Whether to send `Retry-After`. Off for an endpoint where telling a
    #: scraper exactly when to come back is not desirable.
    send_retry_after = fields.BooleanField(default=True)

    enabled = fields.BooleanField(default=True, index=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "rate_limits"

    def __str__(self) -> str:
        return f"{self.name} ({self.rate_label})"

    @property
    def rate_label(self) -> str:
        """`100/min`, `10/sec` — how the rate is written everywhere in the UI."""
        units = {1: "sec", 60: "min", 3600: "hour", 86400: "day"}
        unit = units.get(self.window_seconds)
        return f"{self.limit:,}/{unit}" if unit else f"{self.limit:,}/{self.window_seconds}s"


class ApiKey(Model):
    """A key issued to a gateway consumer.

    The secret is never stored. What is kept is a SHA-256 digest for
    verification and a short prefix for identification, which is what lets a
    key be named in an analytics table or a rate-limit rule without the table
    becoming a list of credentials.

    The hashing itself is :mod:`sillo.auth.apikey`'s — this model deliberately
    does not implement key generation or comparison, it calls the framework's.
    What it adds is what a *gateway* key needs and a user's personal key does
    not: an owning client, a rate limit, scopes with meaning at the edge, and
    rotation that keeps the row's identity across a new secret.
    """

    #: How many leading characters of a key are kept for identification.
    #:
    #: One definition, because two would be a bug that hides: the issuer slices
    #: the key to this length to store it, and the access-log collector slices
    #: an observed key to the same length to look it up. When those disagreed,
    #: every lookup missed and no request was ever attributed to a key — with
    #: nothing failing loudly to say so.
    PREFIX_LENGTH = 10

    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=120)

    #: The digest of the full key. Unique so a collision is a database error
    #: rather than two keys authenticating as each other.
    key_hash = fields.CharField(max_length=128, unique=True, index=True)
    #: The leading characters, shown in every list so a key is recognisable:
    #: `jan_live_8fQ2…`. Never enough to authenticate with.
    prefix = fields.CharField(max_length=24, index=True)

    client = fields.ForeignKeyField(
        "models.Client", related_name="api_keys", null=True, on_delete=fields.SET_NULL
    )
    #: Comma-separated scopes, matched by the `scope` auth policy on a route.
    scopes = fields.CharField(max_length=500, default="")

    rate_limit = fields.ForeignKeyField(
        "models.RateLimit", related_name="api_keys", null=True, on_delete=fields.SET_NULL
    )

    enabled = fields.BooleanField(default=True, index=True)
    expires_at = fields.DatetimeField(null=True, index=True)
    revoked_at = fields.DatetimeField(null=True)
    revoked_reason = fields.CharField(max_length=255, null=True)

    last_used_at = fields.DatetimeField(null=True)
    request_count = fields.BigIntField(default=0)

    #: Set on rotation to the row the new key replaced, so usage history
    #: survives a rotation instead of appearing as a key that stopped dead.
    rotated_from_id = fields.IntField(null=True)
    rotated_at = fields.DatetimeField(null=True)

    created_by = fields.ForeignKeyField(
        "models.User", related_name="api_keys", null=True, on_delete=fields.SET_NULL
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "gateway_api_keys"

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix}…)"

    @property
    def scope_list(self) -> list[str]:
        return [s.strip() for s in (self.scopes or "").split(",") if s.strip()]

    @property
    def is_live(self) -> bool:
        if not self.enabled or self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > datetime.now(UTC)

    @property
    def status(self) -> str:
        if self.revoked_at is not None:
            return "revoked"
        if not self.enabled:
            return "disabled"
        if self.expires_at is not None and self.expires_at <= datetime.now(UTC):
            return "expired"
        return "active"

    @staticmethod
    def digest(raw_key: str) -> str:
        """The stored form of a key.

        Mirrors `sillo.auth.apikey.hash_api_key`. It is spelled out here rather
        than imported so that this model does not drag the framework's own
        `ApiKey` table into Janus's model registry — importing that module
        registers it, and Janus would then own two `api_keys` tables with
        different shapes.
        """
        return hashlib.sha256(raw_key.encode()).hexdigest()


class SecurityPolicy(Model):
    """A conditional rule: when this matches, do that.

    Deliberately a small, closed vocabulary rather than an expression
    language. Every policy is `subject` `operator` `value` → `action`, which is
    enough for the rules an edge actually needs and little enough that the
    generator can prove what it emits. A DSL here would mean shipping a parser
    into the configuration path, and a parser is the last thing that should
    stand between an operator and their gateway.
    """

    id = fields.IntField(pk=True)
    gateway = fields.ForeignKeyField(
        "models.Gateway", related_name="policies", null=True, on_delete=fields.CASCADE
    )
    name = fields.CharField(max_length=120)
    description = fields.TextField(null=True)

    #: `ip`, `cidr`, `api_key`, `client`, `path`, `method`, `header`,
    #: `country`, `rate`.
    subject = fields.CharField(max_length=20, default="ip")
    #: `equals`, `in_cidr`, `matches`, `exceeds`.
    operator = fields.CharField(max_length=16, default="equals")
    value = fields.CharField(max_length=255)

    #: `allow`, `block`, `rate_limit`, `require_auth`, `log`.
    action = fields.CharField(max_length=20, default="block")
    #: For `rate_limit` actions — which limit to apply.
    rate_limit = fields.ForeignKeyField(
        "models.RateLimit", related_name="policies", null=True, on_delete=fields.SET_NULL
    )

    #: Scope, narrowing from global down to one route.
    domain = fields.ForeignKeyField(
        "models.Domain", related_name="policies", null=True, on_delete=fields.CASCADE
    )
    route = fields.ForeignKeyField(
        "models.GatewayRoute", related_name="policies", null=True, on_delete=fields.CASCADE
    )

    #: Evaluated high to low, so a specific allow can be ordered above a broad
    #: block without either being deleted.
    priority = fields.IntField(default=0, index=True)
    enabled = fields.BooleanField(default=True, index=True)

    match_count = fields.BigIntField(default=0)
    last_matched_at = fields.DatetimeField(null=True)

    created_by = fields.ForeignKeyField(
        "models.User", related_name="policies", null=True, on_delete=fields.SET_NULL
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "security_policies"

    def __str__(self) -> str:
        return self.name

    @property
    def sentence(self) -> str:
        """The rule as it reads in the UI: `IF ip in_cidr 10.0.0.0/8 THEN block`."""
        return f"IF {self.subject} {self.operator} {self.value} THEN {self.action}"
