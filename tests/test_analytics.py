"""Ingestion, aggregation, percentiles and the problematic-route ranking."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.collector import ingest_lines, parse_line
from app.services import analytics
from database.models import (
    LATENCY_BUCKETS_MS,
    Client,
    GatewayRoute,
    RequestLog,
    RouteRollup,
    bucket_for,
)
from tests.helpers import make_domain, make_gateway, make_upstream


def log_line(
    *,
    path: str = "/api/orders",
    method: str = "GET",
    status: int = 200,
    duration: float = 0.05,
    host: str = "api.test.local",
    client_ip: str = "203.0.113.10",
    at: datetime | None = None,
    headers: dict | None = None,
) -> str:
    """One Caddy access-log line, in the shape Caddy actually writes."""
    moment = at or datetime.now(UTC)
    return json.dumps(
        {
            "level": "info",
            "ts": moment.timestamp(),
            "logger": "http.log.access.janus",
            "msg": "handled request",
            "request": {
                "remote_ip": client_ip,
                "client_ip": client_ip,
                "proto": "HTTP/2.0",
                "method": method,
                "host": host,
                "uri": path,
                "headers": headers or {"User-Agent": ["curl/8.7.1"]},
            },
            "bytes_read": 120,
            "duration": duration,
            "size": 2048,
            "status": status,
        }
    )


class TestParsing:
    def test_a_real_caddy_line_parses(self):
        record = parse_line(log_line(path="/api/orders?page=2", status=502, duration=1.25))
        assert record["method"] == "GET"
        assert record["host"] == "api.test.local"
        # The query string is dropped: a path with a cursor in it would make
        # every request its own route.
        assert record["path"] == "/api/orders"
        assert record["status"] == 502
        assert record["latency_ms"] == 1250.0
        assert record["bytes_out"] == 2048

    def test_a_non_request_line_is_skipped(self):
        """The same file carries Caddy's own informational lines when the
        logger is misconfigured; treating those as requests invents traffic."""
        assert parse_line('{"level":"info","msg":"serving initial configuration"}') is None
        assert parse_line("not json at all") is None
        assert parse_line("") is None

    def test_an_rfc3339_timestamp_is_handled(self):
        line = json.loads(log_line())
        line["ts"] = "2026-09-05T10:00:00Z"
        record = parse_line(json.dumps(line))
        assert record["occurred_at"].year == 2026

    def test_credentials_are_never_extracted(self):
        """Filtering happens at the boundary: a secret that reaches the
        database is already leaked."""
        record = parse_line(
            log_line(
                headers={
                    "Authorization": ["Bearer super-secret-token"],
                    "Cookie": ["session=abc123"],
                    "X-Api-Key": ["jan_live_abcdef1234567890"],
                }
            )
        )
        flat = json.dumps(record, default=str)
        assert "super-secret-token" not in flat
        assert "abc123" not in flat
        assert "abcdef1234567890" not in flat
        # Presence is recorded; the value is not.
        assert record["had_credential"] is True
        # Only the prefix, which is enough to attribute and useless to use —
        # and exactly as long as the prefix the issuer stores, or attribution
        # would never match.
        from database.models import ApiKey

        assert record["api_key_prefix"] == "jan_live_abcdef1234567890"[: ApiKey.PREFIX_LENGTH]


class TestIngestion:
    async def test_lines_become_request_rows_and_rollups(self):
        gateway = await make_gateway()
        domain = await make_domain(gateway)
        upstream = await make_upstream(gateway)
        route = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Orders", slug="orders", path="/api/orders*",
            domain_id=domain.pk, upstream_id=upstream.pk,
        )

        ingested, skipped = await ingest_lines(gateway, [log_line() for _ in range(5)])
        assert (ingested, skipped) == (5, 0)
        assert await RequestLog.filter(gateway_id=gateway.pk).count() == 5

        rollup = await RouteRollup.get(gateway_id=gateway.pk, route_id=route.pk)
        assert rollup.requests == 5
        assert rollup.status_2xx == 5

    async def test_requests_are_attributed_to_the_matching_route(self):
        gateway = await make_gateway()
        domain = await make_domain(gateway)
        upstream = await make_upstream(gateway)
        orders = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Orders", slug="orders", path="/api/orders*",
            domain_id=domain.pk, upstream_id=upstream.pk, priority=100,
        )
        await GatewayRoute.create(
            gateway_id=gateway.pk, name="Catch all", slug="catch-all", path="/*", priority=0,
        )

        await ingest_lines(gateway, [log_line(path="/api/orders/42")])
        row = await RequestLog.filter(gateway_id=gateway.pk).first()
        assert row.route_id == orders.pk

    async def test_unmatched_traffic_is_still_counted(self):
        """Traffic matching no route is where scanning shows up."""
        gateway = await make_gateway()
        await make_domain(gateway)
        await ingest_lines(gateway, [log_line(path="/.env", status=404)])

        row = await RequestLog.filter(gateway_id=gateway.pk).first()
        assert row.route_id is None
        rollup = await RouteRollup.get(gateway_id=gateway.pk, route_id=None)
        assert rollup.status_404 == 1

    async def test_clients_are_discovered_from_traffic(self):
        gateway = await make_gateway()
        await ingest_lines(gateway, [log_line(client_ip="198.51.100.7")])
        client = await Client.get(kind="ip", identifier="198.51.100.7")
        assert client.first_seen_at is not None

    async def test_ingesting_the_same_minute_twice_accumulates(self):
        gateway = await make_gateway()
        moment = datetime.now(UTC).replace(second=0, microsecond=0)
        await ingest_lines(gateway, [log_line(at=moment)])
        await ingest_lines(gateway, [log_line(at=moment)])
        rollup = await RouteRollup.get(gateway_id=gateway.pk, minute=moment)
        assert rollup.requests == 2

    async def test_a_malformed_line_does_not_stop_the_batch(self):
        gateway = await make_gateway()
        ingested, skipped = await ingest_lines(
            gateway, [log_line(), "{not json", "", log_line()]
        )
        assert ingested == 2
        assert skipped == 2

    async def test_a_429_is_recorded_as_rate_limited(self):
        gateway = await make_gateway()
        await ingest_lines(gateway, [log_line(status=429)])
        rollup = await RouteRollup.first()
        assert rollup.rate_limited == 1
        row = await RequestLog.first()
        assert row.edge_action == "rate_limited"


class TestPercentiles:
    def test_an_empty_histogram_is_zero(self):
        assert analytics.percentiles([]) == {"p50": 0.0, "p95": 0.0, "p99": 0.0}

    def test_a_single_bucket_reports_within_that_bucket(self):
        histogram = [0] * (len(LATENCY_BUCKETS_MS) + 1)
        histogram[bucket_for(30)] = 100  # the 25–50ms bucket
        result = analytics.percentiles(histogram)
        assert 25 <= result["p50"] <= 50
        assert 25 <= result["p95"] <= 50

    def test_percentiles_are_ordered(self):
        histogram = [10, 20, 30, 20, 10, 5, 3, 1, 1, 0, 0]
        result = analytics.percentiles(histogram)
        assert result["p50"] <= result["p95"] <= result["p99"]

    def test_the_overflow_bucket_reports_its_floor(self):
        """A request slower than the largest bound could be any amount slower,
        and pretending to know would be inventing precision."""
        histogram = [0] * len(LATENCY_BUCKETS_MS) + [100]
        result = analytics.percentiles(histogram)
        assert result["p99"] == float(LATENCY_BUCKETS_MS[-1])

    def test_histograms_compose_across_rows(self):
        """The reason rollups store a histogram rather than an average: a mean
        of means is not a mean, and a P95 of P95s is not a percentile."""
        left = [0] * (len(LATENCY_BUCKETS_MS) + 1)
        right = [0] * (len(LATENCY_BUCKETS_MS) + 1)
        left[bucket_for(10)] = 90
        right[bucket_for(2000)] = 10
        summed = [a + b for a, b in zip(left, right, strict=True)]

        result = analytics.percentiles(summed)
        assert result["p50"] <= 10
        assert result["p95"] >= 1000


class TestWindows:
    def test_each_named_range_resolves(self):
        for key in ("15m", "1h", "6h", "24h", "7d", "30d"):
            window = analytics.range_bounds(key)
            assert window.key == key
            assert window.until > window.since

    def test_an_unknown_range_falls_back_to_24h(self):
        assert analytics.range_bounds("nonsense").key == "24h"

    def test_a_custom_range_is_honoured(self):
        window = analytics.range_bounds(
            "custom", start="2026-09-01T00:00:00", end="2026-09-02T00:00:00"
        )
        assert window.key == "custom"
        assert (window.until - window.since) == timedelta(days=1)

    def test_a_malformed_custom_range_falls_back(self):
        """A bad query string in a shared URL should show a dashboard, not an
        error page."""
        window = analytics.range_bounds("custom", start="nonsense", end="also nonsense")
        assert window.key == "24h"


async def traffic(gateway, route, *, minutes: int, per_minute: int, status: int, latency_ms: float):
    """Write rollups directly — faster than ingesting, and the aggregation
    under test reads rollups anyway."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    rows = []
    for step in range(minutes):
        histogram = [0] * (len(LATENCY_BUCKETS_MS) + 1)
        histogram[bucket_for(latency_ms)] = per_minute
        rows.append(
            RouteRollup(
                gateway_id=gateway.pk,
                route_id=route.pk if route else None,
                minute=now - timedelta(minutes=step),
                requests=per_minute,
                status_2xx=per_minute if status < 400 else 0,
                status_4xx=per_minute if 400 <= status < 500 else 0,
                status_5xx=per_minute if status >= 500 else 0,
                latency_sum_ms=latency_ms * per_minute,
                latency_max_ms=latency_ms,
                latency_buckets=histogram,
                bytes_out=per_minute * 1000,
            )
        )
    await RouteRollup.bulk_create(rows)


