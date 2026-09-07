"""Authorization: every role, every permission, on both front doors.

The point of this module is that the dashboard and the CLI are the same gate.
Each operation is tested through the JSON API — which is what the CLI calls —
*and* through the dashboard route, so a permission that is enforced in one
place and not the other fails here rather than in production.
"""

from __future__ import annotations

import pytest

from app.authz import (
    DEFAULT_ROLES,
    PERMISSION_NAMES,
    PermissionDenied,
    permissions_of,
    require,
)
from tests.helpers import api_token, auth_headers, csrf, make_gateway, make_user, sign_in


class TestTheCatalogue:
    def test_every_default_role_holds_only_real_permissions(self):
        """A role granting a permission nothing checks is a role that lies."""
        for name, spec in DEFAULT_ROLES.items():
            unknown = set(spec["permissions"]) - set(PERMISSION_NAMES)
            assert not unknown, f"{name} grants unknown permission(s): {unknown}"

    def test_the_catalogue_has_no_duplicates(self):
        assert len(PERMISSION_NAMES) == len(set(PERMISSION_NAMES))

    def test_owner_holds_everything(self):
        assert set(DEFAULT_ROLES["Owner"]["permissions"]) == set(PERMISSION_NAMES)

    def test_viewer_holds_only_reads(self):
        for permission in DEFAULT_ROLES["Viewer"]["permissions"]:
            assert permission.endswith(".read"), f"Viewer holds a write permission: {permission}"

    def test_analyst_cannot_change_configuration(self):
        held = set(DEFAULT_ROLES["Analyst"]["permissions"])
        assert "configuration.write" not in held
        assert "routes.write" not in held
        assert "analytics.read" in held

    def test_operator_cannot_roll_back_or_manage_the_team(self):
        """Rolling back and managing people are a different authority from
        operating the gateway, which is the whole reason Operator exists."""
        held = set(DEFAULT_ROLES["Operator"]["permissions"])
        assert "configuration.write" in held
        assert "configuration.rollback" not in held
        assert "users.write" not in held
        assert "roles.write" not in held


class TestResolution:
    async def test_a_role_grants_its_permissions(self):
        user = await make_user("op@test.local", role="Operator")
        held = await permissions_of(user)
        assert "routes.write" in held
        assert "users.write" not in held

    async def test_a_superuser_holds_everything(self):
        """The Owner escape hatch: an installation whose roles were edited
        badly must still be recoverable."""
        user = await make_user("root@test.local", role=None, superuser=True)
        assert await permissions_of(user) == set(PERMISSION_NAMES)

    async def test_require_raises_naming_the_missing_permission(self):
        user = await make_user("viewer@test.local", role="Viewer")
        with pytest.raises(PermissionDenied) as error:
            await require(user, "routes.write")
        assert error.value.permission == "routes.write"

    async def test_require_with_no_permissions_always_passes(self):
        user = await make_user("nobody@test.local", role=None)
        await require(user)  # must not raise

    async def test_an_anonymous_actor_holds_nothing(self):
        assert await permissions_of(None) == set()


#: (role, method, path, body, expected status). Written out rather than
#: generated so a reader can see exactly which role may do what.
API_MATRIX = [
    ("Viewer", "GET", "/api/routes", None, 200),
    ("Viewer", "POST", "/api/routes", {"name": "x"}, 403),
    ("Viewer", "GET", "/api/analytics/overview", None, 200),
    ("Viewer", "POST", "/api/security/block", {"cidr": "10.0.0.1"}, 403),
    ("Viewer", "GET", "/api/users", None, 200),
    ("Viewer", "POST", "/api/config/apply", {}, 403),
    ("Analyst", "GET", "/api/analytics/routes", None, 200),
    ("Analyst", "GET", "/api/routes", None, 200),
    ("Analyst", "POST", "/api/routes", {"name": "x"}, 403),
    ("Analyst", "GET", "/api/config", None, 403),
    ("Analyst", "POST", "/api/config/apply", {}, 403),
    ("Analyst", "GET", "/api/users", None, 403),
    ("Operator", "GET", "/api/roles", None, 200),
    ("Operator", "POST", "/api/routes", {"name": "x", "path": "/x"}, 201),
    ("Operator", "GET", "/api/config", None, 200),
    ("Operator", "POST", "/api/security/block", {"cidr": "10.0.0.1"}, 201),
    ("Operator", "POST", "/api/config/rollback", {"version": 1}, 403),
    ("Operator", "POST", "/api/users", {"email": "a@b.c", "password": "x" * 12}, 403),
    ("Administrator", "POST", "/api/users", {"email": "a@b.c", "password": "x" * 12}, 201),
    ("Administrator", "POST", "/api/config/rollback", {"version": 99}, 422),
    ("Owner", "POST", "/api/roles", {"name": "Custom"}, 201),
    ("Administrator", "POST", "/api/roles", {"name": "Custom"}, 403),
]


