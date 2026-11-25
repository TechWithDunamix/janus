"""janus overview / requests / domains / policies / upstreams update / gateways maintenance / security overview.

The day-to-day operator commands. Each prints a table or a short summary,
and ``--json`` for scripts. Changes to what Caddy serves are saved; ``janus config apply`` deploys them
(``gateways maintenance`` deploys at once).
"""

from __future__ import annotations

import time

from sillo.console import Argument, Flag, Option

from cli.base import ApiCommand, cell
from cli.client import ApiClient

GATEWAY = Option("gateway", help="Gateway slug or id (default: the first enabled gateway).")
RANGE = Option("range", default="24h", help="15m, 1h, 6h, 24h, 7d or 30d.")
YES = Flag("yes", help="Do not ask for confirmation.")
DEPLOY_HINT = "Run `janus config apply` to deploy it."

REQUEST_COLUMNS = [("TIME", "at"), ("METHOD", "method"), ("STATUS", "status"), ("MS", "latency_ms"),
                   ("HOST", "host"), ("PATH", "path"), ("CLIENT", "client_ip"), ("EDGE", "edge_action")]


def _bool_option(value: str, name: str) -> bool:
    lowered = value.strip().lower()
    if lowered in ("yes", "true", "on", "1"):
        return True
    if lowered in ("no", "false", "off", "0"):
        return False
    raise ValueError(f"--{name} must be yes or no.")


class Overview(ApiCommand):
    """``janus overview`` — the gateway at a glance: health, traffic, alerts, recent deploys."""

    name = "overview"
    help = "Show a gateway's health, traffic, open alerts and recent deployments."
    arguments = [GATEWAY, RANGE]

    def run(self, api: ApiClient) -> int:
        data = api.get("/api/overview", gateway=self.option("gateway"), range=self.option("range"))
        if self.wants_json:
            self.emit(data)
            return 0
        gw, traffic, health = data["gateway"], data["traffic"], data["health"]
        self.rule(f"{gw['name']} · {data['range'].get('label', self.option('range'))}"
                  + (" · MAINTENANCE" if gw["maintenance"] else ""))
        self.pairs([(k.replace("_", " "), f"{v:.2%}" if k.endswith("_rate") else cell(v))
                    for k, v in traffic.items() if not isinstance(v, (dict, list))])
        summary = {k: v for k, v in health.items() if not isinstance(v, (dict, list))}
        if summary:
            self.line()
            self.pairs([(k.replace("_", " "), cell(v)) for k, v in summary.items()])
        self.line()
        self.rows(data["alerts"], [("OPENED", "opened_at"), ("SEVERITY", "severity"), ("ALERT", "title"),
                                   ("DETAIL", "detail")], empty="No open alerts.")
        self.line()
        self.rows(data["deployments"], [("STARTED", "started_at"), ("VERSION", "version"), ("STATUS", "status"),
                                        ("BY", "actor"), ("SUMMARY", "summary"), ("ERROR", "error")],
                  empty="No deployments yet.")
        return 0