class TestAggregation:
    async def test_the_overview_totals_the_range(self):
        gateway = await make_gateway()
        route = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Orders", slug="orders", path="/a*"
        )
        await traffic(gateway, route, minutes=10, per_minute=100, status=200, latency_ms=30)

        window = analytics.range_bounds("1h")
        summary = await analytics.overview(gateway, window)
        assert summary["requests"] == 1000
        assert summary["successful"] == 1000
        assert summary["error_rate"] == 0.0
        assert 25 <= summary["p95"] <= 50

    async def test_the_error_rate_is_computed_over_the_whole_range(self):
        gateway = await make_gateway()
        good = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Good", slug="good", path="/good*"
        )
        bad = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Bad", slug="bad", path="/bad*"
        )
        await traffic(gateway, good, minutes=10, per_minute=90, status=200, latency_ms=20)
        await traffic(gateway, bad, minutes=10, per_minute=10, status=500, latency_ms=20)

        summary = await analytics.overview(gateway, analytics.range_bounds("1h"))
        assert summary["requests"] == 1000
        assert summary["errors_5xx"] == 100
        assert abs(summary["error_rate"] - 0.10) < 0.001

    async def test_a_series_emits_empty_buckets_as_zero(self):
        """A line chart that skips empty buckets draws a straight line across
        an outage, which is the most misleading thing a traffic chart can do."""
        gateway = await make_gateway()
        route = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Orders", slug="orders", path="/a*"
        )
        await traffic(gateway, route, minutes=3, per_minute=10, status=200, latency_ms=20)

        window = analytics.range_bounds("1h")
        points = await analytics.series(gateway, window)
        assert len(points) > 50
        assert any(point["requests"] == 0 for point in points)
        assert sum(point["requests"] for point in points) == 30

    async def test_the_route_table_ranks_by_volume(self):
        gateway = await make_gateway()
        big = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Big", slug="big", path="/big*"
        )
        small = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Small", slug="small", path="/small*"
        )
        await traffic(gateway, big, minutes=5, per_minute=100, status=200, latency_ms=20)
        await traffic(gateway, small, minutes=5, per_minute=5, status=200, latency_ms=20)

        table = await analytics.route_table(gateway, analytics.range_bounds("1h"))
        assert table[0]["name"] == "Big"
        assert table[0]["requests"] == 500


