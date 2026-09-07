"""The gateway estate: instances, upstreams, domains and routes.

These rows are Janus's *desired state*. Nothing here is read from Caddy — the
direction of travel is always this table, then the configuration engine, then
Caddy. What comes back the other way is only ever compared, never merged: see
`app/services/configuration.py::detect_drift`.

The split between :class:`Upstream` and :class:`UpstreamTarget` is the reason
load balancing works at all. A route points at an upstream by name, and the
upstream owns the list of dial addresses and their weights, so moving a backend
in or out of rotation touches one row and no routes.
"""

from __future__ import annotations

from sillo.record import Model
from tortoise import fields

__all__ = [
    "Domain",
    "Gateway",
    "GatewayRoute",
    "Upstream",
    "UpstreamTarget",
]


#: Where a gateway's configuration stands relative to Janus's desired state.
#: These four are the whole vocabulary and they appear in the UI, the API and
#: the CLI unchanged.
SYNC_STATES = ("SYNCED", "SYNCING", "FAILED", "DRIFTED")


class Gateway(Model):
    """One Caddy instance Janus manages.

    Janus supports more than one because a real estate has more than one — an
    edge tier and an internal tier, or one per region — and they do not share a
    configuration. Each carries its own admin URL and its own sync state.
    """

    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=100, unique=True)
    slug = fields.CharField(max_length=100, unique=True, index=True)
    description = fields.TextField(null=True)

    #: Caddy's admin API for this instance. Empty means "use the global
    #: `CADDY_ADMIN_URL`", which is the single-gateway case and the default.
    admin_url = fields.CharField(max_length=255, null=True)
    #: The address Caddy's Janus-owned server listens on, e.g. `:443`.
    listen = fields.CharField(max_length=100, default=":8080")

    region = fields.CharField(max_length=60, null=True)
    enabled = fields.BooleanField(default=True)

    #: Module ids an operator has recorded for this gateway's Caddy build.
    #:
    #: Needed because `caddy list-modules` runs whatever binary is on the
    #: *control plane's* host, which says nothing about a Caddy on another
    #: machine. For a remote gateway an operator records what they built, and
    #: the UI labels that as declared rather than detected — an assumption
    #: presented as an observation is how a dashboard starts lying.
    declared_modules = fields.JSONField(null=True)

    #: Where this gateway's Caddy writes the access log Janus ingests.
    #:
    #: Per gateway, not global, and that is a correctness requirement rather
    #: than a convenience. The collector reads a file per gateway and keeps a
    #: byte offset per gateway; point two gateways at one file and every
    #: request is ingested twice, once under each — which shows up as a
    #: gateway reporting traffic it never served, with nothing failing to say
    #: so. Null means the per-slug default in :meth:`access_log_path`.
    access_log = fields.CharField(max_length=500, null=True)

    #: Whether the gateway as a whole is in maintenance. When on, the generated
    #: configuration answers every route with the maintenance response rather
    #: than proxying — the routes stay defined, so turning it off restores
    #: service without a re-deploy of the route table.
    maintenance = fields.BooleanField(default=False)
    maintenance_status = fields.IntField(default=503)
    maintenance_body = fields.TextField(null=True)

    sync_state = fields.CharField(max_length=16, default="SYNCED", index=True)
    sync_error = fields.TextField(null=True)
    last_synced_at = fields.DatetimeField(null=True)
    #: The `ConfigVersion` currently live on this gateway. Nullable until the
    #: first successful deployment.
    active_version_id = fields.IntField(null=True)

    #: Last successful health probe of the admin API, and what it said.
    last_seen_at = fields.DatetimeField(null=True)
    caddy_version = fields.CharField(max_length=60, null=True)
    #: True when this gateway is backed by the in-process simulator rather than
    #: a real Caddy. Stored rather than read from configuration so the UI can
    #: label a *specific* gateway as simulated, and so the label survives in
    #: audit history after the setting changes.
    simulated = fields.BooleanField(default=False)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "gateways"

    def __str__(self) -> str:
        return self.name

    @property
    def access_log_path(self) -> str:
        """This gateway's log file, defaulting to one named after its slug."""
        if self.access_log:
            return self.access_log
        from app.config import BASE_DIR

        return str(BASE_DIR / "storage" / f"access-{self.slug}.log")