@pytest.mark.parametrize("role,method,path,body,expected", API_MATRIX)
async def test_the_api_enforces_the_matrix(client, role, method, path, body, expected):
    """The CLI calls exactly these endpoints, so this is the CLI's matrix too."""
    await make_gateway()
    user = await make_user(f"{role.lower()}@test.local", role=role)
    token = await api_token(client, user)

    response = await client.request(
        method, path, json=body, headers=auth_headers(token)
    )
    assert response.status_code == expected, (
        f"{role} {method} {path} -> {response.status_code} "
        f"(expected {expected}): {response.text[:200]}"
    )


#: The dashboard half of the same question.
PAGE_MATRIX = [
    ("Viewer", "/", 200),
    ("Viewer", "/routes", 200),
    ("Viewer", "/team/roles", 200),
    ("Analyst", "/analytics", 200),
    ("Analyst", "/configuration", 403),
    ("Analyst", "/team/users", 403),
    ("Operator", "/configuration", 200),
    ("Operator", "/security/rate-limits", 200),
    ("Operator", "/team/roles", 200),
    ("Owner", "/team/users", 200),
]


@pytest.mark.parametrize("role,path,expected", PAGE_MATRIX)
async def test_dashboard_pages_enforce_the_matrix(client, role, path, expected):
    await make_gateway()
    user = await make_user(f"{role.lower()}2@test.local", role=role)
    await sign_in(client, user)
    response = await client.get(path)
    assert response.status_code == expected, f"{role} {path} -> {response.status_code}"


class TestActionsRefuseAndReturn:
    async def test_a_refused_dashboard_action_sends_the_user_back(self, client):
        """A rejected form post must not replace the page with an error screen:
        that loses whatever the operator had typed."""
        gateway = await make_gateway()
        user = await make_user("analyst2@test.local", role="Analyst")
        await sign_in(client, user)

        response = await client.post(
            "/routes/save",
            json={"name": "Blocked", "path": "/x"},
            headers=csrf(client),
        )
        assert response.status_code in (302, 303, 409)

        from database.models import GatewayRoute

        assert await GatewayRoute.filter(gateway_id=gateway.pk).count() == 0


class TestCliIsNotABypass:
    async def test_the_same_operation_is_refused_on_both_doors(self, client):
        """The regression this whole design exists to prevent."""
        await make_gateway()
        analyst = await make_user("analyst3@test.local", role="Analyst")

        token = await api_token(client, analyst)
        api = await client.post(
            "/api/routes", json={"name": "x", "path": "/x"}, headers=auth_headers(token)
        )
        assert api.status_code == 403
        assert api.json()["permission"] == "routes.write"

        await sign_in(client, analyst)
        page = await client.get("/configuration")
        assert page.status_code == 403

    async def test_a_disabled_user_loses_api_access_immediately(self, client):
        await make_gateway()
        user = await make_user("temp@test.local", role="Owner", superuser=True)
        token = await api_token(client, user)
        assert (await client.get("/api/routes", headers=auth_headers(token))).status_code == 200

        await user.disable(reason="test")
        assert (await client.get("/api/routes", headers=auth_headers(token))).status_code == 401
