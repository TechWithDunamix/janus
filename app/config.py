"""Settings, read once from the environment.

Janus is configured the twelve-factor way: every setting below has a working
default, and a deployment changes behaviour by setting environment variables
and nothing else. No code path is conditional on a hostname or an environment
name — `app_env` is a label that appears in the UI and in health output, not a
switch that turns features on.

The one exception is :func:`check_production`, which refuses to let the process
boot with development secrets when `APP_ENV=production`. That is a guard, not a
branch: it changes nothing about how the application behaves, it only declines
to start when the configuration would be unsafe.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


#: The insecure default. Named so `check_production` can compare against it
#: rather than duplicating the literal, and so a grep for it finds both ends.
INSECURE_SECRET = "dev-only-insecure-secret-key"


@dataclass(frozen=True)
class Config:
    """Every setting Janus reads, resolved at import."""

    app_name: str = field(default_factory=lambda: os.getenv("APP_NAME", "Janus"))
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "local"))
    app_url: str = field(default_factory=lambda: os.getenv("APP_URL", "http://localhost:8000"))
    debug: bool = field(default_factory=lambda: _bool("APP_DEBUG", True))

    secret_key: str = field(default_factory=lambda: os.getenv("SECRET_KEY", INSECURE_SECRET))

    # ---- Database ---------------------------------------------------------
    # SQLite on a laptop, Postgres in a container. Every column Janus defines
    # is portable between the two: no array columns, no JSON operators in a
    # WHERE clause, and every timestamp is timezone-aware.
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", f"sqlite://{BASE_DIR / 'storage' / 'janus.db'}"
        )
    )
    db_pool_size: int = field(default_factory=lambda: _int("DB_POOL_SIZE", 10))
    db_echo: bool = field(default_factory=lambda: _bool("DB_ECHO", False))
    db_generate_schemas: bool = field(default_factory=lambda: _bool("DB_GENERATE_SCHEMAS", True))

    # ---- Caddy data plane -------------------------------------------------
    #: Caddy's admin API. Janus never proxies traffic itself; this is the only
    #: address it talks to, and `app/caddy/` is the only code that does.
    caddy_admin_url: str = field(
        default_factory=lambda: os.getenv("CADDY_ADMIN_URL", "http://localhost:2019")
    )
    #: How long a call to the admin API may take. Applying a configuration is
    #: the slow one — Caddy provisions TLS and health checkers before it
    #: answers — so this is generous by the standards of an HTTP client.
    caddy_timeout: float = field(default_factory=lambda: float(os.getenv("CADDY_TIMEOUT", "15")))
    #: The id Janus stamps on the server it owns inside Caddy's config. This is
    #: what makes "only manage what Janus owns" enforceable rather than a
    #: promise: every write is scoped to this path, and anything else in the
    #: running config is read but never rewritten.
    caddy_server_name: str = field(
        default_factory=lambda: os.getenv("CADDY_SERVER_NAME", "janus")
    )
    #: The address Caddy's Janus-owned server listens on.
    caddy_listen: str = field(default_factory=lambda: os.getenv("CADDY_LISTEN", ":8080"))
    #: Where Caddy writes the JSON access log Janus ingests. Caddy is
    #: configured to write it by the configuration Janus generates.
    caddy_access_log: str = field(
        default_factory=lambda: os.getenv(
            "CADDY_ACCESS_LOG", str(BASE_DIR / "storage" / "access.log")
        )
    )
    #: Caddy's Prometheus endpoint, used for aggregate counters.
    caddy_metrics_url: str = field(
        default_factory=lambda: os.getenv("CADDY_METRICS_URL", "http://localhost:2019/metrics")
    )
    #: When on, `app/caddy/transport.py` serves an in-process Caddy admin API
    #: instead of dialling a real one.
    #:
    #: **Off by default.** Janus manages Caddy; the normal case is that there
    #: is one to manage. Defaulting to a simulator meant an operator with a
    #: perfectly good Caddy running still saw a banner telling them their
    #: gateway was not real. Opt in with `CADDY_SIMULATE=true` where there
    #: genuinely is no gateway — CI, or a laptop without the binary.
    #:
    #: It stands in for the admin API's *contract*, not for Caddy: it proxies
    #: nothing, and it cannot tell you whether a configuration will actually
    #: provision. Everything produced while it is on is marked `simulated` in
    #: the database and labelled in the UI.
    caddy_simulate: bool = field(default_factory=lambda: _bool("CADDY_SIMULATE", False))

    # ---- Queue ------------------------------------------------------------
    queue_backend: str = field(default_factory=lambda: os.getenv("QUEUE_BACKEND", "memory"))
    redis_url: str = field(
        default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )

    # ---- Sessions ---------------------------------------------------------
    session_cookie_name: str = field(
        default_factory=lambda: os.getenv("SESSION_COOKIE", "janus_session")
    )
    session_lifetime: int = field(default_factory=lambda: _int("SESSION_LIFETIME", 60 * 60 * 12))

    #: Whether session and CSRF cookies carry `Secure`. An explicit setting
    #: rather than a guess from `app_env`: Janus behind a TLS-terminating proxy
    #: in staging wants it on, and a plain-HTTP docker-compose wants it off, and
    #: neither is inferable from the environment's name. A `Secure` cookie on
    #: http:// is accepted by the browser and then never sent back, which reads
    #: as "login silently does nothing".
    cookie_secure: bool = field(
        default_factory=lambda: _bool("COOKIE_SECURE", os.getenv("APP_ENV", "local") == "production")
    )

    # ---- Front end --------------------------------------------------------
    vite_dev: bool = field(default_factory=lambda: _bool("VITE_DEV", True))
    vite_dev_server: str = field(
        default_factory=lambda: os.getenv("VITE_DEV_SERVER", "http://localhost:5173")
    )

    # ---- Analytics --------------------------------------------------------
    #: How long request-level rows are kept. Rollups outlive them: the
    #: per-minute aggregates are what the long time ranges read, and they are
    #: three orders of magnitude smaller than the rows they summarise.
    request_retention_hours: int = field(
        default_factory=lambda: _int("REQUEST_RETENTION_HOURS", 24 * 7)
    )
    rollup_retention_days: int = field(default_factory=lambda: _int("ROLLUP_RETENTION_DAYS", 90))
    #: A route is CRITICAL above this error rate, WARNING above half of it.
    #: Thresholds live in configuration because "bad" is deployment-specific:
    #: an internal batch API and a public checkout do not share a definition.
    error_rate_critical: float = field(
        default_factory=lambda: float(os.getenv("ERROR_RATE_CRITICAL", "0.05"))
    )
    latency_p95_critical_ms: int = field(
        default_factory=lambda: _int("LATENCY_P95_CRITICAL_MS", 1000)
    )

    @property
    def scheme(self) -> str:
        return "https" if self.app_url.startswith("https://") else "http"


config = Config()


def cors_origins() -> list[str]:
    """Origins allowed to call the API with credentials.

    Defaults to the application's own URL. A wildcard is deliberately not the
    default: `allow_credentials=True` with `*` is rejected by browsers anyway,
    and a control plane is not a public API.
    """
    raw = os.getenv("CORS_ORIGINS", "")
    if raw.strip():
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [config.app_url]


def check_production() -> list[str]:
    """Configuration that is unsafe to run in production.

    Returned rather than raised so `/health` can report every problem at once
    instead of the first one. `app/bootstrap.py` turns a non-empty list into a
    refusal to start when `APP_ENV=production`.
    """
    warnings: list[str] = []
    if config.app_env != "production":
        return warnings

    if config.secret_key == INSECURE_SECRET:
        warnings.append("SECRET_KEY is the development default.")
    if config.debug:
        warnings.append("APP_DEBUG is on.")
    if not config.cookie_secure:
        warnings.append("COOKIE_SECURE is off, so session cookies are sent over plain HTTP.")
    if config.caddy_simulate:
        warnings.append(
            "CADDY_SIMULATE is on: Janus is not talking to a real Caddy and all "
            "gateway state is simulated."
        )
    if config.database_url.startswith("sqlite://"):
        warnings.append("DATABASE_URL is SQLite.")
    return warnings
