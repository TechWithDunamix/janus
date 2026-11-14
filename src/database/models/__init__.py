"""Every model Janus defines, in one namespace.

`database/config.py` names this module as the one Tortoise scans, so a model
that is not imported here does not exist as far as the schema is concerned.

Deliberately absent: `sillo.users`. Janus's own :class:`User` sets
`table = "users"`, and adding the framework's module to the scan would register
a second model against the same table name. `sillo.permissions` *is* included —
see `database/config.py` — because Janus uses those tables as they ship.
"""

from database.models.analytics import (
    LATENCY_BUCKETS_MS,
    Alert,
    Anomaly,
    RequestLog,
    RouteRollup,
    bucket_for,
)
from database.models.configuration import (
    AuditEvent,
    ConfigVersion,
    Deployment,
    checksum_of,
)
from database.models.gateway import (
    SYNC_STATES,
    Domain,
    Gateway,
    GatewayRoute,
    Upstream,
    UpstreamTarget,
)
from database.models.identity import LoginEvent, User, UserSession
from database.models.security import (
    ApiKey,
    Client,
    IpRule,
    RateLimit,
    SecurityPolicy,
    normalise_cidr,
)

__all__ = [
    "LATENCY_BUCKETS_MS",
    "SYNC_STATES",
    "Alert",
    "Anomaly",
    "ApiKey",
    "AuditEvent",
    "Client",
    "ConfigVersion",
    "Deployment",
    "Domain",
    "Gateway",
    "GatewayRoute",
    "IpRule",
    "LoginEvent",
    "RateLimit",
    "RequestLog",
    "RouteRollup",
    "SecurityPolicy",
    "Upstream",
    "UpstreamTarget",
    "User",
    "UserSession",
    "bucket_for",
    "checksum_of",
    "normalise_cidr",
]
