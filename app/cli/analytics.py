"""janus analytics — the CLI view of the observability platform."""

from __future__ import annotations

from sillo.console import Option

from app.cli.base import ApiCommand
from app.cli.client import ApiClient

__all__ = [
    "AnalyticsClients", "AnalyticsErrors", "AnalyticsOverview", "AnalyticsPerformance",
    "AnalyticsProblematic", "AnalyticsRoutes", "AnalyticsTraffic",
]

RANGE_OPTION = Option(
    "range", default="24h", choices=["15m", "1h", "6h", "24h", "7d", "30d"],
    help="Time range.",
)
GATEWAY_OPTION = Option("gateway", help="Gateway slug or id.")


def _rate(value: float) -> str:
    return f"{value * 100:.2f}%"


def _ms(value: float) -> str:
    return f"{value / 1000:.2f}s" if value >= 1000 else f"{value:.0f}ms"


def _bytes(value: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024:
            return f"{value:,.1f} {unit}"
        value /= 1024.0
    return f"{value:,.1f} PB"


class _Base(ApiCommand):
    arguments = [RANGE_OPTION, GATEWAY_OPTION]

    def fetch(self, api: ApiClient, path: str):
        return api.get(path, range=self.option("range"), gateway=self.option("gateway"))


class AnalyticsOverview(_Base):
    name = "analytics overview"
    help = "Headline traffic, error and latency figures."

    def run(self, api: ApiClient) -> int:
        result = self.fetch(api, "/api/analytics/overview")
        if self.wants_json:
            self.emit(result)
            return 0

        s = result["summary"]
        self.rule(result["range"]["label"])
        if s.get("simulated"):
            self.warn("This range contains simulated demo data, not real gateway traffic.")
        self.pairs(
            [
                ["Requests", f"{s['requests']:,}"],
                ["Requests/sec", f"{s['rps']:,.2f}"],
                ["Successful", f"{s['successful']:,}"],
                ["4xx", f"{s['errors_4xx']:,}"],
                ["5xx", f"{s['errors_5xx']:,}"],
                ["Error rate", _rate(s["error_rate"])],
                ["Avg latency", _ms(s["avg_latency_ms"])],
                ["P50", _ms(s["p50"])],
                ["P95", _ms(s["p95"])],
                ["P99", _ms(s["p99"])],
                ["Bandwidth out", _bytes(s["bytes_out"])],
                ["Blocked", f"{s['blocked']:,}"],
                ["Rate limited", f"{s['rate_limited']:,}"],
                ["Active routes", f"{s['active_routes']} of {s['total_routes']}"],
                ["Active upstreams", s["active_upstreams"]],
            ]
        )
        return 0


class AnalyticsProblematic(_Base):
    name = "analytics problematic"
    help = "Routes ranked by how much trouble they are in."

    def run(self, api: ApiClient) -> int:
        result = self.fetch(api, "/api/analytics/problematic")
        if self.wants_json:
            self.emit(result)
            return 0

        routes = result["routes"]
        self.rule("MOST PROBLEMATIC ROUTES")
        if not routes:
            self.muted("Nothing is misbehaving in this range.")
            return 0

        for route in routes:
            self.blank()
            self.line(route["label"])
            self.pairs(
                [
                    ["Error Rate", _rate(route["error_rate"])],
                    ["5xx", f"{route['errors_5xx']:,}"],
                    ["P95", _ms(route["p95"])],
                    ["P99", _ms(route["p99"])],
                    ["Requests", f"{route['requests']:,}"],
                    ["Status", route["status"]],
                ]
            )
        return 0


class AnalyticsRoutes(_Base):
    name = "analytics routes"
    help = "Per-route traffic and error statistics."

    def run(self, api: ApiClient) -> int:
        result = self.fetch(api, "/api/analytics/routes")
        if self.wants_json:
            self.emit(result)
            return 0
        self.table(
            ["ROUTE", "REQUESTS", "RPS", "ERR", "P95", "BYTES OUT", "HEALTH"],
            [
                [
                    r["label"][:44], f"{r['requests']:,}", f"{r['rps']:.2f}",
                    _rate(r["error_rate"]), _ms(r["p95"]), _bytes(r["bytes_out"]),
                    r["health"],
                ]
                for r in result["routes"]
            ],
        )
        return 0


class AnalyticsPerformance(_Base):
    name = "analytics performance"
    help = "The slowest routes, by P95."

    def run(self, api: ApiClient) -> int:
        result = self.fetch(api, "/api/analytics/performance")
        if self.wants_json:
            self.emit(result)
            return 0
        self.table(
            ["ROUTE", "P50", "P95", "P99", "MAX", "TIMEOUTS", "REQUESTS"],
            [
                [
                    r["label"][:44], _ms(r["p50"]), _ms(r["p95"]), _ms(r["p99"]),
                    _ms(r["max_latency_ms"]), f"{r['timeouts']:,}", f"{r['requests']:,}",
                ]
                for r in result["routes"]
            ],
        )
        return 0


class AnalyticsErrors(_Base):
    name = "analytics errors"
    help = "Errors by status code, route and client."

    def run(self, api: ApiClient) -> int:
        result = self.fetch(api, "/api/analytics/errors")
        if self.wants_json:
            self.emit(result)
            return 0

        errors = result["errors"]
        self.rule("BY STATUS")
        self.table(
            ["CODE", "COUNT"], [[c["code"], f"{c['count']:,}"] for c in errors["codes"]]
        )
        self.blank()
        self.rule("BY ROUTE")
        self.table(
            ["ROUTE", "ERRORS", "4XX", "5XX"],
            [
                [r["label"][:44], f"{r['errors']:,}", f"{r['errors_4xx']:,}",
                 f"{r['errors_5xx']:,}"]
                for r in errors["by_route"]
            ],
        )
        if errors["detail_truncated"]:
            self.blank()
            self.muted(
                "Client and method breakdowns cover only the request-retention "
                f"window, from {errors['detail_since']}."
            )
        self.blank()
        self.rule("BY CLIENT")
        self.table(
            ["CLIENT", "ERRORS"],
            [[c["client_ip"], f"{c['errors']:,}"] for c in errors["by_client"]],
        )
        return 0


class AnalyticsClients(_Base):
    name = "analytics clients"
    help = "Who is consuming the most traffic."

    def run(self, api: ApiClient) -> int:
        result = self.fetch(api, "/api/analytics/clients")
        if self.wants_json:
            self.emit(result)
            return 0
        self.table(
            ["CLIENT", "LABEL", "REQUESTS", "BYTES OUT", "STATUS"],
            [
                [c["ip"], c["label"] or "—", f"{c['requests']:,}",
                 _bytes(c["bytes_out"]), c["status"]]
                for c in result["clients"]
            ],
        )
        return 0


class AnalyticsTraffic(_Base):
    name = "analytics traffic"
    help = "Traffic by domain, method, route and client."

    def run(self, api: ApiClient) -> int:
        result = self.fetch(api, "/api/analytics/traffic")
        if self.wants_json:
            self.emit(result)
            return 0

        traffic = result["traffic"]
        for title, key in (
            ("BY DOMAIN", "by_domain"), ("BY METHOD", "by_method"), ("BY CLIENT", "by_client"),
        ):
            self.rule(title)
            self.table(
                ["", "REQUESTS", "BYTES OUT"],
                [
                    [row["label"][:40], f"{row['requests']:,}", _bytes(row["bytes_out"])]
                    for row in traffic[key]
                ],
            )
            self.blank()

        self.rule("BY ROUTE")
        self.table(
            ["ROUTE", "REQUESTS", "BYTES OUT"],
            [
                [r["label"][:44], f"{r['requests']:,}", _bytes(r["bytes_out"])]
                for r in traffic["by_route"]
            ],
        )
        return 0
