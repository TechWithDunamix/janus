"""Caddy module (plugin) management.

The behaviour worth pinning down is not the catalogue — it is what Janus does
with an *incomplete* picture: a build it cannot introspect, a declaration that
contradicts the local binary, and a rebuild command that must not silently drop
what is already installed.
"""

from __future__ import annotations

import shutil

import pytest

from app.caddy.modules import (
    CATALOG,
    BuildInfo,
    build_command,
    catalog_status,
    detect,
    entry_for_module,
    script_command,
    unsupported_modules,
)
from app.services import configuration as config_service
from app.services.modules import build_for, feature_gaps, record_declared
from database.models import GatewayRoute, RateLimit
from tests.helpers import (
    api_token,
    auth_headers,
    make_domain,
    make_gateway,
    make_upstream,
    make_user,
)

has_caddy = pytest.mark.skipif(shutil.which("caddy") is None, reason="no caddy binary")


class TestTheCatalogue:
    def test_every_entry_has_a_package_and_a_module(self):
        for entry in CATALOG:
            assert entry.package.startswith("github.com/"), entry.key
            assert entry.provides, entry.key

    def test_keys_are_unique(self):
        keys = [entry.key for entry in CATALOG]
        assert len(keys) == len(set(keys))

    def test_no_two_plugins_claim_the_same_module(self):
        """Otherwise `entry_for_module` is ambiguous and the UI would attribute
        a capability to the wrong plugin."""
        seen: dict[str, str] = {}
        for entry in CATALOG:
            for module in entry.provides:
                assert module not in seen, f"{module} claimed by {seen.get(module)} and {entry.key}"
                seen[module] = entry.key

    def test_lookup_by_module(self):
        assert entry_for_module("http.handlers.rate_limit").key == "ratelimit"
        assert entry_for_module("http.handlers.reverse_proxy") is None


class TestDetection:
    @has_caddy
    async def test_a_real_binary_is_read(self):
        build = await detect()
        assert build.source == "binary"
        assert build.version.startswith("v")
        assert "http.handlers.reverse_proxy" in build.modules
        # Detection reads the real module list rather than guessing: the core
        # handlers are always present, and the standard count is populated.
        assert {"http.handlers.static_response", "http.handlers.rewrite"} <= build.modules
        assert build.standard_count > 0

    async def test_a_missing_binary_is_not_an_error(self):
        """Not having Caddy on the control plane's host is a normal
        deployment, not a failure."""
        build = await detect(binary="/nonexistent/caddy")
        assert build.source == "unknown"
        assert build.known is False

    async def test_an_unknown_build_reads_as_core_only(self):
        build = BuildInfo(source="unknown")
        rows = catalog_status(build)
        assert all(row["installed"] is False for row in rows)


class TestPerGatewayResolution:
    async def test_a_declaration_wins_over_local_detection(self):
        """The operator who built the remote binary knows more about it than
        this machine's caddy does."""
        gateway = await make_gateway()
        await record_declared(gateway, ["http.handlers.rate_limit"])

        build = await build_for(gateway)
        assert build.source == "declared"
        assert "http.handlers.rate_limit" in build.modules

    async def test_clearing_a_declaration_returns_to_detection(self):
        gateway = await make_gateway()
        await record_declared(gateway, ["http.handlers.rate_limit"])
        await record_declared(gateway, None)

        build = await build_for(gateway)
        assert build.source != "declared"

    async def test_gaps_name_the_plugin_that_would_close_them(self):
        build = BuildInfo(modules=frozenset({"http.handlers.reverse_proxy"}), source="binary")
        gaps = {gap["feature"]: gap for gap in feature_gaps(build)}
        assert "Rate limit enforcement" in gaps
        assert gaps["Rate limit enforcement"]["package"] == "github.com/mholt/caddy-ratelimit"

    async def test_a_complete_build_has_no_gaps(self):
        every = frozenset(m for entry in CATALOG for m in entry.provides)
        assert feature_gaps(BuildInfo(modules=every, source="binary")) == []


class TestTheBuildCommand:
    def test_it_includes_what_is_already_installed(self):
        """The mistake this exists to prevent: xcaddy builds from exactly the
        packages named, so a command listing only the new plugin produces a
        binary missing every plugin the current one has."""
        build = BuildInfo(
            modules=frozenset({"http.handlers.cache"}), version="v2.11.4", source="binary"
        )
        command = build_command(build, adding=["ratelimit"])

        assert "github.com/caddyserver/cache-handler" in command
        assert "github.com/mholt/caddy-ratelimit" in command

    def test_it_pins_the_caddy_version(self):
        build = BuildInfo(version="v2.11.4", source="binary")
        assert "xcaddy build v2.11.4" in build_command(build, adding=["jwt"])

    def test_adding_nothing_to_an_empty_build_is_just_the_command(self):
        command = build_command(BuildInfo(source="unknown"), adding=[])
        assert command.strip() == "xcaddy build"


