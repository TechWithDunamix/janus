"""Allowlist, blocklist, rate limits and API keys."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services import apikeys, security
from database.models import ApiKey, AuditEvent, Client, IpRule, RateLimit, normalise_cidr
from tests.helpers import api_token, auth_headers, make_gateway, make_user


class TestAddressNormalisation:
    def test_a_bare_address_becomes_a_host_network(self):
        """One column and one comparison then covers both an address and a
        range, so an allowlist holding each does not need two code paths."""
        assert normalise_cidr("203.0.113.42") == "203.0.113.42/32"
        assert normalise_cidr("10.0.0.0/8") == "10.0.0.0/8"

    def test_a_host_bit_inside_a_range_is_canonicalised(self):
        """Operators write `192.168.1.42/24` meaning that address's network,
        and rejecting it teaches them nothing useful."""
        assert normalise_cidr("192.168.1.42/24") == "192.168.1.0/24"

    def test_ipv6_is_handled(self):
        assert normalise_cidr("2001:db8::1") == "2001:db8::1/128"

    def test_nonsense_is_refused(self):
        with pytest.raises(ValueError):
            normalise_cidr("not-an-address")
        with pytest.raises(ValueError):
            normalise_cidr("")


class TestBlocking:
    async def test_blocking_creates_a_permanent_rule(self):
        gateway = await make_gateway()
        rule = await security.block(gateway, "203.0.113.42", reason="probing")
        assert rule.action == "block"
        assert rule.cidr == "203.0.113.42/32"
        assert rule.is_permanent is True
        assert rule.reason == "probing"

    async def test_a_temporary_block_carries_an_expiry(self):
        gateway = await make_gateway()
        rule = await security.block(gateway, "203.0.113.42", duration=timedelta(hours=1))
        assert rule.is_permanent is False
        assert rule.expires_at > datetime.now(UTC)

    async def test_blocking_twice_updates_rather_than_duplicates(self):
        gateway = await make_gateway()
        await security.block(gateway, "203.0.113.42", reason="first")
        await security.block(gateway, "203.0.113.42", reason="second")
        rules = await IpRule.filter(gateway_id=gateway.pk, action="block")
        assert len(rules) == 1
        assert rules[0].reason == "second"

    async def test_unblocking_removes_the_rule(self):
        gateway = await make_gateway()
        await security.block(gateway, "203.0.113.42")
        removed = await security.unblock(gateway, "203.0.113.42")
        assert removed == 1
        assert await IpRule.filter(gateway_id=gateway.pk, action="block").count() == 0

    async def test_blocking_is_audited(self):
        gateway = await make_gateway()
        user = await make_user(role="Operator")
        await security.block(gateway, "203.0.113.42", reason="scanning", actor=user)
        event = await AuditEvent.filter(action="client.blocked").first()
        assert event.actor_label == user.email
        assert event.after["cidr"] == "203.0.113.42/32"

    async def test_an_expired_rule_is_removed_by_the_sweeper(self):
        gateway = await make_gateway()
        await security.block(gateway, "203.0.113.42", duration=timedelta(seconds=-1))
        expired = await security.expire_due_rules(gateway)
        assert expired == 1
        assert await IpRule.filter(gateway_id=gateway.pk).count() == 0


class TestEffectiveStatus:
    async def test_an_address_inside_a_blocked_range_is_blocked(self):
        gateway = await make_gateway()
        await security.block(gateway, "203.0.113.0/24")
        assert await security.effective_status(gateway, "203.0.113.42") == "blocked"

    async def test_an_address_outside_every_rule_is_active(self):
        gateway = await make_gateway()
        await security.block(gateway, "203.0.113.0/24")
        assert await security.effective_status(gateway, "198.51.100.1") == "active"

    async def test_a_specific_allow_beats_a_broad_block(self):
        """The precedence the generator emits, checked here so the two agree."""
        gateway = await make_gateway()
        await security.block(gateway, "10.0.0.0/8")
        await security.allow(gateway, "10.1.2.3")
        assert await security.effective_status(gateway, "10.1.2.3") == "allowed"
        assert await security.effective_status(gateway, "10.9.9.9") == "blocked"

    async def test_an_expired_block_no_longer_applies(self):
        gateway = await make_gateway()
        await security.block(gateway, "203.0.113.42", duration=timedelta(seconds=-1))
        assert await security.effective_status(gateway, "203.0.113.42") == "active"


class TestRateLimits:
    async def test_the_rate_label_reads_the_way_operators_write_it(self):
        gateway = await make_gateway()
        per_second = await RateLimit.create(
            gateway_id=gateway.pk, name="a", key="ip", limit=10, window_seconds=1
        )
        per_minute = await RateLimit.create(
            gateway_id=gateway.pk, name="b", key="ip", limit=100, window_seconds=60
        )
        per_day = await RateLimit.create(
            gateway_id=gateway.pk, name="c", key="ip", limit=1_000_000, window_seconds=86400
        )
        assert per_second.rate_label == "10/sec"
        assert per_minute.rate_label == "100/min"
        assert per_day.rate_label == "1,000,000/day"

    async def test_creating_a_limit_through_the_api(self, client):
        await make_gateway()
        user = await make_user(role="Operator")
        token = await api_token(client, user)
        response = await client.post(
            "/api/security/rate-limits",
            json={"name": "Per key", "key": "api_key", "limit": 1000, "window_seconds": 60},
            headers=auth_headers(token),
        )
        assert response.status_code == 201
        assert response.json()["rate"] == "1,000/min"


class TestApiKeys:
    async def test_creation_returns_the_secret_exactly_once(self):
        user = await make_user(role="Owner", superuser=True)
        key, secret = await apikeys.create(name="Partner", actor=user)

        assert secret.startswith("jan_")
        assert len(secret) > 40
        # The row holds a digest and a prefix, and nothing that can be used.
        assert key.key_hash == ApiKey.digest(secret)
        assert secret not in key.prefix
        assert key.prefix in secret

    async def test_the_secret_is_not_recoverable(self):
        user = await make_user(role="Owner", superuser=True)
        key, secret = await apikeys.create(name="Partner", actor=user)
        reloaded = await ApiKey.get(pk=key.pk)
        # Nothing on the row, in any field, is the secret.
        assert secret not in str(reloaded.to_dict())

    async def test_verification_matches_the_secret(self):
        user = await make_user(role="Owner", superuser=True)
        _, secret = await apikeys.create(name="Partner", actor=user)
        found = await apikeys.verify(secret)
        assert found is not None
        assert found.name == "Partner"

    async def test_verification_refuses_a_revoked_key(self):
        user = await make_user(role="Owner", superuser=True)
        key, secret = await apikeys.create(name="Partner", actor=user)
        await apikeys.revoke(key, reason="leaked", actor=user)
        assert await apikeys.verify(secret) is None

    async def test_verification_refuses_an_expired_key(self):
        user = await make_user(role="Owner", superuser=True)
        key, secret = await apikeys.create(
            name="Partner", expires_in=timedelta(seconds=-1), actor=user
        )
        assert key.status == "expired"
        assert await apikeys.verify(secret) is None

    async def test_rotation_replaces_the_secret_and_keeps_the_history(self):
        """Two rows rather than an in-place update: analytics reference the key
        that served them, so overwriting the digest would silently
        re-attribute every historical request to the new secret."""
        user = await make_user(role="Owner", superuser=True)
        key, old_secret = await apikeys.create(name="Partner", actor=user)
        replacement, new_secret = await apikeys.rotate(key, actor=user)

        assert new_secret != old_secret
        assert replacement.pk != key.pk
        assert replacement.rotated_from_id == key.pk
        assert await apikeys.verify(old_secret) is None
        assert (await apikeys.verify(new_secret)).pk == replacement.pk

    async def test_the_api_never_lists_a_secret(self, client):
        user = await make_user(role="Owner", superuser=True)
        await apikeys.create(name="Partner", actor=user)
        token = await api_token(client, user)

        response = await client.get("/api/keys", headers=auth_headers(token))
        body = response.text
        assert response.status_code == 200
        assert "secret" not in body
        assert "key_hash" not in body

    async def test_the_audit_trail_never_holds_a_secret(self):
        user = await make_user(role="Owner", superuser=True)
        _, secret = await apikeys.create(name="Partner", actor=user)
        for event in await AuditEvent.all():
            assert secret not in str(event.before or {})
            assert secret not in str(event.after or {})


class TestClientRegistry:
    async def test_registering_a_client_is_idempotent(self):
        first = await security.register_client("ip", "203.0.113.42", label="Partner")
        second = await security.register_client("ip", "203.0.113.42")
        assert first.pk == second.pk
        assert await Client.all().count() == 1
