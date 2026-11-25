"""Application assembly.

`create_app` is the single place Janus is put together: middleware, then
infrastructure, then routes. Keeping it a function rather than module-level
code means tests can build an isolated instance, and the import in
`main.py` stays trivial.
"""

from __future__ import annotations

from typing import Any

from sillo import SilloApp
from sillo.middleware import BaseMiddleware
from sillo.record import setup_record

from core.config import check_production, config

__all__ = ["create_app"]


def create_app() -> SilloApp:
    """Build and return the configured application."""
    warnings = check_production()
    if warnings:
        # Refuse rather than warn. Every item in this list is a way for a
        # production control plane to be quietly insecure — a development
        # secret, a simulated gateway presented as real — and a log line at
        # startup is not where anyone will see it.
        raise RuntimeError(
            "Refusing to start in production with unsafe configuration:\n  - "
            + "\n  - ".join(warnings)
        )

    application = SilloApp(
        debug=config.debug,
        title=config.app_name,
        version="1.0.0",
        docs=[],  # keep machine-readable OpenAPI, omit browser viewers
        # Copied, because the parameter's default is a shared mutable list and
        # handing it our own keeps the two from ever being the same object.
        routes=[],
    )

    application.use(_ResetAnalyticsMemo())
    _register_database(application)
    _register_work(application)
    _register_routers(application)
    _register_health(application)
    _register_bootstrap_hooks(application)

    return application


class _ResetAnalyticsMemo(BaseMiddleware):
    """Give each request a fresh rollup memo.

    One overview page resolves six deferred props and four of them want the
    same window of rollups. Without a memo the identical 6,000-row query ran
    six times and the page took five seconds; with it, once.
    """

    async def dispatch(self, ctx: Any, call_next: Any) -> Any:
        """Start a fresh per-request analytics memo, then handle the request."""
        from modules.services.analytics import begin_request

        begin_request()
        return await call_next()


def _register_database(application: SilloApp) -> None:
    """Wire the Record ORM into the application lifecycle."""
    from database.config import MIGRATIONS_MODULE, MODEL_MODULES, database_config

    manager = setup_record(application, database_config(), model_modules=MODEL_MODULES)
    manager.set_migrations(MIGRATIONS_MODULE)


def _register_work(application: SilloApp) -> None:
    """The job queue.

    No scheduler here: exactly one instance of it should run and the web
    process may be scaled past one. In a container the scheduler is its own
    process (`janus scheduler`); on a laptop `janus work` runs the same jobs on
    demand.
    """
    from sillo.work import setup_work

    setup_work(application)

    async def _bind_queue() -> None:
        """Attach every job class to the queue connection at startup."""
        from core.queue import bind_jobs

        bind_jobs()

    application.on_startup(_bind_queue)


def _register_bootstrap_hooks(application: SilloApp) -> None:
    """Create the permission catalogue and default roles on startup.

    Idempotent, and it runs here rather than only in a migration because a
    permission added by an upgrade has to exist before the first request that
    checks it — otherwise every operator is missing a permission nobody has
    granted them yet.
    """

    async def _ensure_roles() -> None:
        """Create or update the built-in roles and permission catalogue (idempotent)."""
        from modules.auth.permissions import ensure_roles

        await ensure_roles()

    application.on_startup(_ensure_roles)


def _register_routers(application: SilloApp) -> None:
    """Mount the JSON API used by the CLI and automation."""
    from routes.api import router as api_router

    application.mount_router(api_router)  # /api


def _register_health(application: SilloApp) -> None:
    """A health endpoint that actually checks something.

    It touches the database rather than only confirming the process is alive —
    a container answering HTTP with a dead database should not be receiving
    traffic, and a check that only proves the event loop is running would
    happily let it.
    """
    from sillo.core.routing import Route
    from sillo.responses import json

    async def health(ctx: Any) -> Any:
        """``GET /health`` — database, queue and Caddy status; 200 when healthy, 503 otherwise.

        Unauthenticated so load balancers and the container healthcheck can use it.
        It touches the database, so a process with a dead database reports unhealthy.
        """
        checks: dict[str, Any] = {"app": config.app_name, "env": config.app_env}
        healthy = True

        try:
            from database.models import Gateway

            await Gateway.all().limit(1).count()
            checks["database"] = "ok"
        except Exception as error:  # noqa: BLE001
            checks["database"] = f"failed: {error}"
            healthy = False

        checks["queue"] = config.queue_backend
        checks["caddy"] = "simulated" if config.caddy_simulate else config.caddy_admin_url
        warnings = check_production()
        if warnings:
            checks["warnings"] = warnings

        return json(checks, status_code=200 if healthy else 503)

    application.add_route(Route("/health", handler=health, methods=["GET"], name="health"))