class TestTheScriptCommand:
    def test_it_names_the_same_packages_as_the_xcaddy_command(self):
        build = BuildInfo(
            modules=frozenset({"http.handlers.cache"}), version="v2.11.4", source="binary"
        )
        script = script_command(build, adding=["ratelimit"])

        assert script.startswith("./scripts/setup-caddy.sh")
        assert "--with github.com/caddyserver/cache-handler" in script
        assert "--with github.com/mholt/caddy-ratelimit" in script
        assert "--version v2.11.4" in script
        assert "--restart --redeploy" in script

    def test_with_nothing_to_build_it_offers_rollback(self):
        assert script_command(BuildInfo(source="unknown"), adding=[]) == (
            "./scripts/setup-caddy.sh --rollback"
        )

    def test_removing_a_plugin_drops_it_from_both_commands(self):
        build = BuildInfo(
            modules=frozenset({"http.handlers.rate_limit", "http.handlers.cache"}),
            version="v2.11.4",
            source="binary",
        )
        script = script_command(build, adding=[], removing=["ratelimit"])
        xcaddy = build_command(build, adding=[], removing=["ratelimit"])

        for command in (script, xcaddy):
            assert "caddy-ratelimit" not in command
            assert "cache-handler" in command

    def test_removing_the_last_plugin_builds_a_plain_caddy(self):
        build = BuildInfo(
            modules=frozenset({"http.handlers.rate_limit"}),
            version="v2.11.4",
            source="binary",
        )
        script = script_command(build, adding=[], removing=["ratelimit"])

        assert "--plain" in script
        assert "--with" not in script
        assert "--version v2.11.4" in script
        assert build_command(build, adding=[], removing=["ratelimit"]).strip() == (
            "xcaddy build v2.11.4"
        )

    def test_unrecognised_modules_are_reported(self):
        """So an operator knows the command is incomplete for their binary,
        rather than finding out after the rebuild."""
        build = BuildInfo(
            modules=frozenset({"http.handlers.something_bespoke"}),
            non_standard=("http.handlers.something_bespoke",),
            source="binary",
        )
        assert unsupported_modules(build) == ["http.handlers.something_bespoke"]

    def test_a_known_plugin_is_not_reported_as_unrecognised(self):
        build = BuildInfo(
            modules=frozenset({"http.handlers.rate_limit"}),
            non_standard=("http.handlers.rate_limit",),
            source="binary",
        )
        assert unsupported_modules(build) == []


class TestItChangesWhatJanusEmits:
    async def estate(self):
        gateway = await make_gateway()
        domain = await make_domain(gateway)
        upstream = await make_upstream(gateway)
        route = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Orders", slug="orders", path="/api/orders*",
            domain_id=domain.pk, upstream_id=upstream.pk,
        )
        await RateLimit.create(
            gateway_id=gateway.pk, name="Orders", key="ip", limit=10,
            window_seconds=1, route_id=route.pk,
        )
        return gateway

    async def test_without_the_module_the_handler_is_not_emitted(self, monkeypatch):
        # Force a build with no non-core modules, so the test does not depend
        # on whatever caddy is installed on the machine running it.
        from app.services import modules as modules_service

        async def _core_only(_gateway):
            return BuildInfo(source="unknown")

        monkeypatch.setattr(modules_service, "build_for", _core_only)

        gateway = await self.estate()
        payload, warnings = await config_service.generate(gateway)
        assert "rate_limit" not in str(payload)
        assert any("not enforced" in w for w in warnings)

    async def test_declaring_the_module_emits_the_handler(self):
        """The payoff: recording what a remote build has changes what Janus
        generates for it."""
        gateway = await self.estate()
        await record_declared(gateway, ["http.handlers.rate_limit"])

        payload, warnings = await config_service.generate(gateway)
        assert "rate_limit" in str(payload)
        assert not any("not enforced" in w for w in warnings)

    async def test_a_declared_build_is_not_validated_by_the_local_binary(self):
        """The operator has said the remote build differs from this host's, so
        this binary would reject handlers the real gateway supports."""
        from app.caddy.manager import manager_for

        gateway = await self.estate()
        await record_declared(gateway, ["http.handlers.rate_limit"])
        payload, _ = await config_service.generate(gateway)

        result = await manager_for(gateway).validate(payload, use_binary=False)
        assert result.ok
        assert result.method == "structural"
        assert any("declared" in w for w in result.warnings)


class TestTheApi:
    async def test_the_overview_reports_source_and_gaps(self, client):
        await make_gateway()
        user = await make_user(role="Operator")
        token = await api_token(client, user)

        response = await client.get("/api/modules", headers=auth_headers(token))
        assert response.status_code == 200
        body = response.json()
        assert body["source"] in {"binary", "declared", "unknown"}
        assert body["requires_rebuild"] is True
        assert isinstance(body["build_command"], str)
        assert body["script_command"].startswith("./scripts/setup-caddy.sh")

    async def test_the_overview_takes_add_and_remove(self, client):
        await make_gateway()
        token = await api_token(client, await make_user(role="Operator"))

        response = await client.get(
            "/api/modules?add=jwt&remove=ratelimit", headers=auth_headers(token)
        )
        assert response.status_code == 200
        body = response.json()
        assert "caddy-jwt" in body["script_command"]
        assert "caddy-ratelimit" not in body["script_command"]

    async def test_declaring_requires_write_permission(self, client):
        await make_gateway()
        analyst = await make_user("analyst@test.local", role="Analyst")
        token = await api_token(client, analyst)

        response = await client.post(
            "/api/modules/declare",
            json={"modules": ["http.handlers.rate_limit"]},
            headers=auth_headers(token),
        )
        assert response.status_code == 403

    async def test_declaring_through_the_api(self, client):
        gateway = await make_gateway()
        user = await make_user(role="Operator")
        token = await api_token(client, user)

        response = await client.post(
            "/api/modules/declare",
            json={"modules": ["http.handlers.rate_limit"]},
            headers=auth_headers(token),
        )
        assert response.status_code == 200
        await gateway.refresh_from_db()
        assert gateway.declared_modules == ["http.handlers.rate_limit"]

    async def test_the_overview_is_readable_by_an_analyst(self, client):
        await make_gateway()
        analyst = await make_user("analyst2@test.local", role="Analyst")
        token = await api_token(client, analyst)
        # Analyst holds gateway.read.
        assert (await client.get("/api/modules", headers=auth_headers(token))).status_code == 200