class TestProblematicRoutes:
    async def test_a_failing_high_volume_route_ranks_first(self):
        gateway = await make_gateway()
        failing = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Failing", slug="failing", path="/f*"
        )
        healthy = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Healthy", slug="healthy", path="/h*"
        )
        await traffic(gateway, failing, minutes=10, per_minute=100, status=500, latency_ms=1500)
        await traffic(gateway, healthy, minutes=10, per_minute=100, status=200, latency_ms=20)

        ranked = await analytics.problematic_routes(gateway, analytics.range_bounds("1h"))
        assert ranked[0]["name"] == "Failing"
        assert ranked[0]["status"] == "CRITICAL"

    async def test_a_tiny_route_at_100_percent_errors_does_not_dominate(self):
        """A route at 100% errors and eleven requests is not the estate's
        biggest problem; one at 3% errors and four million usually is."""
        gateway = await make_gateway()
        tiny = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Tiny", slug="tiny", path="/t*"
        )
        busy = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Busy", slug="busy", path="/b*"
        )
        await traffic(gateway, tiny, minutes=10, per_minute=3, status=500, latency_ms=20)
        await traffic(gateway, busy, minutes=10, per_minute=1000, status=500, latency_ms=20)

        ranked = await analytics.problematic_routes(gateway, analytics.range_bounds("1h"))
        assert ranked[0]["name"] == "Busy"

    async def test_a_route_below_the_traffic_floor_is_not_ranked(self):
        gateway = await make_gateway()
        route = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Quiet", slug="quiet", path="/q*"
        )
        await traffic(gateway, route, minutes=2, per_minute=3, status=500, latency_ms=20)
        ranked = await analytics.problematic_routes(gateway, analytics.range_bounds("1h"))
        assert ranked == []

    async def test_the_components_explain_the_score(self):
        """A ranking that says 'this is worst' without saying why is one an
        operator has to take on faith."""
        gateway = await make_gateway()
        route = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Slow", slug="slow", path="/s*"
        )
        await traffic(gateway, route, minutes=10, per_minute=100, status=200, latency_ms=3000)

        ranked = await analytics.problematic_routes(gateway, analytics.range_bounds("1h"))
        assert ranked[0]["components"]["latency"] == 1.0
        assert ranked[0]["components"]["server_errors"] == 0.0

    async def test_slow_routes_rank_by_p95(self):
        gateway = await make_gateway()
        slow = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Slow", slug="slow", path="/s*"
        )
        fast = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Fast", slug="fast", path="/f*"
        )
        await traffic(gateway, slow, minutes=5, per_minute=50, status=200, latency_ms=2000)
        await traffic(gateway, fast, minutes=5, per_minute=50, status=200, latency_ms=10)

        ranked = await analytics.slow_routes(gateway, analytics.range_bounds("1h"))
        assert ranked[0]["name"] == "Slow"


