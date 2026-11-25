"""The operator API the terminal uses: requests, domains, policies, upstream edits, maintenance, clients —
and the TUI and the table renderer on top of them."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from textual.widgets import DataTable, Static, TabbedContent

from cli.base import cell, render_table
from cli.tui import Confirm, Details, JanusTui, parse_filter
from database.models import Client, Gateway, GatewayRoute, RateLimit, RequestLog
from tests.helpers import api_token, auth_headers, make_domain, make_upstream


async def operator(client, owner) -> dict[str, str]:
    return auth_headers(await api_token(client, owner))


@pytest.mark.asyncio
async def test_requests_filter_by_status_class_path_and_follow(client, owner, gateway):
    headers = await operator(client, owner)
    now = datetime.now(UTC)
    for path, status in (("/api/orders", 200), ("/api/orders/9", 404), ("/admin", 403), ("/api/pay", 502)):
        await RequestLog.create(gateway_id=gateway.pk, occurred_at=now, method="GET", host="api.test.local",
                                path=path, status=status, client_ip="203.0.113.7")
    every = (await client.get("/api/requests", headers=headers)).json()["requests"]
    assert [r["path"] for r in every] == ["/api/pay", "/admin", "/api/orders/9", "/api/orders"]
    errors = (await client.get("/api/requests?status=4xx", headers=headers)).json()["requests"]
    assert sorted(r["status"] for r in errors) == [403, 404]
    orders = (await client.get("/api/requests?contains=orders&method=get", headers=headers)).json()["requests"]
    assert len(orders) == 2
    newer = (await client.get(f"/api/requests?since_id={every[1]['id']}", headers=headers)).json()["requests"]
    assert [r["path"] for r in newer] == ["/api/pay"]
    bad = await client.get("/api/requests?status=teapot", headers=headers)
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_domains_create_update_and_refuse_delete_while_used(client, owner, gateway):
    headers = await operator(client, owner)
    made = await client.post("/api/domains", headers=headers, json={"hostname": "API.example.com", "tls_mode": "off"})
    assert made.status_code == 201 and made.json()["hostname"] == "api.example.com"
    again = await client.post("/api/domains", headers=headers, json={"hostname": "api.example.com"})
    assert again.status_code == 409
    wrong = await client.post("/api/domains", headers=headers, json={"hostname": "x.example.com", "tls_mode": "maybe"})
    assert wrong.status_code == 400
    domain_id = made.json()["id"]
    off = await client.patch(f"/api/domains/{domain_id}", headers=headers, json={"enabled": False})
    assert off.json()["enabled"] is False
    upstream = await make_upstream(gateway)
    await GatewayRoute.create(gateway_id=gateway.pk, name="orders", slug="orders", path="/orders*",
                              upstream_id=upstream.pk, domain_id=domain_id)
    assert (await client.delete(f"/api/domains/{domain_id}", headers=headers)).status_code == 409
    listed = (await client.get("/api/domains", headers=headers)).json()["domains"]
    assert listed[0]["routes"] == 1


@pytest.mark.asyncio
async def test_policies_validate_and_change(client, owner, gateway):
    headers = await operator(client, owner)
    created = await client.post("/api/policies", headers=headers, json={
        "name": "office", "subject": "ip", "operator": "in_cidr", "value": "10.0.0.0/8", "action": "allow",
        "priority": 5})
    assert created.status_code == 201, created.text
    policy = created.json()
    assert policy["sentence"] == "IF ip in_cidr 10.0.0.0/8 THEN allow" and policy["scope"] == "gateway"
    assert (await client.post("/api/policies", headers=headers,
                              json={"name": "x", "value": "1.2.3.4", "action": "explode"})).status_code == 400
    limited = await client.post("/api/policies", headers=headers,
                                json={"name": "slow", "subject": "path", "operator": "matches", "value": "/api*",
                                      "action": "rate_limit"})
    assert limited.status_code == 400 and "rate_limit" in limited.json()["error"]
    await RateLimit.create(gateway_id=gateway.pk, name="api-60", limit=60, window_seconds=60)
    limited = await client.post("/api/policies", headers=headers,
                                json={"name": "slow", "subject": "path", "operator": "matches", "value": "/api*",
                                      "action": "rate_limit", "rate_limit": "api-60"})
    assert limited.status_code == 201 and limited.json()["rate_limit"] == "api-60"
    changed = await client.patch(f"/api/policies/{policy['id']}", headers=headers, json={"enabled": False})
    assert changed.json()["enabled"] is False and changed.json()["value"] == "10.0.0.0/8"
    names = [p["name"] for p in (await client.get("/api/policies", headers=headers)).json()["policies"]]
    assert names == ["office", "slow"]
    assert (await client.delete(f"/api/policies/{policy['id']}", headers=headers)).json()["deleted"] == "office"


@pytest.mark.asyncio
async def test_upstream_update_replaces_targets(client, owner, gateway):
    headers = await operator(client, owner)
    upstream = await make_upstream(gateway, dials=("10.0.0.1:8000", "10.0.0.2:8000"))
    changed = await client.patch(f"/api/upstreams/{upstream.pk}", headers=headers, json={
        "targets": ["10.0.0.9:8000", {"dial": "10.0.0.8:8000", "weight": 3}], "maintenance": True})
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["targets"] == [{"dial": "10.0.0.9:8000", "weight": 1}, {"dial": "10.0.0.8:8000", "weight": 3}]
    assert body["maintenance"] is True
    empty = await client.patch(f"/api/upstreams/{upstream.pk}", headers=headers, json={"targets": []})
    assert empty.status_code == 400


@pytest.mark.asyncio
async def test_maintenance_deploys_and_needs_a_state(client, owner, gateway):
    headers = await operator(client, owner)
    await make_domain(gateway)
    assert (await client.post("/api/gateways/maintenance", headers=headers, json={})).status_code == 400
    on = await client.post("/api/gateways/maintenance", headers=headers, json={"on": True, "body": "Back soon"})
    assert on.status_code == 200, on.text
    assert on.json()["maintenance"] is True and on.json()["deploy"]["ok"] is True
    assert (await Gateway.get(pk=gateway.pk)).maintenance is True


@pytest.mark.asyncio
async def test_security_clients_and_overview(client, owner, gateway):
    headers = await operator(client, owner)
    await Client.create(kind="ip", identifier="203.0.113.7", request_count=40, status="active")
    await Client.create(kind="ip", identifier="198.51.100.2", request_count=4, status="blocked")
    listed = (await client.get("/api/security/clients", headers=headers)).json()["clients"]
    assert [c["identifier"] for c in listed] == ["203.0.113.7", "198.51.100.2"]
    blocked = (await client.get("/api/security/clients?status=blocked", headers=headers)).json()["clients"]
    assert [c["identifier"] for c in blocked] == ["198.51.100.2"]
    one = (await client.get(f"/api/security/clients/{listed[0]['id']}", headers=headers)).json()
    assert one["client"]["effective_status"] and one["recent"] == []
    overview = (await client.get("/api/security/overview", headers=headers)).json()
    assert "summary" in overview and overview["top_blocked"] == []


@pytest.mark.asyncio
async def test_viewers_cannot_change_policies(client, gateway):
    from tests.helpers import make_user

    viewer = await make_user("viewer@test.local", role="Viewer")
    headers = await operator(client, viewer)
    assert (await client.get("/api/requests", headers=headers)).status_code in (200, 403)
    denied = await client.post("/api/policies", headers=headers, json={"name": "x", "value": "1.2.3.4"})
    assert denied.status_code == 403
    assert (await client.post("/api/gateways/maintenance", headers=headers, json={"on": True})).status_code == 403


def test_tables_and_filters():
    narrow = render_table(["ID", "PATH", "STATUS"], [["1", "/api/" + "x" * 80, "200"]], width=50)
    assert all(len(line) <= 50 for line in narrow) and narrow[2].rstrip().endswith("200")
    assert render_table(["A"], [["y" * 300]], width=None)[2] == "y" * 300
    assert cell("2026-11-25T20:58:13.123+00:00") == "2026-11-25 20:58:13"
    assert parse_filter("status:5xx /api client:1.2.3.4", ("status", "client"), "contains") == {
        "status": "5xx", "client": "1.2.3.4", "contains": "/api"}


class FakeApi:
    """Janus's API as the TUI sees it, without a server."""

    url = "http://janus.test"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    def get(self, path: str, /, **_: object) -> dict:
        route = {"id": 1, "name": "orders", "method": "ANY", "path": "/orders*", "enabled": True, "priority": 0}
        return {
            "/api/gateways": {"gateways": [{"id": 1, "name": "Edge", "slug": "edge", "maintenance": False}]},
            "/api/overview": {
                "gateway": {"id": 1, "name": "Edge", "slug": "edge", "maintenance": False},
                "range": {"label": "Last 24 hours"}, "health": {"reachable": True, "sync_state": "SYNCED"},
                "traffic": {"requests": 120, "error_rate": 0.05, "rps": 0.1}, "alerts": [], "deployments": []},
            "/api/routes": {"routes": [route]},
            "/api/requests": {"requests": [{"id": 9, "at": "2026-11-25T20:58:13Z", "method": "GET", "status": 502,
                                            "latency_ms": 3.1, "host": "api", "path": "/pay", "client_ip": "1.2.3.4"}]},
        }.get(path, {})

    def post(self, path: str, payload: object = None, /, **_: object) -> dict:
        self.calls.append(("post", path, payload))
        return {"ok": True, "version": 2}

    def patch(self, path: str, payload: object = None, /, **_: object) -> dict:
        self.calls.append(("patch", path, payload))
        return {}

    def delete(self, path: str, /, **_: object) -> dict:
        self.calls.append(("delete", path, None))
        return {}


@pytest.mark.asyncio
async def test_tui_overview_disables_a_route_and_deploys():
    api = FakeApi()
    app = JanusTui(api)  # type: ignore[arg-type]
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause(0.6)
        assert "120 requests" in str(app.query_one("#overview-body", Static).render())
        app.query_one(TabbedContent).active = "requests"
        await pilot.pause(0.6)
        requests = app.views["requests-view"].query_one(DataTable)
        assert requests.row_count == 1
        requests.focus()
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert isinstance(app.screen, Details)
        await pilot.press("escape")
        app.query_one(TabbedContent).active = "routes"
        await pilot.pause(0.6)
        app.views["routes-view"].query_one(DataTable).focus()
        await pilot.press("t")
        await pilot.pause(0.2)
        assert isinstance(app.screen, Confirm)
        await pilot.press("y")
        await pilot.pause(0.4)
        assert ("post", "/api/routes/1/toggle", {"enabled": False}) in api.calls
        await pilot.press("D")
        await pilot.pause(0.2)
        await pilot.press("y")
        await pilot.pause(0.4)
        assert any(call[1] == "/api/config/apply" for call in api.calls)
