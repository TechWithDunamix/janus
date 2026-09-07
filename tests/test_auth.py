"""Authentication: signing in, signing out, and session revocation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from database.models import LoginEvent, UserSession
from tests.helpers import api_token, auth_headers, make_user, sign_in


class TestSigningIn:
    async def test_valid_credentials_start_a_session(self, client):
        user = await make_user()
        await sign_in(client, user)
        assert (await client.get("/")).status_code == 200

    async def test_invalid_password_is_refused(self, client):
        await make_user()
        await client.get("/login")
        token = client.cookies.get("XSRF-TOKEN") or ""
        response = await client.post(
            "/login",
            json={"email": "operator@test.local", "password": "wrong"},
            headers={"X-XSRF-TOKEN": token},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/login"
        assert (await client.get("/")).status_code == 302

    async def test_an_unknown_address_is_refused_the_same_way(self, client):
        """The refusal must not distinguish a wrong password from no account.

        Otherwise the login form is an oracle for which addresses are
        registered, on the front page of the control plane.
        """
        await make_user()
        await client.get("/login")
        token = client.cookies.get("XSRF-TOKEN") or ""

        unknown = await client.post(
            "/login",
            json={"email": "nobody@test.local", "password": "whatever"},
            headers={"X-XSRF-TOKEN": token},
            follow_redirects=False,
        )
        wrong = await client.post(
            "/login",
            json={"email": "operator@test.local", "password": "wrong"},
            headers={"X-XSRF-TOKEN": token},
            follow_redirects=False,
        )
        assert unknown.status_code == wrong.status_code
        assert unknown.headers["location"] == wrong.headers["location"]

    async def test_a_disabled_account_cannot_sign_in(self, client):
        user = await make_user(active=False)
        await client.get("/login")
        token = client.cookies.get("XSRF-TOKEN") or ""
        response = await client.post(
            "/login",
            json={"email": user.email, "password": "correct-horse-battery"},
            headers={"X-XSRF-TOKEN": token},
            follow_redirects=False,
        )
        assert response.headers["location"] == "/login"
        event = await LoginEvent.filter(email=user.email).order_by("-id").first()
        assert event.reason == "disabled"

    async def test_every_attempt_is_recorded(self, client):
        user = await make_user()
        await client.get("/login")
        token = client.cookies.get("XSRF-TOKEN") or ""
        await client.post(
            "/login",
            json={"email": user.email, "password": "wrong"},
            headers={"X-XSRF-TOKEN": token},
            follow_redirects=False,
        )
        await sign_in(client, user)

        events = await LoginEvent.filter(email=user.email).order_by("id")
        assert [e.successful for e in events] == [False, True]
        assert events[0].reason == "bad_password"


class TestSigningOut:
    async def test_logout_ends_the_session(self, client):
        user = await make_user()
        await sign_in(client, user)
        token = client.cookies.get("XSRF-TOKEN") or ""
        await client.post("/logout", headers={"X-XSRF-TOKEN": token})
        assert (await client.get("/")).status_code == 302

    async def test_logout_revokes_the_session_row(self, client):
        user = await make_user()
        await sign_in(client, user)
        token = client.cookies.get("XSRF-TOKEN") or ""
        await client.post("/logout", headers={"X-XSRF-TOKEN": token})

        session = await UserSession.filter(user_id=user.pk).first()
        assert session.revoked_at is not None
        assert session.is_live is False


class TestSessionLifetime:
    async def test_a_revoked_session_stops_working_on_the_next_request(self, client):
        """Revocation must not wait for the cookie to expire.

        The cookie is still valid and the browser still sends it; what stops
        the request is the guard checking the session row. Without that check
        "revoke session" would mean "revoke it eventually".
        """
        user = await make_user()
        await sign_in(client, user)
        assert (await client.get("/")).status_code == 200

        await UserSession.filter(user_id=user.pk).update(
            revoked_at=datetime.now(UTC), revoked_reason="revoked in a test"
        )
        assert (await client.get("/")).status_code == 302

    async def test_an_expired_session_is_not_live(self, client):
        user = await make_user()
        await sign_in(client, user)
        await UserSession.filter(user_id=user.pk).update(
            expires_at=datetime.now(UTC) - timedelta(minutes=1)
        )
        assert (await client.get("/")).status_code == 302

    async def test_disabling_a_user_revokes_their_sessions(self, client):
        user = await make_user()
        await sign_in(client, user)
        await user.disable(reason="test")
        assert (await client.get("/")).status_code == 302


class TestApiAuthentication:
    async def test_a_token_authenticates_api_calls(self, client):
        user = await make_user()
        token = await api_token(client, user)
        response = await client.get("/api/auth/whoami", headers=auth_headers(token))
        assert response.status_code == 200
        assert response.json()["email"] == user.email

    async def test_api_calls_without_a_token_are_refused(self, client):
        assert (await client.get("/api/routes")).status_code == 401

    async def test_a_garbage_token_is_refused(self, client):
        response = await client.get("/api/routes", headers=auth_headers("not-a-real-token"))
        assert response.status_code == 401

    async def test_revoking_a_cli_session_kills_the_token(self, client):
        user = await make_user()
        token = await api_token(client, user)
        assert (await client.get("/api/auth/whoami", headers=auth_headers(token))).status_code == 200

        await UserSession.filter(user_id=user.pk, kind="cli").update(
            revoked_at=datetime.now(UTC)
        )
        assert (await client.get("/api/auth/whoami", headers=auth_headers(token))).status_code == 401

    async def test_logout_revokes_the_token_it_was_called_with(self, client):
        user = await make_user()
        token = await api_token(client, user)
        await client.post("/api/auth/logout", headers=auth_headers(token))
        assert (await client.get("/api/auth/whoami", headers=auth_headers(token))).status_code == 401