class Requests(ApiCommand):
    """``janus requests`` — the latest requests through the gateway; ``--follow`` keeps printing new ones."""

    name = "requests"
    help = "Show the latest requests (filter by status, path, client); --follow streams new ones."
    arguments = [
        GATEWAY,
        Option("status", help="A code like 404 or a class like 5xx."),
        Option("method", help="GET, POST …"),
        Option("path", help="Paths containing this."),
        Option("client", help="One client address."),
        Option("host", help="One hostname."),
        Option("limit", default="50", help="How many (1-500)."),
        Flag("follow", help="Keep printing new requests until Ctrl-C."),
    ]

    def _fetch(self, api: ApiClient, **extra: str) -> list[dict]:
        params = {name: self.option(name) for name in ("status", "method", "client", "host", "limit")}
        return api.get("/api/requests", gateway=self.option("gateway"), contains=self.option("path"), **params,
                       **extra)["requests"]

    def run(self, api: ApiClient) -> int:
        rows = self._fetch(api)
        if not self.flag("follow"):
            return self.rows(rows, REQUEST_COLUMNS, empty="No requests match.")
        last = max((r["id"] for r in rows), default=0)
        for row in reversed(rows):
            self._print_one(row)
        try:
            while True:
                time.sleep(1.0)
                fresh = self._fetch(api, since_id=str(last))
                for row in reversed(fresh):
                    self._print_one(row)
                last = max([last, *(r["id"] for r in fresh)])
        except KeyboardInterrupt:
            return 0

    def _print_one(self, row: dict) -> None:
        if self.wants_json:
            self.emit(row)
            return
        edge = f"  [{row['edge_action']}]" if row.get("edge_action") else ""
        self.line(f"{cell(row['at'])}  {row['method']:<6} {row['status']}  {row['latency_ms']:>7} ms  "
                  f"{row['host']}{row['path']}  {row['client_ip']}{edge}")


# --------------------------------------------------------------------------
# domains
# --------------------------------------------------------------------------


DOMAIN_COLUMNS = [("ID", "id"), ("HOSTNAME", "hostname"), ("TLS", "tls_mode"), ("TLS STATUS", "tls_status"),
                  ("EXPIRES", "tls_expires_at"), ("ENABLED", "enabled"), ("ROUTES", "routes")]


class DomainList(ApiCommand):
    """``janus domains list`` — hostnames on a gateway and their TLS state."""

    name = "domains list"
    help = "List a gateway's hostnames and their TLS state."
    arguments = [GATEWAY]

    def run(self, api: ApiClient) -> int:
        rows = api.get("/api/domains", gateway=self.option("gateway"))["domains"]
        return self.rows(rows, DOMAIN_COLUMNS, empty="No domains. Add one with: janus domains create HOSTNAME")


class DomainCreate(ApiCommand):
    """``janus domains create HOSTNAME [--tls auto|internal|off]`` — add a hostname."""

    name = "domains create"
    help = "Add a hostname to a gateway."
    arguments = [Argument("hostname", help="e.g. api.example.com"),
                 Option("tls", default="auto", help="auto (public certificate), internal or off."),
                 Option("enabled", default="yes", help="yes or no."), GATEWAY]

    def run(self, api: ApiClient) -> int:
        try:
            enabled = _bool_option(self.option("enabled"), "enabled")
        except ValueError as exc:
            self.error(str(exc))
            return 2
        domain = api.post("/api/domains", {"hostname": self.argument("hostname"), "tls_mode": self.option("tls"),
                                           "enabled": enabled}, gateway=self.option("gateway"))
        if self.wants_json:
            self.emit(domain)
        else:
            self.success(f"Added {domain['hostname']} (id {domain['id']}).")
            self.muted(DEPLOY_HINT)
        return 0


class DomainUpdate(ApiCommand):
    """``janus domains update ID [--tls MODE] [--enabled yes|no]`` — change TLS or switch a hostname off."""

    name = "domains update"
    help = "Change a hostname's TLS mode or switch it on or off."
    arguments = [Argument("id", help="Domain id (see domains list)."),
                 Option("tls", help="auto, internal or off."), Option("enabled", help="yes or no."), GATEWAY]

    def run(self, api: ApiClient) -> int:
        body: dict = {}
        if self.option("tls"):
            body["tls_mode"] = self.option("tls")
        if self.option("enabled"):
            try:
                body["enabled"] = _bool_option(self.option("enabled"), "enabled")
            except ValueError as exc:
                self.error(str(exc))
                return 2
        if not body:
            self.error("Nothing to change: pass --tls or --enabled.")
            return 2
        domain = api.patch(f"/api/domains/{self.argument('id')}", body, gateway=self.option("gateway"))
        if self.wants_json:
            self.emit(domain)
        else:
            self.success(f"Updated {domain['hostname']}.")
            self.muted(DEPLOY_HINT)
        return 0