class Upstream(Model):
    """A named pool of backend servers.

    Health checking is declared here and executed by Caddy, not by Janus. That
    is deliberate and it is the difference between a control plane and a proxy:
    Janus writes the health-check policy into the configuration and reads the
    results back, but no Janus process sits in the request path.
    """

    id = fields.IntField(pk=True)
    gateway = fields.ForeignKeyField(
        "models.Gateway", related_name="upstreams", on_delete=fields.CASCADE
    )
    name = fields.CharField(max_length=100, index=True)
    slug = fields.CharField(max_length=100, index=True)
    description = fields.TextField(null=True)

    #: Caddy's `load_balancing.selection_policy`. `weighted_round_robin` is the
    #: default because targets carry weights and a policy that ignored them
    #: would make the weight column a lie.
    policy = fields.CharField(max_length=40, default="weighted_round_robin")

    #: Passive health: how many failures inside the window take a target out.
    max_fails = fields.IntField(default=3)
    fail_duration_seconds = fields.IntField(default=30)
    #: Active health: Caddy polls this path. Empty disables active checking.
    health_path = fields.CharField(max_length=255, null=True)
    health_interval_seconds = fields.IntField(default=30)
    health_timeout_seconds = fields.IntField(default=5)
    health_expect_status = fields.IntField(default=200)

    dial_timeout_seconds = fields.IntField(default=10)
    #: Per-target connection ceiling. 0 means unlimited, which is Caddy's own
    #: default and the right one for a pool fronting a service that manages its
    #: own concurrency.
    max_connections = fields.IntField(default=0)

    #: Maintenance takes the whole pool out without deleting it, which is what
    #: an operator draining a service actually wants.
    maintenance = fields.BooleanField(default=False)
    enabled = fields.BooleanField(default=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "upstreams"
        unique_together = (("gateway_id", "slug"),)

    def __str__(self) -> str:
        return self.name


class UpstreamTarget(Model):
    """One backend server inside a pool."""

    id = fields.IntField(pk=True)
    upstream = fields.ForeignKeyField(
        "models.Upstream", related_name="targets", on_delete=fields.CASCADE
    )
    #: `host:port`, exactly as Caddy dials it.
    dial = fields.CharField(max_length=255)
    weight = fields.IntField(default=1)
    enabled = fields.BooleanField(default=True)

    #: Last known health, written by the health poller in
    #: `app/services/health.py` from what Caddy reports. Advisory: Caddy is the
    #: authority, this is the cached answer the dashboard reads.
    healthy = fields.BooleanField(default=True)
    last_checked_at = fields.DatetimeField(null=True)
    last_error = fields.CharField(max_length=255, null=True)

    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "upstream_targets"
        unique_together = (("upstream_id", "dial"),)

    def __str__(self) -> str:
        return f"{self.dial} (weight {self.weight})"


class Domain(Model):
    """A hostname the gateway answers on."""

    id = fields.IntField(pk=True)
    gateway = fields.ForeignKeyField(
        "models.Gateway", related_name="domains", on_delete=fields.CASCADE
    )
    hostname = fields.CharField(max_length=255, index=True)

    #: `auto` lets Caddy provision a certificate; `internal` uses Caddy's local
    #: CA, which is what a laptop and a private network want; `off` serves
    #: plain HTTP; `custom` means a certificate is supplied out of band.
    tls_mode = fields.CharField(max_length=20, default="auto")
    tls_status = fields.CharField(max_length=20, default="unknown")
    tls_expires_at = fields.DatetimeField(null=True)
    tls_issuer = fields.CharField(max_length=120, null=True)

    enabled = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "domains"
        unique_together = (("gateway_id", "hostname"),)

    def __str__(self) -> str:
        return self.hostname


class GatewayRoute(Model):
    """One routing rule: what to match, and what to do with it.

    Ordering is by `priority` descending, then by id, and the configuration
    engine emits them in exactly that order. Caddy matches the first route that
    matches, so priority is not decoration — a catch-all at priority 0 and a
    specific path at priority 100 is the difference between a working API and
    every request landing on the catch-all.
    """

    id = fields.IntField(pk=True)
    gateway = fields.ForeignKeyField(
        "models.Gateway", related_name="routes", on_delete=fields.CASCADE
    )
    name = fields.CharField(max_length=120)
    slug = fields.CharField(max_length=120, index=True)
    description = fields.TextField(null=True)

    # ---- Matching ---------------------------------------------------------
    #: Empty matches every host the gateway serves.
    domain = fields.ForeignKeyField(
        "models.Domain", related_name="routes", null=True, on_delete=fields.SET_NULL
    )
    #: A Caddy path matcher — `/api/orders`, or `/api/*` for a subtree.
    path = fields.CharField(max_length=500, default="/*")
    #: Comma-separated, empty meaning every method. Stored as text rather than
    #: a JSON column so it can be filtered and grouped in plain SQL on both
    #: SQLite and Postgres.
    methods = fields.CharField(max_length=120, default="")
    priority = fields.IntField(default=0, index=True)

    # ---- Handling ---------------------------------------------------------
    upstream = fields.ForeignKeyField(
        "models.Upstream", related_name="routes", null=True, on_delete=fields.SET_NULL
    )
    #: `proxy`, `redirect`, `static` or `maintenance`. A route that redirects
    #: needs no upstream, which is why upstream is nullable.
    action = fields.CharField(max_length=20, default="proxy")
    redirect_to = fields.CharField(max_length=500, null=True)
    redirect_status = fields.IntField(default=302)
    static_body = fields.TextField(null=True)
    static_status = fields.IntField(default=200)

    # ---- Transformation ---------------------------------------------------
    #: `strip_prefix` removes the matched prefix before proxying; `rewrite_to`
    #: replaces the path wholesale. Both are Caddy primitives, kept as separate
    #: columns because they compose differently and an operator picks one.
    strip_prefix = fields.CharField(max_length=255, null=True)
    rewrite_to = fields.CharField(max_length=500, null=True)
    #: JSON objects: header name to value. Kept as JSON because they are only
    #: ever read whole, into the configuration generator, and never queried.
    request_headers = fields.JSONField(null=True)
    response_headers = fields.JSONField(null=True)
    remove_request_headers = fields.JSONField(null=True)
    remove_response_headers = fields.JSONField(null=True)

    # ---- Reliability ------------------------------------------------------
    timeout_seconds = fields.IntField(default=30)
    read_timeout_seconds = fields.IntField(default=0)
    retries = fields.IntField(default=0)
    #: Traffic split: when set, this fraction of matching requests goes to
    #: `canary_upstream` instead. 0 disables the split entirely.
    canary_upstream = fields.ForeignKeyField(
        "models.Upstream", related_name="canary_routes", null=True, on_delete=fields.SET_NULL
    )
    canary_percent = fields.IntField(default=0)

    # ---- Policy -----------------------------------------------------------
    #: `public`, `api_key`, `jwt`, `user`, `role`, `scope`. What the gateway
    #: requires before forwarding. `app/caddy/config_builder.py` turns this
    #: into the matchers and handlers that enforce it.
    auth_policy = fields.CharField(max_length=20, default="public")
    #: For `role` and `scope` policies — the required value.
    auth_requirement = fields.CharField(max_length=120, null=True)

    enabled = fields.BooleanField(default=True, index=True)
    maintenance = fields.BooleanField(default=False)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "gateway_routes"
        unique_together = (("gateway_id", "slug"),)
        indexes = (("gateway_id", "enabled"),)

    def __str__(self) -> str:
        return f"{self.method_label} {self.path}"

    @property
    def method_list(self) -> list[str]:
        return [m.strip().upper() for m in (self.methods or "").split(",") if m.strip()]

    @property
    def method_label(self) -> str:
        """How the route is written in a list — `POST /api/orders`, `ANY /*`."""
        methods = self.method_list
        return "/".join(methods) if methods else "ANY"
