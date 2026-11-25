"""Authentication: signing in, signing out, and session revocation."""

from __future__ import annotations

from datetime import UTC, datetime

from database.models import UserSession
from tests.helpers import api_token, auth_headers, make_user


async def test_browser_routes_are_absent(client):
    assert (await client.get("/")).status_code == 404
    assert (await client.get("/login")).status_code == 404
    assert (await client.get("/docs")).status_code == 404
    assert (await client.get("/redoc")).status_code == 404


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