class DomainDelete(ApiCommand):
    """``janus domains delete ID`` — remove a hostname that no route uses."""

    name = "domains delete"
    help = "Remove a hostname (refused while a route uses it)."
    arguments = [Argument("id", help="Domain id."), GATEWAY, YES]

    def run(self, api: ApiClient) -> int:
        if not self.confirm_destructive(f"Delete domain {self.argument('id')}?"):
            return 1
        result = api.delete(f"/api/domains/{self.argument('id')}", gateway=self.option("gateway"))
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Deleted {result['deleted']}.")
            self.muted(DEPLOY_HINT)
        return 0


# --------------------------------------------------------------------------
# policies
# --------------------------------------------------------------------------


POLICY_COLUMNS = [("ID", "id"), ("NAME", "name"), ("RULE", "sentence"), ("PRIORITY", "priority"),
                  ("SCOPE", "scope"), ("ENABLED", "enabled"), ("MATCHES", "match_count")]
POLICY_OPTIONS = [
    Option("subject", help="ip, cidr, api_key, client, path, method, header, country or rate."),
    Option("operator", help="equals, in_cidr, matches or exceeds."),
    Option("value", help="What to match, e.g. 10.0.0.0/8 or /admin."),
    Option("action", help="allow, block, rate_limit, require_auth or log."),
    Option("priority", help="Higher runs first."),
    Option("rate-limit", help="For action rate_limit: the rate limit's name."),
    Option("domain", help="Only this hostname."),
    Option("route", help="Only this route (its name)."),
    Option("description", help="A note for the team."),
]


def _policy_body(command: ApiCommand) -> dict:
    body: dict = {}
    for name in ("subject", "operator", "value", "action", "description", "domain", "route"):
        if command.option(name):
            body[name] = command.option(name)
    if command.option("priority"):
        body["priority"] = int(command.option("priority"))
    if command.option("rate-limit"):
        body["rate_limit"] = command.option("rate-limit")
    return body


class PolicyList(ApiCommand):
    """``janus policies list`` — the gateway's rules, highest priority first."""

    name = "policies list"
    help = "List policies (IF subject operator value THEN action), highest priority first."
    arguments = [GATEWAY]

    def run(self, api: ApiClient) -> int:
        rows = api.get("/api/policies", gateway=self.option("gateway"))["policies"]
        return self.rows(rows, POLICY_COLUMNS, empty="No policies. Add one with: janus policies create NAME …")


class PolicyCreate(ApiCommand):
    """``janus policies create NAME --subject S --operator O --value V --action A`` — add a rule."""

    name = "policies create"
    help = "Add a policy: IF subject operator value THEN action."
    arguments = [Argument("name"), *POLICY_OPTIONS, GATEWAY]

    def run(self, api: ApiClient) -> int:
        body = {"name": self.argument("name"), "subject": "ip", "operator": "equals", "action": "block",
                **_policy_body(self)}
        if "value" not in body:
            self.error("Pass --value: what the rule matches.")
            return 2
        policy = api.post("/api/policies", body, gateway=self.option("gateway"))
        if self.wants_json:
            self.emit(policy)
        else:
            self.success(f"Added policy {policy['id']}: {policy['sentence']}")
            self.muted(DEPLOY_HINT)
        return 0


class PolicyUpdate(ApiCommand):
    """``janus policies update ID [options]`` — change a rule; what you leave out stays."""

    name = "policies update"
    help = "Change a policy; fields you leave out keep their value."
    arguments = [Argument("id"), Option("name"), *POLICY_OPTIONS, Option("enabled", help="yes or no."), GATEWAY]

    def run(self, api: ApiClient) -> int:
        body = _policy_body(self)
        if self.option("name"):
            body["name"] = self.option("name")
        if self.option("enabled"):
            try:
                body["enabled"] = _bool_option(self.option("enabled"), "enabled")
            except ValueError as exc:
                self.error(str(exc))
                return 2
        if not body:
            self.error("Nothing to change.")
            return 2
        policy = api.patch(f"/api/policies/{self.argument('id')}", body, gateway=self.option("gateway"))
        if self.wants_json:
            self.emit(policy)
        else:
            self.success(f"Policy {policy['id']}: {policy['sentence']}" + ("" if policy["enabled"] else " (off)"))
            self.muted(DEPLOY_HINT)
        return 0


