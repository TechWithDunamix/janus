"""Route management, and the check that no dashboard route is left unguarded."""

from __future__ import annotations

from database.models import AuditEvent, GatewayRoute
from tests.helpers import (
    api_token,
    auth_headers,
    make_gateway,
    make_upstream,
    make_user,
)


class TestRouteCrud:
    async def test_create(self, client):
        gateway = await make_gateway()
        upstream = await make_upstream(gateway)
        user = await make_user(role="Operator")
        token = await api_token(client, user)

        response = await client.post(
            "/api/routes",
            json={
                "name": "Orders", "path": "/api/orders*", "methods": "POST",
                "upstream": upstream.slug, "priority": 100, "auth_policy": "api_key",
            },
            headers=auth_headers(token),
        )
        assert response.status_code == 201
        route = response.json()["route"]
        assert route["path"] == "/api/orders*"
        assert route["upstream"] == upstream.name
        assert route["auth_policy"] == "api_key"

    async def test_create_with_an_unknown_upstream_is_refused(self, client):
        await make_gateway()
        user = await make_user(role="Operator")
        token = await api_token(client, user)
        response = await client.post(
            "/api/routes",
            json={"name": "x", "upstream": "nope"},
            headers=auth_headers(token),
        )
        assert response.status_code == 404

    async def test_update(self, client):
        gateway = await make_gateway()
        route = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Orders", slug="orders", path="/a*", priority=1
        )
        user = await make_user(role="Operator")
        token = await api_token(client, user)

        response = await client.patch(
            f"/api/routes/{route.pk}",
            json={"path": "/b*", "priority": 50},
            headers=auth_headers(token),
        )
        assert response.status_code == 200
        await route.refresh_from_db()
        assert route.path == "/b*"
        assert route.priority == 50

    async def test_enable_and_disable(self, client):
        gateway = await make_gateway()
        route = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Orders", slug="orders", path="/a*"
        )
        user = await make_user(role="Operator")
        token = await api_token(client, user)

        await client.post(
            f"/api/routes/{route.pk}/toggle", json={"enabled": False},
            headers=auth_headers(token),
        )
        await route.refresh_from_db()
        assert route.enabled is False

        await client.post(
            f"/api/routes/{route.pk}/toggle", json={"enabled": True},
            headers=auth_headers(token),
        )
        await route.refresh_from_db()
        assert route.enabled is True

    async def test_delete(self, client):
        gateway = await make_gateway()
        route = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Orders", slug="orders", path="/a*"
        )
        user = await make_user(role="Operator")
        token = await api_token(client, user)

        response = await client.delete(f"/api/routes/{route.pk}", headers=auth_headers(token))
        assert response.status_code == 200
        assert await GatewayRoute.filter(pk=route.pk).count() == 0

    async def test_a_route_from_another_gateway_is_not_reachable(self, client):
        """The id in the URL must not be able to select a row outside the
        gateway the request is scoped to."""
        first = await make_gateway("First", slug="first")
        second = await make_gateway("Second", slug="second")
        route = await GatewayRoute.create(
            gateway_id=second.pk, name="Hidden", slug="hidden", path="/x*"
        )
        user = await make_user(role="Operator")
        token = await api_token(client, user)

        response = await client.get(
            f"/api/routes/{route.pk}", params={"gateway": first.slug},
            headers=auth_headers(token),
        )
        assert response.status_code == 404


class TestAuditing:
    async def test_creating_a_route_is_audited_with_the_actor_and_origin(self, client):
        gateway = await make_gateway()
        upstream = await make_upstream(gateway)
        user = await make_user(role="Operator")
        token = await api_token(client, user)

        await client.post(
            "/api/routes",
            json={"name": "Orders", "path": "/a*", "upstream": upstream.slug},
            headers=auth_headers(token),
        )
        event = await AuditEvent.filter(action="route.created").first()
        assert event is not None
        assert event.actor_label == user.email
        assert event.origin == "cli"
        assert event.after["path"] == "/a*"

    async def test_an_update_records_the_previous_values(self, client):
        """Before/after is what makes the trail useful during an incident."""
        gateway = await make_gateway()
        route = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Orders", slug="orders", path="/a*", priority=1
        )
        user = await make_user(role="Operator")
        token = await api_token(client, user)

        await client.patch(
            f"/api/routes/{route.pk}", json={"priority": 99}, headers=auth_headers(token)
        )
        event = await AuditEvent.filter(action="route.updated").first()
        assert event.before["priority"] == 1
        assert event.after["priority"] == 99
