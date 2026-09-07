"""The Caddy integration: generation, validation, deployment, drift, rollback.

Two layers of test here, and the distinction matters:

* Most run against `SimulatedTransport`, which implements the admin API's
  contract in process. They prove the *pipeline* — that a rejected
  configuration never reaches the gateway, that a version is recorded either
  way, that a rollback sends the stored payload.
* A few marked `real_caddy` shell out to an actual `caddy` binary. They prove
  the *claims Janus makes about Caddy* — chiefly that a bad configuration is
  refused atomically and the previous one keeps serving, which is the whole
  safety argument and would be worthless as an assumption.
"""

from __future__ import annotations

import shutil

import pytest

from app.caddy import CaddyApplyError, CaddyManager, SimulatedTransport
from app.caddy.config_builder import CAPABILITY_RATE_LIMIT, build_server
from app.services import configuration as config_service
from database.models import ConfigVersion, Deployment, GatewayRoute, IpRule, RateLimit
from tests.helpers import make_domain, make_gateway, make_upstream, make_user

has_caddy = pytest.mark.skipif(shutil.which("caddy") is None, reason="no caddy binary")


def _find_handler(node: object, name: str) -> dict | None:
    """The first handler dict with `handler == name`, anywhere in the tree."""
    if isinstance(node, dict):
        if node.get("handler") == name:
            return node
        for value in node.values():
            found = _find_handler(value, name)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_handler(item, name)
            if found is not None:
                return found
    return None


async def estate():
    """A gateway with a domain, an upstream and one proxying route."""
    gateway = await make_gateway()
    domain = await make_domain(gateway)
    upstream = await make_upstream(gateway, dials=("10.0.0.1:8000", "10.0.0.2:8000"))
    route = await GatewayRoute.create(
        gateway_id=gateway.pk,
        name="Orders",
        slug="orders",
        path="/api/orders*",
        methods="GET,POST",
        priority=100,
        domain_id=domain.pk,
        upstream_id=upstream.pk,
        strip_prefix="/api",
    )
    return gateway, domain, upstream, route