class PolicyDelete(ApiCommand):
    """``janus policies delete ID`` — remove a rule."""

    name = "policies delete"
    help = "Delete a policy."
    arguments = [Argument("id"), GATEWAY, YES]

    def run(self, api: ApiClient) -> int:
        if not self.confirm_destructive(f"Delete policy {self.argument('id')}?"):
            return 1
        result = api.delete(f"/api/policies/{self.argument('id')}", gateway=self.option("gateway"))
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Deleted {result['deleted']}.")
            self.muted(DEPLOY_HINT)
        return 0


# --------------------------------------------------------------------------
# upstreams and maintenance
# --------------------------------------------------------------------------


class UpstreamUpdate(ApiCommand):
    """``janus upstreams update ID [--targets host:port[:weight],…] [--policy P] [--maintenance yes|no]``."""

    name = "upstreams update"
    help = "Change an upstream: targets (host:port[:weight]), policy, health path, on/off, maintenance."
    arguments = [
        Argument("id", help="Upstream id (see upstreams list)."),
        Option("targets", help="Replace the targets: 10.0.0.5:8080:3,10.0.0.6:8080 (host:port[:weight])."),
        Option("policy", help="weighted_round_robin, round_robin, least_conn, ip_hash, first …"),
        Option("health-path", help="Path Caddy probes, e.g. /healthz. Empty to stop probing."),
        Option("enabled", help="yes or no."),
        Option("maintenance", help="yes or no."),
        GATEWAY,
    ]

    def run(self, api: ApiClient) -> int:
        body: dict = {}
        if self.option("targets"):
            targets = []
            for part in self.option("targets").split(","):
                pieces = part.strip().split(":")
                weight = int(pieces.pop()) if len(pieces) == 3 else 1  # host:port:weight, as upstreams create
                if pieces and pieces[0]:
                    targets.append({"dial": ":".join(pieces), "weight": weight})
            body["targets"] = targets
        if self.option("policy"):
            body["policy"] = self.option("policy")
        if self.option("health-path") is not None and self.option("health-path") != "":
            body["health_path"] = self.option("health-path")
        for flag in ("enabled", "maintenance"):
            if self.option(flag):
                try:
                    body[flag] = _bool_option(self.option(flag), flag)
                except ValueError as exc:
                    self.error(str(exc))
                    return 2
        if not body:
            self.error("Nothing to change.")
            return 2
        upstream = api.patch(f"/api/upstreams/{self.argument('id')}", body, gateway=self.option("gateway"))
        if self.wants_json:
            self.emit(upstream)
        else:
            targets = ", ".join(f"{t['dial']} (weight {t['weight']})" for t in upstream["targets"])
            self.success(f"Updated {upstream['name']}: {targets}")
            self.muted(DEPLOY_HINT)
        return 0


class GatewayMaintenance(ApiCommand):
    """``janus gateways maintenance on|off`` — answer every request with a maintenance page, deployed now."""

    name = "gateways maintenance"
    help = "Turn maintenance mode on or off for a whole gateway (deploys immediately)."
    arguments = [Argument("state", help="on or off."),
                 Option("status", help="HTTP status to answer with (default 503)."),
                 Option("message", help="Body to answer with."), GATEWAY, YES]

    def run(self, api: ApiClient) -> int:
        state = self.argument("state").lower()
        if state not in ("on", "off"):
            self.error("State must be on or off.")
            return 2
        if state == "on" and not self.confirm_destructive("Every request will get the maintenance page. Continue?"):
            return 1
        body: dict = {"on": state == "on"}
        if self.option("status"):
            body["status"] = int(self.option("status"))
        if self.option("message"):
            body["body"] = self.option("message")
        result = api.post("/api/gateways/maintenance", body, gateway=self.option("gateway"))
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Maintenance is {'on' if result['maintenance'] else 'off'} and deployed.")
        return 0


