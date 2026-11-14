"""janus gateways / routes / upstreams."""

from __future__ import annotations

from sillo.console import Argument, Flag, Option

from cli.base import ApiCommand
from cli.client import ApiClient

__all__ = [
    "GatewayCreate", "GatewayDelete", "GatewayList", "GatewayShow", "GatewayStatus",
    "RouteCreate", "RouteDelete", "RouteDisable", "RouteEnable", "RouteList",
    "RouteShow", "RouteUpdate", "UpstreamCreate", "UpstreamDelete", "UpstreamHealth",
    "UpstreamList", "UpstreamShow",
]

GATEWAY_OPTION = Option("gateway", help="Gateway slug or id. Defaults to the first enabled one.")


class GatewayList(ApiCommand):
    """``janus gateways list`` — List the gateways Janus manages."""

    name = "gateways list"
    help = "List the gateways Janus manages."

    def run(self, api: ApiClient) -> int:
        """Call ``GET /api/gateways`` and print the result (raw JSON with ``--json``); returns the
        exit code.
        """
        data = api.get("/api/gateways")["gateways"]
        if self.wants_json:
            self.emit(data)
            return 0
        if not data:
            self.muted("No gateways are configured. Create one with: janus gateways create")
            return 0
        self.table(
            ["ID", "NAME", "LISTEN", "REGION", "SYNC", "SIMULATED"],
            [
                [
                    g["id"], g["name"], g["listen"], g["region"] or "—",
                    g["sync_state"], "yes" if g["simulated"] else "no",
                ]
                for g in data
            ],
        )
        return 0


class GatewayShow(ApiCommand):
    """``janus gateways show`` — Show one gateway.

    Arguments:
        GATEWAY: Gateway slug or id.
    """

    name = "gateways show"
    help = "Show one gateway."
    arguments = [Argument("gateway", help="Gateway slug or id.")]

    def run(self, api: ApiClient) -> int:
        """Call ``GET /api/gateways/status`` and print the result (raw JSON with ``--json``);
        returns the exit code.
        """
        result = api.get("/api/gateways/status", gateway=self.argument("gateway"))
        if self.wants_json:
            self.emit(result)
            return 0
        status = result["status"]
        self.line(result["gateway"])
        self.pairs(
            [
                ["Reachable", "yes" if status["reachable"] else f"no — {status['error']}"],
                ["Caddy", status["version"] or "unknown"],
                ["Simulated", "yes" if status["simulated"] else "no"],
                ["Sync state", status["sync_state"]],
                ["Last synced", status["last_synced_at"] or "never"],
                ["Targets", f"{status['targets_total']} ({status['targets_unhealthy']} unhealthy)"],
                ["Rate limiting", "available" if status["rate_limiting_available"] else "no module"],
            ]
        )
        if status["foreign_servers"]:
            self.blank()
            self.muted("Other servers on this Caddy, which Janus does not manage:")
            for name in status["foreign_servers"]:
                self.bullet(name)
        return 0


class GatewayStatus(GatewayShow):
    """``janus gateways status`` — Show a gateway's live status."""

    name = "gateways status"
    help = "Show a gateway's live status."


class GatewayCreate(ApiCommand):
    """``janus gateways create`` — Register a Caddy instance with Janus.

    Arguments:
        --name VALUE: Display name.
        --slug VALUE: Short identifier.
        --listen VALUE: Address Caddy's Janus server listens on. Default ``:8080``.
        --admin-url VALUE: Caddy admin API. Defaults to the global CADDY_ADMIN_URL.
        --region VALUE: Free-text region label.
    """

    name = "gateways create"
    help = "Register a Caddy instance with Janus."
    arguments = [
        Option("name", help="Display name."),
        Option("slug", help="Short identifier."),
        Option("listen", default=":8080", help="Address Caddy's Janus server listens on."),
        Option("admin-url", help="Caddy admin API. Defaults to the global CADDY_ADMIN_URL."),
        Option("region", help="Free-text region label."),
    ]

    def run(self, api: ApiClient) -> int:
        """Call ``POST /api/gateways`` and print the result (raw JSON with ``--json``); returns the
        exit code.
        """
        name = self.option("name") or self.ask("Gateway name")
        result = api.post(
            "/api/gateways",
            {
                "name": name,
                "slug": self.option("slug"),
                "listen": self.option("listen"),
                "admin_url": self.option("admin-url"),
                "region": self.option("region"),
            },
        )
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Created gateway “{result['name']}” (id {result['id']}).")
        return 0


