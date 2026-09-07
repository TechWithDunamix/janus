"""The dashboard's route guard, and the decorators that apply it.

Every dashboard route runs the same checks, in this order:

    1. signed in?                 → /login
    2. is the session still live? → /login
    3. permission for this route  → 403 page, or a flash back for an action

The guard is attached as the route's `auth=`, not as a wrapper around the
handler, so `route.auth` is discoverable — a test can assert that every
dashboard route has one, which is the check that catches a route added without
protection.

Two decorators, because pages and actions fail differently. A page a user may
not see renders a 403 screen; an action they may not perform flashes an error
and sends them back. Rendering a full error page in response to a form post
would lose whatever they had typed.

Step 2 is why `UserSession` exists. Sillo's session middleware owns the cookie
and will happily keep honouring it after an administrator revokes the session;
checking the revocation on every request is what makes "revoke sessions" mean
something before the cookie expires.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from sillo.auth import useAuth
from sillo.auth.exceptions import AuthenticationFailed
from sillo.auth.exceptions import PermissionDenied as AuthPermissionDenied
from sillo.core.http import HttpContext
from sillo.responses import redirect
from sillo_inertia import back, render, set_flash

from app.authz import PermissionDenied, require

#: Where the lifted gate is stashed on a handler before `routes/web/__init__`
#: turns it into a `Route(..., auth=...)`.
GATE_ATTR = "_janus_gate"

__all__ = ["GATE_ATTR", "DashboardGate", "action", "form", "page", "public"]


class DashboardGate(useAuth):
    """Sign-in, session liveness and permission, as one route-level gate.

    Subclasses `useAuth` so the router runs it the way it runs any other
    authentication gate, and so `route.auth` is a real object a test can
    inspect rather than a closure hidden inside a decorator.
    """

    def __init__(self, *permissions: str, is_action: bool = False) -> None:
        super().__init__(required=True)
        self.permissions = permissions
        self.is_action = is_action

    async def authenticate(self, ctx: HttpContext) -> bool:  # type: ignore[override]
        user = _user_or_none(ctx)
        if user is None:
            # `useAuth`'s own `unauthorized` hook is not used for this, because
            # the answer differs by request kind: an Inertia XHR needs a 409
            # with a location header to force a full visit, or the client tries
            # to render the login HTML as a page object.
            raise AuthenticationFailed("Sign in to continue.")

        if not await _session_is_live(ctx):
            raise AuthenticationFailed("This session has been revoked.")

        ctx.state.auth_user = user

        if self.permissions:
            try:
                await require(user, *self.permissions)
            except PermissionDenied as error:
                ctx.state.missing_permission = error.permission
                raise AuthPermissionDenied(error.permission) from error

        return True


def _user_or_none(ctx: HttpContext) -> Any | None:
    """`ctx.user`, or `None` — never raising.

    A stale session cookie naming a row that no longer loads leaves
    `scope["user"]` unset and the property raises. That must read as "signed
    out", not as a 500 on every page.
    """
    try:
        user = ctx.user
    except Exception:  # noqa: BLE001
        return None
    return user if getattr(user, "is_authenticated", False) else None


async def _session_is_live(ctx: HttpContext) -> bool:
    """Whether the `UserSession` behind this cookie is still valid.

    A request whose session was never registered — one made before this
    tracking existed, or in a test that signs in directly — is allowed through.
    Failing closed here would mean a revocation feature that logs everyone out.
    """
    session = getattr(ctx, "session", None)
    if session is None:
        return True
    key = session.get("janus_session_key")
    if not key:
        return True

    from database.models import UserSession

    row = await UserSession.get_or_none(key_hash=key)
    return row is None or row.is_live


async def _unauthorized(ctx: HttpContext) -> Any:
    """Send an unauthenticated visitor to the login page.

    An Inertia request gets a 409 with `X-Inertia-Location` rather than a 302:
    the client follows a redirect with XHR and would receive the login page's
    HTML where it expects a page object, and render nothing at all.

    Async deliberately: `useAuth` runs a *sync* hook in a thread pool, and
    everything here reads the adapter out of a `ContextVar` that a worker
    thread is not guaranteed to inherit.
    """
    target = "/login"
    if ctx.headers.get("X-Inertia", "").lower() == "true":
        from sillo_inertia import location

        return location(target)
    return redirect(target)


async def _forbidden(ctx: HttpContext) -> Any:
    """Answer a user who may not do this."""
    permission = getattr(ctx.state, "missing_permission", None)

    if getattr(ctx.state, "is_action", None):
        set_flash(ctx, "error", f"You do not have permission to do that ({permission}).")
        return back(fallback="/", ctx=ctx)

    return await render("errors/Forbidden", {"permission": permission}, status_code=403, ctx=ctx)


def page(*permissions: str) -> Callable[..., Any]:
    """Guard a dashboard page. Refusal renders a 403 screen."""

    def decorate(handler: Callable[..., Any]) -> Callable[..., Any]:
        gate = DashboardGate(*permissions)
        gate.unauthorized = _unauthorized
        gate.forbidden = _forbidden
        setattr(handler, GATE_ATTR, gate)
        return handler

    return decorate


def action(*permissions: str) -> Callable[..., Any]:
    """Guard a dashboard mutation. Refusal flashes and sends the user back."""

    def decorate(handler: Callable[..., Any]) -> Callable[..., Any]:
        gate = DashboardGate(*permissions, is_action=True)
        gate.unauthorized = _unauthorized

        async def forbidden(ctx: HttpContext) -> Any:
            ctx.state.is_action = True
            return await _forbidden(ctx)

        gate.forbidden = forbidden

        @functools.wraps(handler)
        async def wrapper(ctx: HttpContext, *args: Any, **kwargs: Any) -> Any:
            ctx.state.is_action = True
            return await handler(ctx, *args, **kwargs)

        setattr(wrapper, GATE_ATTR, gate)
        return wrapper

    return decorate


def public(handler: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a route as deliberately unguarded.

    Explicit, so "this route has no gate" is a decision recorded in the code
    rather than something a reader infers from an absence. The test that
    asserts every dashboard route is guarded reads this attribute.
    """
    setattr(handler, GATE_ATTR, None)
    handler._janus_public = True
    return handler


async def form(ctx: HttpContext) -> dict[str, Any]:
    """The submitted body, whether it arrived as JSON or as a form.

    `ctx.json` and `ctx.form` are `@property async def`, so they are awaited
    *without* parentheses — `await ctx.json()` calls the coroutine object and
    raises `TypeError`, which to a handler catching broadly looks exactly like
    an empty body: every field fails validation and a valid submission comes
    back with three errors. Only `ValueError` is caught, for that reason.
    """
    content_type = (ctx.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            return dict(await ctx.json)
        except ValueError:
            return {}
    try:
        data = await ctx.form
    except ValueError:
        return {}
    return {key: data[key] for key in data}


def client_ip(ctx: HttpContext) -> str | None:
    """The requesting address, for the audit trail."""
    forwarded = ctx.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    client = getattr(ctx, "client", None)
    return getattr(client, "host", None)