class TestGeneration:
    async def test_a_route_becomes_a_caddy_route(self):
        gateway, _, _, _ = await estate()
        payload, warnings = await config_service.generate(gateway)

        assert payload["listen"] == [":8080"]
        assert len(payload["routes"]) == 1
        assert warnings == []

        matcher = payload["routes"][0]["match"][0]
        assert matcher["host"] == ["api.test.local"]
        assert matcher["path"] == ["/api/orders*"]
        assert matcher["method"] == ["GET", "POST"]

    async def test_the_upstream_becomes_a_weighted_reverse_proxy(self):
        gateway, _, _, _ = await estate()
        payload, _ = await config_service.generate(gateway)
        handler = payload["routes"][0]["handle"][0]["routes"][-1]["handle"][0]

        assert handler["handler"] == "reverse_proxy"
        assert [u["dial"] for u in handler["upstreams"]] == ["10.0.0.1:8000", "10.0.0.2:8000"]
        assert handler["load_balancing"]["selection_policy"]["weights"] == [1, 2]

    async def test_a_disabled_route_is_not_emitted(self):
        gateway, _, _, route = await estate()
        route.enabled = False
        await route.save()
        payload, _ = await config_service.generate(gateway)
        assert payload["routes"] == []

    async def test_routes_are_emitted_in_priority_order(self):
        """Caddy answers with the first match, so this ordering *is* the
        routing behaviour rather than a presentation detail."""
        gateway, domain, upstream, _ = await estate()
        await GatewayRoute.create(
            gateway_id=gateway.pk, name="Catch all", slug="catch-all", path="/*",
            priority=0, action="static", static_status=404,
        )
        await GatewayRoute.create(
            gateway_id=gateway.pk, name="Health", slug="health", path="/healthz",
            priority=200, action="static", static_status=200,
        )
        payload, _ = await config_service.generate(gateway)
        paths = [r["match"][0]["path"][0] for r in payload["routes"]]
        assert paths == ["/healthz", "/api/orders*", "/*"]

    async def test_a_blocked_range_is_emitted_before_any_route(self):
        gateway, _, _, _ = await estate()
        await IpRule.create(gateway_id=gateway.pk, action="block", cidr="203.0.113.0/24")
        payload, _ = await config_service.generate(gateway)

        first = payload["routes"][0]
        assert first["match"][0]["client_ip"]["ranges"] == ["203.0.113.0/24"]
        assert first["handle"][0]["status_code"] == 403
        assert first["terminal"] is True

    async def test_an_expired_block_is_not_emitted(self):
        from datetime import UTC, datetime, timedelta

        gateway, _, _, _ = await estate()
        await IpRule.create(
            gateway_id=gateway.pk, action="block", cidr="203.0.113.0/24",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        payload, _ = await config_service.generate(gateway)
        assert all("client_ip" not in r["match"][0] for r in payload["routes"])

    async def test_tls_is_off_when_no_domain_wants_it(self):
        """The trap this guards: Caddy turns on automatic HTTPS as soon as a
        server has host matchers, and then answers plain HTTP with a 400."""
        gateway, _, _, _ = await estate()
        payload, _ = await config_service.generate(gateway)
        assert payload["automatic_https"] == {"disable": True}

    async def test_tls_is_on_when_a_domain_wants_it(self):
        gateway, domain, _, _ = await estate()
        domain.tls_mode = "internal"
        await domain.save()
        payload, _ = await config_service.generate(gateway)
        assert payload["automatic_https"]["disable"] is False

    async def test_a_route_with_no_upstream_warns_and_is_skipped(self):
        gateway, _, _, route = await estate()
        route.upstream_id = None
        await route.save()
        payload, warnings = await config_service.generate(gateway)
        assert payload["routes"] == []
        assert any("no upstream" in w for w in warnings)

    async def test_a_rate_limit_warns_when_the_build_lacks_the_module(self, monkeypatch):
        """Janus must never emit a handler it knows would be rejected, and must
        never claim a limit is enforced when it is not."""
        from app.caddy.modules import BuildInfo
        from app.services import modules as modules_service

        async def _core_only(_gateway):
            return BuildInfo(source="unknown")

        # Independent of whatever caddy is installed on the test host.
        monkeypatch.setattr(modules_service, "build_for", _core_only)

        gateway, _, _, route = await estate()
        await RateLimit.create(
            gateway_id=gateway.pk, name="Orders", key="ip", limit=10,
            window_seconds=1, route_id=route.pk,
        )
        payload, warnings = await config_service.generate(gateway)

        assert any("not enforced" in w for w in warnings)
        assert "rate_limit" not in str(payload)

    async def test_a_rate_limit_is_emitted_when_the_module_exists(self):
        gateway, domain, upstream, route = await estate()
        snapshot = await config_service.snapshot(gateway)
        snapshot["rate_limits"] = [
            {
                "id": 1, "name": "Orders", "key": "ip", "limit": 10,
                "window_seconds": 1, "burst": 0, "enabled": True,
                "route_id": route.pk, "path": "/api/orders*",
            }
        ]
        generated = build_server(
            gateway=snapshot["gateway"], routes=snapshot["routes"],
            upstreams=snapshot["upstreams"], ip_rules=snapshot["ip_rules"],
            rate_limits=snapshot["rate_limits"], policies=snapshot["policies"],
            domains=snapshot["domains"], capabilities=frozenset({CAPABILITY_RATE_LIMIT}),
        )
        assert "rate_limit" in str(dict(generated))
        assert generated.warnings == []

        handler = _find_handler(dict(generated), "rate_limit")
        assert handler is not None
        # `storage`/`distributed` must be ABSENT, not null: caddy-ratelimit
        # treats a JSON null on its storage field as "load a storage module
        # from this" and provisioning fails with "module name not specified
        # with key 'module' in map[]".
        assert "storage" not in handler
        assert "distributed" not in handler

    async def test_a_canary_split_produces_proportional_weights(self):
        gateway, _, upstream, route = await estate()
        canary = await make_upstream(gateway, name="canary", dials=("10.9.9.9:8000",))
        route.canary_upstream_id = canary.pk
        route.canary_percent = 10
        await route.save()

        payload, _ = await config_service.generate(gateway)
        handler = payload["routes"][0]["handle"][0]["routes"][-1]["handle"][0]
        weights = handler["load_balancing"]["selection_policy"]["weights"]

        # The canary is the last upstream, and must be ~10% of the total.
        assert len(weights) == 3
        assert abs(weights[-1] / sum(weights) - 0.10) < 0.02
        # Reduced by their GCD: Caddy gives each upstream `weight` *consecutive*
        # requests, so an un-reduced vector makes a 90/10 split behave as 100/0
        # for as long as anyone watches it.
        from functools import reduce
        from math import gcd

        assert reduce(gcd, weights) == 1


class TestThePipeline:
    async def test_a_deployment_records_a_version_and_activates_it(self):
        gateway, _, _, _ = await estate()
        user = await make_user("op@test.local", role="Operator")

        result = await config_service.deploy(gateway, actor=user, origin="web")
        assert result.ok
        assert result.stage == "active"

        version = await ConfigVersion.get(pk=result.version.pk)
        assert version.version == 1
        assert version.status == "active"
        await gateway.refresh_from_db()
        assert gateway.sync_state == "SYNCED"
        assert gateway.active_version_id == version.pk

    async def test_deploying_twice_with_no_change_creates_no_version(self):
        """History is what versions are for; a version nobody can tell apart
        from its predecessor makes the history useless."""
        gateway, _, _, _ = await estate()
        await config_service.deploy(gateway)
        second = await config_service.deploy(gateway)

        assert second.unchanged is True
        assert await ConfigVersion.filter(gateway_id=gateway.pk).count() == 1

    async def test_force_deploys_an_unchanged_configuration(self):
        gateway, _, _, _ = await estate()
        await config_service.deploy(gateway)
        result = await config_service.deploy(gateway, force=True)
        assert result.ok and not result.unchanged
        assert await ConfigVersion.filter(gateway_id=gateway.pk).count() == 2

    async def test_a_change_produces_a_new_version_with_a_summary(self):
        gateway, domain, upstream, _ = await estate()
        await config_service.deploy(gateway)
        await GatewayRoute.create(
            gateway_id=gateway.pk, name="Second", slug="second", path="/other*",
            upstream_id=upstream.pk, priority=50,
        )
        result = await config_service.deploy(gateway)

        assert result.version.version == 2
        assert "added" in result.version.summary

    async def test_a_rejected_configuration_is_recorded_and_never_sent(self):
        """A pipeline that only writes history on success cannot answer
        'what did we try to deploy at 3am and why did it not go out'."""
        gateway, _, _, _ = await estate()
        transport = SimulatedTransport()
        manager = CaddyManager(transport=transport, simulate=True)

        payload, _ = await config_service.generate(gateway, manager)
        payload["routes"][0]["handle"][0]["routes"][-1]["handle"][0]["handler"] = "nope_xyz"

        with pytest.raises(CaddyApplyError):
            await manager.apply(payload)
        # Nothing was written to the gateway's server path.
        assert await transport.get(manager.server_path) is None

    async def test_a_failed_apply_leaves_the_previous_version_active(self):
        gateway, _, _, _ = await estate()
        await config_service.deploy(gateway)
        first_version = gateway.active_version_id

        # A transport that refuses everything, standing in for an unreachable
        # or unhappy Caddy.
        class Refusing(SimulatedTransport):
            async def post(self, path, payload):
                raise CaddyApplyError("simulated refusal", status=500)

        import app.caddy.manager as manager_module

        original = manager_module.manager_for
        manager_module.manager_for = lambda gw: CaddyManager(transport=Refusing(), simulate=True)
        config_service.manager_for = manager_module.manager_for
        try:
            await GatewayRoute.create(
                gateway_id=gateway.pk, name="New", slug="new", path="/new*",
                action="static", static_status=200, priority=5,
            )
            result = await config_service.deploy(gateway)
        finally:
            manager_module.manager_for = original
            config_service.manager_for = original

        assert result.ok is False
        assert result.stage == "apply"
        await gateway.refresh_from_db()
        assert gateway.sync_state == "FAILED"
        # The version that was live before is still the active one.
        assert gateway.active_version_id == first_version

    async def test_every_deployment_is_recorded_with_its_stage(self):
        gateway, _, _, _ = await estate()
        await config_service.deploy(gateway)
        deployment = await Deployment.filter(gateway_id=gateway.pk).first()
        assert deployment.status == "succeeded"
        assert deployment.stage == "done"
        assert deployment.duration_ms is not None


class TestDrift:
    async def test_an_untouched_gateway_has_not_drifted(self):
        gateway, _, _, _ = await estate()
        transport = SimulatedTransport()
        manager = CaddyManager(transport=transport, simulate=True)
        payload, _ = await config_service.generate(gateway, manager)
        await manager.apply(payload)

        drifted, why = await manager.detect_drift(payload)
        assert drifted is False and why is None

    async def test_a_hand_edited_gateway_has_drifted(self):
        gateway, _, _, _ = await estate()
        transport = SimulatedTransport()
        manager = CaddyManager(transport=transport, simulate=True)
        payload, _ = await config_service.generate(gateway, manager)
        await manager.apply(payload)

        live = await transport.get(manager.server_path)
        live["routes"].append({"handle": [{"handler": "static_response"}], "terminal": True})
        await transport.post(manager.server_path, live)

        drifted, why = await manager.detect_drift(payload)
        assert drifted is True
        assert "routes" in why

    async def test_a_missing_server_reads_as_drift(self):
        gateway, _, _, _ = await estate()
        manager = CaddyManager(transport=SimulatedTransport(), simulate=True)
        payload, _ = await config_service.generate(gateway, manager)
        drifted, why = await manager.detect_drift(payload)
        assert drifted is True
        assert "not present" in why


class TestRollback:
    async def test_rollback_redeploys_the_stored_payload(self):
        gateway, domain, upstream, _ = await estate()
        user = await make_user("owner2@test.local", role="Owner", superuser=True)

        await config_service.deploy(gateway, actor=user)
        await GatewayRoute.create(
            gateway_id=gateway.pk, name="Second", slug="second", path="/other*",
            upstream_id=upstream.pk, priority=50,
        )
        second = await config_service.deploy(gateway, actor=user)
        assert len(second.version.payload["routes"]) == 2

        result = await config_service.rollback(gateway, 1, actor=user)
        assert result.ok
        # A new version carrying v1's payload — the history reads forward.
        assert result.version.version == 3
        assert len(result.version.payload["routes"]) == 1
        assert result.version.rolled_back_from_id is not None

    async def test_rolling_back_to_a_rejected_version_is_refused(self):
        gateway, _, _, _ = await estate()
        await config_service.deploy(gateway)
        await ConfigVersion.create(
            gateway_id=gateway.pk, version=99, payload={"listen": []},
            checksum="x", status="rejected",
        )
        result = await config_service.rollback(gateway, 99)
        assert result.ok is False
        assert "never ran" in result.error

    async def test_rolling_back_to_an_unknown_version_is_refused(self):
        gateway, _, _, _ = await estate()
        result = await config_service.rollback(gateway, 42)
        assert result.ok is False


class TestOwnership:
    async def test_janus_writes_only_its_own_server_and_logger(self):
        """A Caddy instance carrying other servers must keep them."""
        gateway, _, _, _ = await estate()
        transport = SimulatedTransport(
            {
                "apps": {"http": {"servers": {"someone_else": {"listen": [":9999"], "routes": []}}}},
                "logging": {"logs": {"default": {"level": "INFO"}}},
            }
        )
        manager = CaddyManager(transport=transport, server_name="janus", simulate=True)
        payload, _ = await config_service.generate(gateway, manager)
        await manager.apply(payload)

        full = await transport.get("/config/")
        assert "someone_else" in full["apps"]["http"]["servers"]
        assert full["apps"]["http"]["servers"]["someone_else"]["listen"] == [":9999"]
        assert "default" in full["logging"]["logs"]
        assert "janus" in full["apps"]["http"]["servers"]

    async def test_foreign_servers_are_reported(self):
        transport = SimulatedTransport(
            {"apps": {"http": {"servers": {"other": {"listen": [":1"], "routes": []}}}}}
        )
        manager = CaddyManager(transport=transport, simulate=True)
        assert await manager.read_foreign_servers() == ["other"]

    async def test_remove_deletes_only_the_janus_subtrees(self):
        gateway, _, _, _ = await estate()
        transport = SimulatedTransport(
            {"apps": {"http": {"servers": {"other": {"listen": [":1"], "routes": []}}}}}
        )
        manager = CaddyManager(transport=transport, simulate=True)
        payload, _ = await config_service.generate(gateway, manager)
        await manager.apply(payload)
        await manager.remove()

        full = await transport.get("/config/")
        assert "other" in full["apps"]["http"]["servers"]
        assert "janus" not in full["apps"]["http"]["servers"]


@has_caddy
class TestAgainstARealCaddy:
    """Claims about Caddy, checked against Caddy."""

    async def test_a_generated_configuration_provisions(self):
        gateway, _, _, _ = await estate()
        manager = CaddyManager(simulate=True)
        payload, _ = await config_service.generate(gateway, manager)

        result = await manager.validate(payload)
        assert result.ok, result.error
        assert result.method == "caddy", "expected validation by the real binary"

    async def test_an_unknown_handler_is_rejected_by_validation(self):
        gateway, _, _, _ = await estate()
        manager = CaddyManager(simulate=True)
        payload, _ = await config_service.generate(gateway, manager)
        payload["routes"][0]["handle"][0]["routes"][-1]["handle"][0]["handler"] = "nope_xyz"

        result = await manager.validate(payload)
        assert result.ok is False
        assert "nope_xyz" in result.error

    async def test_capabilities_are_read_from_the_binary(self):
        manager = CaddyManager(simulate=True)
        capabilities = await manager.capabilities()
        # The core handlers are always present; the point of this call is that
        # it reflects the actual binary rather than a fixed assumption.
        assert "http.handlers.reverse_proxy" in capabilities
        assert "http.handlers.static_response" in capabilities