class GatewayDelete(ApiCommand):
    """``janus gateways delete`` — Delete a gateway and everything defined on it.

    Arguments:
        GATEWAY: Gateway slug or id.
        --yes: Skip the confirmation prompt.
    """

    name = "gateways delete"
    help = "Delete a gateway and everything defined on it."
    arguments = [
        Argument("gateway", help="Gateway slug or id."),
        Flag("yes", short="y", help="Skip the confirmation prompt."),
    ]

    def run(self, api: ApiClient) -> int:
        """Call ``POST /api/gateways/delete`` and print the result (raw JSON with ``--json``);
        returns the exit code.
        """
        target = self.argument("gateway")
        if not self.confirm_destructive(
            f"Delete gateway {target} and all of its routes, upstreams and history?"
        ):
            self.muted("Cancelled.")
            return 1
        result = api.post("/api/gateways/delete", {}, gateway=target)
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Deleted {result['deleted']}.")
        return 0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class RouteList(ApiCommand):
    """``janus routes list`` — List routes on a gateway.

    Arguments:
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "routes list"
    help = "List routes on a gateway."
    arguments = [GATEWAY_OPTION]

    def run(self, api: ApiClient) -> int:
        """Call ``GET /api/routes`` and print the result (raw JSON with ``--json``); returns the
        exit code.
        """
        data = api.get("/api/routes", gateway=self.option("gateway"))["routes"]
        if self.wants_json:
            self.emit(data)
            return 0
        if not data:
            self.muted("No routes defined.")
            return 0
        self.table(
            ["ID", "PRI", "METHOD", "PATH", "UPSTREAM", "AUTH", "ENABLED"],
            [
                [
                    r["id"], r["priority"], r["method"], r["path"],
                    r["upstream"] or "—", r["auth_policy"],
                    "yes" if r["enabled"] else "no",
                ]
                for r in data
            ],
        )
        return 0


class RouteShow(ApiCommand):
    """``janus routes show`` — Show one route.

    Arguments:
        ID: Route id.
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "routes show"
    help = "Show one route."
    arguments = [Argument("id", help="Route id."), GATEWAY_OPTION]

    def run(self, api: ApiClient) -> int:
        """Call ``GET /api/routes/{id}`` and print the result (raw JSON with ``--json``); returns
        the exit code.
        """
        route = api.get(f"/api/routes/{int(self.argument('id'))}", gateway=self.option("gateway"))["route"]
        if self.wants_json:
            self.emit(route)
            return 0
        self.line(f"{route['method']} {route['path']}")
        self.pairs([[k.replace("_", " ").title(), v] for k, v in route.items()])
        return 0


class RouteCreate(ApiCommand):
    """``janus routes create`` — Create a route.

    Arguments:
        --name VALUE: Display name.
        --path VALUE: Path matcher, e.g. /api/orders*. Default ``/*``.
        --methods VALUE: Comma-separated methods. Empty matches every method.
        --upstream VALUE: Upstream name or slug.
        --priority VALUE: Higher wins. Caddy matches the first route. Default ``0``.
        --auth VALUE: Default ``public``. One of ``public``, ``api_key``, ``jwt``, ``user``,
            ``role``, ``scope``.
        --strip-prefix VALUE: Prefix to remove before proxying.
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "routes create"
    help = "Create a route."
    arguments = [
        Option("name", help="Display name."),
        Option("path", default="/*", help="Path matcher, e.g. /api/orders*."),
        Option("methods", help="Comma-separated methods. Empty matches every method."),
        Option("upstream", help="Upstream name or slug."),
        Option("priority", default="0", help="Higher wins. Caddy matches the first route."),
        Option("auth", default="public", choices=["public", "api_key", "jwt", "user", "role", "scope"]),
        Option("strip-prefix", help="Prefix to remove before proxying."),
        GATEWAY_OPTION,
    ]

    def run(self, api: ApiClient) -> int:
        """Call ``POST /api/routes`` and print the result (raw JSON with ``--json``); returns the
        exit code.
        """
        name = self.option("name") or self.ask("Route name")
        result = api.post(
            "/api/routes",
            {
                "name": name,
                "path": self.option("path"),
                "methods": self.option("methods") or "",
                "upstream": self.option("upstream"),
                "priority": int(self.option("priority") or 0),
                "auth_policy": self.option("auth"),
                "strip_prefix": self.option("strip-prefix"),
            },
            gateway=self.option("gateway"),
        )
        if self.wants_json:
            self.emit(result)
        else:
            route = result["route"]
            self.success(f"Created route {route['id']}: {route['method']} {route['path']}")
            self.muted("Run `janus config apply` to deploy it.")
        return 0


class RouteUpdate(ApiCommand):
    """``janus routes update`` — Update a route.

    Arguments:
        ID: Route id.
        --name VALUE
        --path VALUE
        --methods VALUE
        --priority VALUE
        --auth VALUE: One of ``public``, ``api_key``, ``jwt``, ``user``, ``role``, ``scope``.
        --timeout VALUE: Request timeout in seconds.
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "routes update"
    help = "Update a route."
    arguments = [
        Argument("id", help="Route id."),
        Option("name"), Option("path"), Option("methods"), Option("priority"),
        Option("auth", choices=["public", "api_key", "jwt", "user", "role", "scope"]),
        Option("timeout", help="Request timeout in seconds."),
        GATEWAY_OPTION,
    ]

    def run(self, api: ApiClient) -> int:
        """Call ``PATCH /api/routes/{id}`` and print the result (raw JSON with ``--json``); returns
        the exit code.
        """
        payload = {}
        for flag, field in (
            ("name", "name"), ("path", "path"), ("methods", "methods"),
            ("auth", "auth_policy"),
        ):
            if self.option(flag):
                payload[field] = self.option(flag)
        if self.option("priority"):
            payload["priority"] = int(self.option("priority"))
        if self.option("timeout"):
            payload["timeout_seconds"] = int(self.option("timeout"))
        if not payload:
            self.warn("Nothing to change.")
            return 1

        result = api.patch(
            f"/api/routes/{int(self.argument('id'))}", payload, gateway=self.option("gateway")
        )
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Updated route {result['route']['id']}.")
            self.muted("Run `janus config apply` to deploy it.")
        return 0


