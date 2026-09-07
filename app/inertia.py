"""The Inertia adapter, configured once.

Route modules never import this — they call the module-level `render` from
`sillo_inertia`, which finds the adapter through the middleware handling the
request. That keeps `routes/` free of any import back into `app/`, and so free
of the circular import it would otherwise cause.

Shared props are deliberately few. Everything here is on every page object on
every navigation, so a prop only two screens need belongs on those two screens.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sillo.core.http import HttpContext
from sillo_inertia import Inertia, always, vite_react

from app.authz import permissions_for, role_names_of
from app.config import BASE_DIR, config

#: Where the compiled front end lands. Vite writes its manifest to
#: `.vite/manifest.json` *inside* the output directory as of Vite 5; the
#: adapter is handed the full path rather than a directory so a Vite upgrade
#: that moves it fails loudly here instead of rendering a page with no script.
BUILD_DIR = BASE_DIR / "static" / "build"
MANIFEST = BUILD_DIR / ".vite" / "manifest.json"

#: The client entry, relative to the project root. This exact string is the key
#: Vite writes into the manifest and the path the dev server serves; it must
#: match `build.rollupOptions.input` in vite.config.ts. If the two drift,
#: development still works and production ships a page with no JavaScript.
ENTRY = "js/main.tsx"

ROOT_VIEW = BASE_DIR / "resources" / "views" / "app.html"


def asset_version() -> str | None:
    """A fingerprint of the current build.

    Inertia refuses a request carrying a stale one, forcing a full visit so the
    client never renders a new page object against the previous build's
    components. `None` in development, where the dev server hot-reloads and a
    version check would only cause spurious reloads on every save.
    """
    if config.vite_dev:
        return None
    if not MANIFEST.is_file():
        return None
    return hashlib.sha256(MANIFEST.read_bytes()).hexdigest()[:12]


def build_inertia() -> Inertia:
    return Inertia(
        root_view=ROOT_VIEW,
        base_dir=BASE_DIR,
        version=asset_version,
        root_id="app",
        vite=vite_react(
            entry=ENTRY,
            dev=config.vite_dev,
            dev_server=config.vite_dev_server,
            manifest_path=MANIFEST,
            asset_prefix="/assets/",
        ),
    )


def _safe_user(ctx: HttpContext) -> Any | None:
    """`ctx.user`, or `None` — never raising.

    Sillo's auth middleware leaves `scope["user"]` unset when a session names
    an identity whose row no longer loads, and the property then raises. A
    stale cookie after a database rebuild must not 500 the shared props on
    every page.
    """
    try:
        user = ctx.user
    except Exception:  # noqa: BLE001
        return None
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user


async def _auth_prop(ctx: HttpContext) -> dict[str, Any]:
    """Who is signed in, and what they may do.

    The permission list is what `useCan()` reads to hide controls. It is
    cosmetic: the gate that refuses an action is on the route.
    """
    user = _safe_user(ctx)
    if user is None:
        return {"user": None, "permissions": [], "roles": []}
    return {
        # Listed by hand rather than serialised wholesale, so a column added to
        # the user model does not start being published by accident.
        "user": {
            "id": user.pk,
            "name": getattr(user, "name", None) or user.email,
            "email": user.email,
            "title": getattr(user, "title", None),
            "is_superuser": bool(getattr(user, "is_superuser", False)),
        },
        "permissions": await permissions_for(ctx),
        "roles": await role_names_of(user),
    }


async def _gateway_prop(ctx: HttpContext) -> dict[str, Any]:
    """The gateway picker in the top bar, and the selected one's sync state.

    Present on every page because the sync state is chrome: an operator editing
    a route needs to see that the gateway is DRIFTED without navigating to a
    different screen to find out.
    """
    if _safe_user(ctx) is None:
        return {"list": [], "current": None}

    from app.gateways import current_gateway_id, gateway_options

    options = await gateway_options()
    selected = await current_gateway_id(ctx)
    return {
        "list": options,
        "current": next((g for g in options if g["id"] == selected), options[0] if options else None),
    }


def share_globals(inertia: Inertia) -> None:
    """Register the props every page receives.

    `errors` and `flash` are registered by the adapter itself. These are
    callables, so they resolve per request rather than at startup, and
    `always`-wrapped so a partial reload that asks for one prop still refreshes
    the chrome around it.
    """
    inertia.view_data["title"] = config.app_name

    inertia.share(
        auth=always(_auth_prop),
        gateways=always(_gateway_prop),
        app=always(
            lambda _: {
                "name": config.app_name,
                "env": config.app_env,
                # Surfaced on every page because it changes how every number on
                # the screen should be read. A control plane that quietly
                # showed simulated gateway state as real would be worse than
                # one that showed nothing.
                "simulated": config.caddy_simulate,
            }
        ),
    )
