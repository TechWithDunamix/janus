"""Application assembly.

`create_app` is the single place Janus is put together: middleware, then
infrastructure, then routes. Keeping it a function rather than module-level
code means tests can build an isolated instance, and the import in
`app/main.py` stays trivial.
"""

from __future__ import annotations

from typing import Any

from sillo import SilloApp
from sillo.auth import AuthenticationMiddleware
from sillo.auth.session_auth import SessionAuthBackend
from sillo.middleware import BaseMiddleware
from sillo.record import setup_record
from sillo.security import CorsConfig, CORSMiddleware
from sillo.security.csrf import CSRFConfig, CSRFMiddleware
from sillo.session import SessionConfig, SessionMiddleware

from app.config import BASE_DIR, check_production, config, cors_origins
from app.inertia import BUILD_DIR, build_inertia, share_globals
from database.models import User

__all__ = ["create_app"]


def create_app() -> SilloApp:
    """Build and return the configured application."""
    from routes.web import routes as web_routes

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
        # Copied, because the parameter's default is a shared mutable list and
        # handing it our own keeps the two from ever being the same object.
        routes=list(web_routes),
    )

    _register_middleware(application)
    _register_database(application)
    _register_work(application)
    _register_static(application)
    _register_public(application)
    _register_routers(application)
    _register_health(application)
    _register_bootstrap_hooks(application)

    return application


def _register_middleware(application: SilloApp) -> None:
    """Attach middleware.

    `application.use()` builds the chain inside-out: whatever is registered
    *last* ends up outermost and runs *first* on the way in. The registrations
    below are in reverse of runtime order, which is

        CORS → session → CSRF → authentication → Inertia → handler

    Each position is load-bearing:

    * Session is outside authentication because `SessionAuthBackend` reads
      `ctx.session`. The other way round and `ctx.user` is never set.
    * Inertia is innermost because route modules call the module-level
      `render()`, which resolves the adapter from a context variable that only
      exists inside this middleware. It also reads and clears the flash and
      error bags, which live in the session — so it must be inside that too.
    * CSRF is outside authentication so a rejected token never reaches a
      database lookup.
    """
    # Outermost of Janus's own middleware, so the analytics memo is reset
    # before any handler or deferred prop resolves. It has to be per request:
    # a process-wide cache would serve one operator's numbers to the next.
    application.use(_ResetAnalyticsMemo())

    inertia = build_inertia()
    share_globals(inertia)
    inertia.middleware(application)
    application.state["inertia"] = inertia

    application.use(
        AuthenticationMiddleware(user_model=User, backend=SessionAuthBackend())
    )

    # Inertia's client is axios, which auto-attaches a CSRF header on unsafe
    # methods — but under its own convention: it reads the `XSRF-TOKEN` cookie
    # and sends `X-XSRF-TOKEN`. Sillo's defaults are `csrftoken` and
    # `X-CSRFToken`, so left alone every POST from the front end is rejected
    # with 403 and nothing on either side explains why.
    #
    # `httponly` is off for this cookie alone, because axios cannot read a
    # cookie the browser hides from JavaScript. That is safe precisely because
    # the token is useless without the session cookie, which stays httponly.
    application.use(
        CSRFMiddleware(
            config=CSRFConfig(
                enabled=True,
                cookie_name="XSRF-TOKEN",
                header_name="X-XSRF-TOKEN",
                cookie_httponly=False,
                cookie_secure=config.cookie_secure,
                secret_key=config.secret_key,
                # The JSON API is exempt, and it has to be. CSRF exists to stop
                # a cross-site request riding on credentials the *browser*
                # attaches by itself — cookies. `/api` authenticates on an
                # `Authorization: Bearer` header and on nothing else: a session
                # cookie sent to it is ignored by `routes/api::actor_for`, and
                # a cross-site form cannot set that header. So there is no
                # credential for a forged request to ride on, and leaving the
                # check on would reject every CLI call with "CSRF token missing
                # from cookies" — which is exactly what it did before this line
                # existed.
                exempt_urls=[r"^/api/.*"],
            )
        )
    )

    application.use(
        SessionMiddleware(
            config=SessionConfig(
                session_cookie_name=config.session_cookie_name,
                session_expiration_time=config.session_lifetime,
                session_cookie_secure=config.cookie_secure,
            ),
            secret_key=config.secret_key,
        )
    )

    application.use(
        CORSMiddleware(
            config=CorsConfig(allow_origins=cors_origins(), allow_credentials=True)
        )
    )


class _ResetAnalyticsMemo(BaseMiddleware):
    """Give each request a fresh rollup memo.

    One overview page resolves six deferred props and four of them want the
    same window of rollups. Without a memo the identical 6,000-row query ran
    six times and the page took five seconds; with it, once.
    """

    async def dispatch(self, ctx: Any, call_next: Any) -> Any:
        from app.services.analytics import begin_request

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
        from app.queue import bind_jobs

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
        from app.authz import ensure_roles

        await ensure_roles()

    application.on_startup(_ensure_roles)


def _register_static(application: SilloApp) -> None:
    """Serve the compiled front end.

    Only used when `VITE_DEV` is off. The mount point is `/assets` because that
    is what the adapter builds URLs against: it takes each file named in the
    Vite manifest, strips the leading `assets/`, and prefixes `asset_prefix`.
    So the directory served here is the `assets` folder *inside* the build
    output, not the build output itself — mount one level up and every script
    404s against a manifest that is perfectly correct.
    """
    from sillo.core.routing import Group
    from sillo.static import StaticFiles

    assets = BUILD_DIR / "assets"
    if not assets.is_dir():
        # Nothing built yet. Mounting a missing directory raises at startup,
        # which would make `npm run build` a prerequisite for the tests.
        return
    application.add_route(Group(path="/assets", app=StaticFiles(directory=str(assets))))


def _register_public(application: SilloApp) -> None:
    """Serve `public/` — checked-in assets that keep their names across deploys."""
    from sillo.core.routing import Group
    from sillo.static import StaticFiles

    public = BASE_DIR / "public"
    if not public.is_dir():
        return
    application.add_route(Group(path="/static", app=StaticFiles(directory=str(public))))


def _register_routers(application: SilloApp) -> None:
    """Mount the prefixed routers.

    Pages went into `SilloApp(routes=...)` as `Route` objects, each an exact
    path, so nothing they claim overlaps what is mounted here. Only the
    genuinely prefixed tree is a router: a mounted router claims its entire
    prefix subtree, so a prefix-less one would swallow every page.
    """
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