class RouteDelete(ApiCommand):
    """``janus routes delete`` — Delete a route.

    Arguments:
        ID: Route id.
        --yes: Skip the confirmation prompt.
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "routes delete"
    help = "Delete a route."
    arguments = [
        Argument("id", help="Route id."),
        Flag("yes", short="y", help="Skip the confirmation prompt."),
        GATEWAY_OPTION,
    ]

    def run(self, api: ApiClient) -> int:
        """Call ``DELETE /api/routes/{id}``, then ``GET /api/routes/{id}`` and print the result (raw
        JSON with ``--json``); returns the exit code.
        """
        route_id = int(self.argument("id"))
        # Fetched before the prompt so the question names the route rather than
        # its id. "Delete route 4192?" is not a question anyone can answer.
        route = api.get(f"/api/routes/{route_id}", gateway=self.option("gateway"))["route"]
        if not self.confirm_destructive(f"Delete route {route['method']} {route['path']}?"):
            self.muted("Cancelled.")
            return 1

        result = api.delete(f"/api/routes/{route_id}", gateway=self.option("gateway"))
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Deleted {result['deleted']}.")
            self.muted("Run `janus config apply` to deploy the change.")
        return 0


class _RouteToggle(ApiCommand):
    """Shared implementation of ``routes enable`` / ``routes disable``."""

    enabled = True

    def run(self, api: ApiClient) -> int:
        """Call ``POST /api/routes/{id}/toggle`` with the target state and print the result."""
        result = api.post(
            f"/api/routes/{int(self.argument('id'))}/toggle",
            {"enabled": self.enabled},
            gateway=self.option("gateway"),
        )
        if self.wants_json:
            self.emit(result)
        else:
            route = result["route"]
            self.success(
                f"Route {route['id']} {'enabled' if self.enabled else 'disabled'}: "
                f"{route['method']} {route['path']}"
            )
            self.muted("Run `janus config apply` to deploy it.")
        return 0


class RouteEnable(_RouteToggle):
    """``janus routes enable`` — Enable a route.

    Arguments:
        ID
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "routes enable"
    help = "Enable a route."
    enabled = True
    arguments = [Argument("id"), GATEWAY_OPTION]


class RouteDisable(_RouteToggle):
    """``janus routes disable`` — Disable a route.

    Arguments:
        ID
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "routes disable"
    help = "Disable a route."
    enabled = False
    arguments = [Argument("id"), GATEWAY_OPTION]


# ---------------------------------------------------------------------------
# Upstreams
# ---------------------------------------------------------------------------


class UpstreamList(ApiCommand):
    """``janus upstreams list`` — List upstreams and their targets.

    Arguments:
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "upstreams list"
    help = "List upstreams and their targets."
    arguments = [GATEWAY_OPTION]

    def run(self, api: ApiClient) -> int:
        """Call ``GET /api/upstreams`` and print the result (raw JSON with ``--json``); returns the
        exit code.
        """
        data = api.get("/api/upstreams", gateway=self.option("gateway"))["upstreams"]
        if self.wants_json:
            self.emit(data)
            return 0
        if not data:
            self.muted("No upstreams defined.")
            return 0
        for upstream in data:
            healthy = sum(1 for t in upstream["targets"] if t["healthy"])
            self.line(f"{upstream['name']}  ({healthy}/{len(upstream['targets'])} healthy)")
            for target in upstream["targets"]:
                mark = "  " if target["healthy"] else "! "
                self.muted(f"  {mark}{target['dial']}   weight {target['weight']}")
            self.blank()
        return 0


