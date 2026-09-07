"""Signing in and out.

The only unguarded routes in the dashboard, and the only place a
:class:`~database.models.UserSession` is created. Every sign-in attempt is
recorded whether or not it succeeded — failed logins in bursts are a security
signal, and the security dashboard counts them.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sillo.auth.session_auth import login as session_login
from sillo.auth.session_auth import logout as session_logout
from sillo.core.http import HttpContext
from sillo.responses import redirect
from sillo_inertia import render, set_errors, set_flash

from app.config import config
from app.services import audit
from database.models import LoginEvent, User, UserSession
from routes.web._kit import client_ip, form, public

__all__ = ["login", "login_submit", "logout"]


@public
async def login(ctx: HttpContext) -> Any:
    """The sign-in screen. Already-authenticated visitors go to the overview."""
    try:
        if getattr(ctx.user, "is_authenticated", False):
            return redirect("/")
    except Exception:  # noqa: BLE001
        pass
    return await render("auth/Login", {"app_name": config.app_name})


@public
async def login_submit(ctx: HttpContext) -> Any:
    """Verify credentials, register a session, record the attempt."""
    data = await form(ctx)
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    ip = client_ip(ctx)
    agent = ctx.headers.get("user-agent")

    async def refuse(reason: str, user: User | None = None) -> Any:
        await LoginEvent.create(
            user_id=getattr(user, "pk", None),
            email=email or "(blank)",
            successful=False,
            reason=reason,
            kind="web",
            ip=ip,
            user_agent=(agent or "")[:400] or None,
        )
        # One message for every failure mode. Saying "no such account" tells an
        # attacker which addresses are registered, which is a free enumeration
        # oracle on the front page of the control plane.
        set_errors(ctx, {"email": "Those credentials do not match our records."})
        return redirect("/login")

    if not email or not password:
        return await refuse("missing_fields")

    user = await User.get_or_none(email=email)
    if user is None:
        return await refuse("unknown_user")
    if not user.is_active:
        return await refuse("disabled", user)
    if not user.check_password(password):
        return await refuse("bad_password", user)

    # The framework's `login`, not a hand-written session write. It stores the
    # identity in the shape `SessionAuthBackend` reads — getting that shape
    # wrong means the cookie is set and `ctx.user` is never populated, which
    # presents as a login that redirects straight back to the login page — and
    # it calls `cycle_key()`, which is the session-fixation defence: an
    # attacker who planted a known session key in the victim's browser holds a
    # key that stops being valid the moment the victim signs in.
    session_login(ctx, user)

    # Janus's own record *about* that session, so it can be listed and revoked.
    # The key is stored as a digest: this table is read by the team screen, and
    # a readable session key in a list operators can see is a session anyone
    # with that screen could steal.
    raw_key = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw_key.encode()).hexdigest()
    ctx.session["janus_session_key"] = digest

    await UserSession.create(
        user_id=user.pk,
        key_hash=digest,
        kind="web",
        ip=ip,
        user_agent=(agent or "")[:400] or None,
        expires_at=datetime.now(UTC) + timedelta(seconds=config.session_lifetime),
        last_seen_at=datetime.now(UTC),
    )
    await user.set_last_login()
    await LoginEvent.create(
        user_id=user.pk, email=email, successful=True, reason="ok",
        kind="web", ip=ip, user_agent=(agent or "")[:400] or None,
    )
    await audit.record(
        action="user.signed_in", resource_type="user", resource_id=user.pk,
        resource_label=user.email, actor=user, ip=ip, user_agent=agent, origin="web",
    )
    return redirect("/")


@public
async def logout(ctx: HttpContext) -> Any:
    """End the session, and revoke its row so it cannot be reused."""
    key = ctx.session.get("janus_session_key") if hasattr(ctx, "session") else None
    if key:
        session_row = await UserSession.get_or_none(key_hash=key)
        if session_row is not None:
            session_row.revoked_at = datetime.now(UTC)
            session_row.revoked_reason = "signed out"
            await session_row.save()
    # The framework's `logout` removes the identity it wrote; clearing the
    # whole session on top of it drops the flash bag before the next request
    # can read it, which is why the flash is set afterwards.
    session_logout(ctx)
    if hasattr(ctx, "session"):
        ctx.session.pop("janus_session_key", None)
    set_flash(ctx, "success", "You have been signed out.")
    return redirect("/login")