# --------------------------------------------------------------------------
# security overview and clients
# --------------------------------------------------------------------------


class SecurityOverview(ApiCommand):
    """``janus security overview`` — what the edge blocked and limited, and anything unusual."""

    name = "security overview"
    help = "Show blocked and rate-limited traffic, anomalies and the most-blocked clients."
    arguments = [GATEWAY, RANGE]

    def run(self, api: ApiClient) -> int:
        data = api.get("/api/security/overview", gateway=self.option("gateway"), range=self.option("range"))
        if self.wants_json:
            self.emit(data)
            return 0
        self.pairs([(k.replace("_", " "), cell(v)) for k, v in data["summary"].items()
                    if not isinstance(v, (dict, list))])
        self.line()
        self.rows(data["top_blocked"], [("CLIENT", "client_ip"), ("BLOCKED", "blocked")],
                  empty="Nobody was blocked in this window.")
        self.line()
        self.rows(data["anomalies"], [("DETECTED", "detected_at"), ("KIND", "kind"), ("OBSERVED", "observed"),
                                      ("BASELINE", "baseline"), ("DETAIL", "detail")], empty="No anomalies to review.")
        return 0


CLIENT_COLUMNS = [("ID", "id"), ("CLIENT", "identifier"), ("LABEL", "label"), ("STATUS", "status"),
                  ("REQUESTS", "request_count"), ("ERRORS", "error_count"), ("BLOCKED", "blocked_count"),
                  ("LAST SEEN", "last_seen_at")]


class SecurityClients(ApiCommand):
    """``janus security clients [--search Q] [--status blocked]`` — who is calling, by traffic."""

    name = "security clients"
    help = "List clients by traffic; search an address or filter by status."
    arguments = [Option("search", help="Part of an address or identifier."),
                 Option("status", help="active, blocked or allowed."), Option("limit", default="50")]

    def run(self, api: ApiClient) -> int:
        rows = api.get("/api/security/clients", q=self.option("search"), status=self.option("status"),
                       limit=self.option("limit"))["clients"]
        return self.rows(rows, CLIENT_COLUMNS, empty="No clients match.")


class SecurityClient(ApiCommand):
    """``janus security client ID`` — one client: totals, rules, status mix and latest requests."""

    name = "security client"
    help = "Show one client: totals, rules that match it, status codes and latest requests."
    arguments = [Argument("id", help="Client id (see security clients)."), GATEWAY, RANGE]

    def run(self, api: ApiClient) -> int:
        data = api.get(f"/api/security/clients/{self.argument('id')}", gateway=self.option("gateway"),
                       range=self.option("range"))
        if self.wants_json:
            self.emit(data)
            return 0
        self.show(data["client"])
        self.line()
        self.rows(data["rules"], [("RULE", "id"), ("ACTION", "action"), ("CIDR", "cidr"), ("REASON", "reason"),
                                  ("EXPIRES", "expires_at")], empty="No allow or block rules match this client.")
        self.line()
        self.rows(data["by_status"], [("STATUS", "status"), ("REQUESTS", "count")], empty="No requests in this window.")
        self.line()
        self.rows(data["recent"], [("TIME", "at"), ("METHOD", "method"), ("STATUS", "status"), ("MS", "latency_ms"),
                                   ("PATH", "path"), ("EDGE", "edge_action")], empty="")
        return 0


COMMANDS = (Overview, Requests, DomainList, DomainCreate, DomainUpdate, DomainDelete, PolicyList, PolicyCreate,
            PolicyUpdate, PolicyDelete, UpstreamUpdate, GatewayMaintenance, SecurityOverview, SecurityClients,
            SecurityClient)