class UpstreamShow(ApiCommand):
    """``janus upstreams show`` — Show one upstream.

    Arguments:
        NAME: Upstream name or slug.
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "upstreams show"
    help = "Show one upstream."
    arguments = [Argument("name", help="Upstream name or slug."), GATEWAY_OPTION]

    def run(self, api: ApiClient) -> int:
        """Call ``GET /api/upstreams`` and print the result (raw JSON with ``--json``); returns the
        exit code.
        """
        wanted = self.argument("name")
        data = api.get("/api/upstreams", gateway=self.option("gateway"))["upstreams"]
        found = next((u for u in data if wanted in (u["name"], u["slug"], str(u["id"]))), None)
        if found is None:
            self.error(f"No upstream '{wanted}'.")
            return 1
        if self.wants_json:
            self.emit(found)
            return 0
        self.line(found["name"])
        self.pairs(
            [["Policy", found["policy"]], ["Enabled", found["enabled"]],
             ["Health path", found["health_path"] or "passive only"]]
        )
        self.table(
            ["DIAL", "WEIGHT", "ENABLED", "HEALTHY"],
            [[t["dial"], t["weight"], t["enabled"], t["healthy"]] for t in found["targets"]],
        )
        return 0


class UpstreamCreate(ApiCommand):
    """``janus upstreams create`` — Create an upstream pool.

    Arguments:
        --name VALUE: Pool name.
        --targets VALUE: Comma-separated host:port[:weight] entries.
        --health-path VALUE: Active health-check path, e.g. /healthz.
        --policy VALUE: Default ``weighted_round_robin``.
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "upstreams create"
    help = "Create an upstream pool."
    arguments = [
        Option("name", help="Pool name."),
        Option("targets", help="Comma-separated host:port[:weight] entries."),
        Option("health-path", help="Active health-check path, e.g. /healthz."),
        Option("policy", default="weighted_round_robin"),
        GATEWAY_OPTION,
    ]

    def run(self, api: ApiClient) -> int:
        """Call ``POST /api/upstreams`` and print the result (raw JSON with ``--json``); returns the
        exit code.
        """
        name = self.option("name") or self.ask("Upstream name")
        raw = self.option("targets") or self.ask("Targets (host:port:weight, comma separated)")

        targets = []
        for entry in (raw or "").split(","):
            entry = entry.strip()
            if not entry:
                continue
            # `host:port:weight` — split from the right once, so an IPv6
            # address with colons in it does not get mangled into a weight.
            head, _, tail = entry.rpartition(":")
            if head and tail.isdigit() and head.count(":") >= 1:
                targets.append({"dial": head, "weight": int(tail)})
            else:
                targets.append({"dial": entry, "weight": 1})

        result = api.post(
            "/api/upstreams",
            {
                "name": name,
                "policy": self.option("policy"),
                "health_path": self.option("health-path"),
                "targets": targets,
            },
            gateway=self.option("gateway"),
        )
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Created upstream “{result['name']}” with {len(targets)} target(s).")
        return 0


class UpstreamDelete(ApiCommand):
    """``janus upstreams delete`` — Delete an upstream.

    Arguments:
        ID: Upstream id.
        --yes
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "upstreams delete"
    help = "Delete an upstream."
    arguments = [
        Argument("id", help="Upstream id."),
        Flag("yes", short="y"),
        GATEWAY_OPTION,
    ]

    def run(self, api: ApiClient) -> int:
        """Call ``DELETE /api/upstreams/{id}`` and print the result (raw JSON with ``--json``);
        returns the exit code.
        """
        if not self.confirm_destructive(f"Delete upstream {self.argument('id')}?"):
            self.muted("Cancelled.")
            return 1
        result = api.delete(
            f"/api/upstreams/{int(self.argument('id'))}", gateway=self.option("gateway")
        )
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Deleted {result['deleted']}.")
        return 0


class UpstreamHealth(ApiCommand):
    """``janus upstreams health`` — Refresh and show upstream health from Caddy.

    Arguments:
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "upstreams health"
    help = "Refresh and show upstream health from Caddy."
    arguments = [GATEWAY_OPTION]

    def run(self, api: ApiClient) -> int:
        """Call ``GET /api/upstreams/health`` and print the result (raw JSON with ``--json``);
        returns the exit code.
        """
        result = api.get("/api/upstreams/health", gateway=self.option("gateway"))
        if self.wants_json:
            self.emit(result)
            return 0
        if not result["refresh"]["available"]:
            self.warn("Caddy reported no upstream data. Is the gateway reachable?")
        self.table(
            ["UPSTREAM", "DIAL", "HEALTHY", "WEIGHT", "ERROR"],
            [
                [t["upstream"], t["dial"], "yes" if t["healthy"] else "NO",
                 t["weight"], t["error"] or ""]
                for t in result["targets"]
            ],
        )
        return 0