class TestSimulatedDataIsLabelled:
    async def test_a_simulated_rollup_marks_the_summary(self):
        """An observability platform showing invented traffic as observation
        would be worse than one showing nothing."""
        gateway = await make_gateway()
        route = await GatewayRoute.create(
            gateway_id=gateway.pk, name="Demo", slug="demo", path="/d*"
        )
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        await RouteRollup.create(
            gateway_id=gateway.pk, route_id=route.pk, minute=now,
            requests=10, status_2xx=10, latency_buckets=[10] + [0] * len(LATENCY_BUCKETS_MS),
            simulated=True,
        )
        summary = await analytics.overview(gateway, analytics.range_bounds("1h"))
        assert summary["simulated"] is True


class TestApiKeyAttribution:
    async def test_a_request_carrying_a_key_is_attributed_to_it(self):
        """The prefix the collector extracts and the prefix the issuer stores
        have to be the same length, or every lookup misses in silence."""
        from app.services import apikeys
        from tests.helpers import make_user

        gateway = await make_gateway()
        user = await make_user(role="Owner", superuser=True)
        key, secret = await apikeys.create(name="Partner", actor=user)

        await ingest_lines(gateway, [log_line(headers={"X-Api-Key": [secret]})])
        row = await RequestLog.filter(gateway_id=gateway.pk).first()
        assert row.api_key_id == key.pk


class TestEachGatewayHasItsOwnLog:
    async def test_two_gateways_do_not_share_a_log_path(self):
        """Sharing one file means every request is ingested twice, once under
        each gateway — a gateway reporting traffic it never served, with
        nothing failing to say so."""
        first = await make_gateway("Edge", slug="edge")
        second = await make_gateway("Internal", slug="internal")
        assert first.access_log_path != second.access_log_path
        assert "edge" in first.access_log_path
        assert "internal" in second.access_log_path

    async def test_an_explicit_path_wins(self):
        gateway = await make_gateway("Edge", slug="edge")
        gateway.access_log = "/var/log/caddy/custom.log"
        await gateway.save()
        assert gateway.access_log_path == "/var/log/caddy/custom.log"
