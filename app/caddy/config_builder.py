"""Janus desired state → a Caddy configuration.

This module is pure. It takes plain snapshots of the models and returns a JSON
structure; it opens no connections, touches no database and has no side
effects. That is what makes the whole configuration pipeline testable: the
generator can be exercised against any estate shape without a Caddy anywhere.

**What Janus owns.** Two paths inside Caddy's configuration, both named after
`CADDY_SERVER_NAME`:

    apps/http/servers/<name>     the routes, matchers and handlers
    logging/logs/<name>          the access log Janus ingests

Nothing else is ever written. A Caddy instance can carry other servers, other
apps and other loggers, and Janus reads the whole configuration for drift
detection but only ever replaces those two subtrees. There is no code path in
this project that PUTs the configuration root — see
`app/caddy/manager.py::apply`, which is the only writer.

**Route order is meaning.** Caddy evaluates routes top to bottom and a
`terminal` route stops the walk. The generator emits, in this order:

    1. global IP blocks        — refused before anything else looks at them
    2. global IP allowlists    — a deny-by-default fence, when one is defined
    3. policy rules            — highest priority first
    4. gateway maintenance     — when the whole gateway is drained
    5. routes                  — by priority descending, then by id

Getting this order wrong is not a cosmetic bug: an allowlist emitted after a
proxy route is an allowlist that never runs.
"""

from __future__ import annotations

from functools import reduce
from math import gcd
from typing import Any

__all__ = [
    "CAPABILITY_RATE_LIMIT",
    "GeneratedConfig",
    "build_server",
    "build_logging",
    "seconds",
]

#: Caddy's `rate_limit` handler ships in the `caddy-ratelimit` plugin, not in
#: the standard binary. The generator emits it only when the gateway reports
#: this capability; without it, limits are stored and displayed but marked as
#: not enforceable by that build. See `app/services/ratelimits.py` for how that
#: is surfaced — Janus never emits a handler it knows will be rejected, and
#: never claims a limit is being enforced when it is not.
CAPABILITY_RATE_LIMIT = "http.handlers.rate_limit"


def seconds(value: int | float | None) -> str | None:
    """A Caddy duration string, or `None` to leave the field out.

    Caddy's `caddy.Duration` unmarshals either an integer of nanoseconds or a
    Go duration string. The string form is used throughout because a
    configuration a human may read in `config show` should say `30s`, not
    `30000000000`.
    """
    if not value:
        return None
    return f"{int(value)}s"


class GeneratedConfig(dict):
    """The generated server object, plus what the generator wants to report.

    A plain dict subclass so it serialises as the configuration it is, with
    the notes hung off attributes rather than mixed into the payload — anything
    added to the dictionary itself would be sent to Caddy.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(payload)
        #: Things the operator should know that are not errors: a rate limit
        #: that could not be emitted, a route with no upstream.
        self.warnings: list[str] = []
        self.counts: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Matchers
# ---------------------------------------------------------------------------


def _match_for_route(route: dict[str, Any]) -> dict[str, Any]:
    """The matcher set for one Janus route.

    An empty matcher matches everything, which is correct for a catch-all and
    is why the keys are added conditionally rather than always present with
    empty lists — `{"host": []}` matches *nothing* in Caddy, which would
    silently disable every route that did not name a domain.
    """
    match: dict[str, Any] = {}
    if route.get("host"):
        match["host"] = [route["host"]]
    path = route.get("path") or "/*"
    match["path"] = [path]
    methods = route.get("methods") or []
    if methods:
        match["method"] = [m.upper() for m in methods]
    return match


def _ip_match(ranges: list[str]) -> dict[str, Any]:
    """Match by client address.

    `client_ip` rather than `remote_ip`: `remote_ip` is the immediate peer,
    which behind a load balancer is the load balancer, so a blocklist written
    against it would block the wrong thing. `client_ip` respects the trusted
    proxy configuration, which is what an operator means by "this client".
    """
    return {"client_ip": {"ranges": ranges}}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _static_response(
    status: int, body: str | None = None, headers: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    handler: dict[str, Any] = {"handler": "static_response", "status_code": status}
    if body:
        handler["body"] = body
    if headers:
        handler["headers"] = headers
    return handler


def _header_handler(route: dict[str, Any]) -> dict[str, Any] | None:
    """Request and response header transformation, as one handler.

    Returns `None` when there is nothing to do, so the caller can leave the
    handler out entirely rather than emitting an empty one — Caddy accepts an
    empty `headers` handler, but it shows up in `config diff` as noise on every
    route that does not use it.
    """
    request_set = route.get("request_headers") or {}
    response_set = route.get("response_headers") or {}
    request_delete = route.get("remove_request_headers") or []
    response_delete = route.get("remove_response_headers") or []

    if not (request_set or response_set or request_delete or response_delete):
        return None

    handler: dict[str, Any] = {"handler": "headers"}
    if request_set or request_delete:
        request: dict[str, Any] = {}
        if request_set:
            request["set"] = {k: [str(v)] for k, v in request_set.items()}
        if request_delete:
            request["delete"] = list(request_delete)
        handler["request"] = request
    if response_set or response_delete:
        response: dict[str, Any] = {}
        if response_set:
            # `set` on a response goes under `deferred` handling in Caddy's
            # schema only when it needs to see the upstream's headers first;
            # a plain set is fine here and is what an operator means by
            # "add this header to the response".
            response["set"] = {k: [str(v)] for k, v in response_set.items()}
        if response_delete:
            response["delete"] = list(response_delete)
        handler["response"] = response
    return handler


def _rewrite_handler(route: dict[str, Any]) -> dict[str, Any] | None:
    if route.get("rewrite_to"):
        return {"handler": "rewrite", "uri": route["rewrite_to"]}
    if route.get("strip_prefix"):
        return {"handler": "rewrite", "strip_path_prefix": route["strip_prefix"]}
    return None


def _upstream_handler(
    upstream: dict[str, Any],
    *,
    route: dict[str, Any],
    canary: dict[str, Any] | None = None,
    canary_percent: int = 0,
) -> dict[str, Any]:
    """A `reverse_proxy` handler for a pool.

    Weights are positional: Caddy's `weighted_round_robin` policy takes a
    `weights` array that lines up index-for-index with `upstreams`. Building
    the two lists in one pass is not a style choice — zipping them separately
    is how they end up out of step, and an out-of-step weight list sends the
    wrong share of traffic to the wrong backend with nothing to show for it.
    """
    dials: list[dict[str, Any]] = []
    weights: list[int] = []
    for target in upstream.get("targets", []):
        if not target.get("enabled", True):
            continue
        dials.append({"dial": target["dial"]})
        weights.append(max(1, int(target.get("weight", 1))))

    # A canary split is expressed as extra upstreams with proportional weights
    # rather than as a separate handler, because Caddy's load balancer is
    # already a weighted chooser and a second mechanism would be a second thing
    # to reason about. 90/10 across a 3-target pool and a 2-target canary comes
    # out as weights scaled so each side's total is in the right ratio.
    if canary and canary_percent > 0:
        canary_dials: list[dict[str, Any]] = []
        canary_weights: list[int] = []
        for target in canary.get("targets", []):
            if not target.get("enabled", True):
                continue
            canary_dials.append({"dial": target["dial"]})
            canary_weights.append(max(1, int(target.get("weight", 1))))
        if canary_dials:
            primary_total = sum(weights) or 1
            canary_total = sum(canary_weights) or 1
            # Scale each side so primary:canary lands on (100-p):p, then reduce
            # the whole vector by its GCD.
            #
            # The reduction is not tidiness. Caddy's `weighted_round_robin`
            # hands each upstream `weight` *consecutive* requests, so the
            # weights are a cycle length, not a probability. Scaling a 5:3:2
            # pool for a 10% canary produces 450:270:180:100, and that sends
            # the first 450 requests to one backend before the second ever
            # sees traffic — a "90/10 split" that is a 100/0 split for as long
            # as anyone is likely to watch it.
            primary_share = 100 - canary_percent
            weights = [max(1, round(w * primary_share * 10 / primary_total)) for w in weights]
            canary_weights = [
                max(1, round(w * canary_percent * 10 / canary_total)) for w in canary_weights
            ]
            dials.extend(canary_dials)
            weights.extend(canary_weights)

    weights = _reduce_weights(weights)

    handler: dict[str, Any] = {"handler": "reverse_proxy", "upstreams": dials}

    policy = upstream.get("policy") or "weighted_round_robin"
    if policy == "weighted_round_robin":
        handler["load_balancing"] = {
            "selection_policy": {"policy": "weighted_round_robin", "weights": weights}
        }
    else:
        handler["load_balancing"] = {"selection_policy": {"policy": policy}}

    retries = int(route.get("retries") or 0)
    if retries:
        handler["load_balancing"]["retries"] = retries
        # Without a try duration Caddy retries instantly and gives a failing
        # backend no time to be replaced, so a retry count on its own mostly
        # multiplies the error rate. Tying it to the route's timeout is the
        # conservative reading of what the operator asked for.
        handler["load_balancing"]["try_duration"] = seconds(route.get("timeout_seconds") or 30)

    health: dict[str, Any] = {}
    if upstream.get("max_fails"):
        health["passive"] = {
            "max_fails": int(upstream["max_fails"]),
            "fail_duration": seconds(upstream.get("fail_duration_seconds") or 30),
        }
    if upstream.get("health_path"):
        health["active"] = {
            "uri": upstream["health_path"],
            "interval": seconds(upstream.get("health_interval_seconds") or 30),
            "timeout": seconds(upstream.get("health_timeout_seconds") or 5),
            "expect_status": int(upstream.get("health_expect_status") or 200),
        }
    if health:
        handler["health_checks"] = health

    transport: dict[str, Any] = {"protocol": "http"}
    if upstream.get("dial_timeout_seconds"):
        transport["dial_timeout"] = seconds(upstream["dial_timeout_seconds"])
    if route.get("read_timeout_seconds"):
        transport["read_timeout"] = seconds(route["read_timeout_seconds"])
    if upstream.get("max_connections"):
        transport["max_conns_per_host"] = int(upstream["max_connections"])
    handler["transport"] = transport

    return handler


def _reduce_weights(weights: list[int]) -> list[int]:
    """Divide a weight vector by its greatest common divisor.

    Preserves every ratio exactly while keeping the round-robin cycle short.
    A cycle is the sum of the weights, and it is how many requests must pass
    before the distribution matches the intent — so 45:27:18:10 is the same
    split as 450:270:180:100 and reaches it ten times sooner.
    """
    if not weights:
        return weights
    divisor = reduce(gcd, weights)
    return [w // divisor for w in weights] if divisor > 1 else weights


def _maintenance_handler(status: int, body: str | None) -> dict[str, Any]:
    return _static_response(
        status,
        body or "This service is temporarily unavailable for maintenance.",
        {"Content-Type": ["text/plain; charset=utf-8"], "Retry-After": ["300"]},
    )


def _rate_limit_handler(limit: dict[str, Any]) -> dict[str, Any]:
    """The `caddy-ratelimit` handler.

    Only emitted when the gateway reports `CAPABILITY_RATE_LIMIT`. The key
    expression uses Caddy placeholders so the counter is kept per whatever the
    limit says it is keyed on.
    """
    keys = {
        "ip": "{http.request.remote.host}",
        "api_key": "{http.request.header.x-api-key}",
        "user": "{http.auth.user.id}",
        "route": "{http.request.uri.path}",
        "domain": "{http.request.host}",
        "client": "{http.request.remote.host}",
        "global": "static",
    }
    # `distributed` and `storage` are deliberately absent, not null. The
    # caddy-ratelimit handler carries `caddy:"...inline_key=module"` on its
    # storage field, so a JSON `null` there is not "no storage" — Caddy tries
    # to load a storage module out of it and provisioning fails with
    # "module name not specified with key 'module' in map[]". Omitting the key
    # gives the default in-memory store, which is what a single gateway wants.
    return {
        "handler": "rate_limit",
        "rate_limits": {
            f"janus_{limit['id']}": {
                "match": [{"path": [limit.get("path") or "/*"]}],
                "key": keys.get(limit.get("key", "ip"), keys["ip"]),
                "window": seconds(limit.get("window_seconds") or 60),
                "max_events": int(limit.get("limit") or 100),
            }
        },
        "log_key": False,
    }


# ---------------------------------------------------------------------------
# Route assembly
# ---------------------------------------------------------------------------


def _route_for(
    route: dict[str, Any],
    upstreams: dict[int, dict[str, Any]],
    *,
    warnings: list[str],
    capabilities: frozenset[str],
    rate_limits: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """One Janus route as one Caddy route, or `None` if it cannot be built."""
    inner: list[dict[str, Any]] = []

    # Per-route IP rules run first, inside the route's own subroute, so they
    # apply only to traffic that already matched this route.
    for rule in route.get("ip_rules", []):
        if rule["action"] == "block":
            inner.append(
                {
                    "match": [_ip_match([rule["cidr"]])],
                    "handle": [_static_response(403, "Forbidden.")],
                    "terminal": True,
                }
            )
    allow_ranges = [r["cidr"] for r in route.get("ip_rules", []) if r["action"] == "allow"]
    if allow_ranges:
        # Deny-by-default: anything *not* in the allowlist is refused. Written
        # as a negated matcher so the allowed case falls through to the real
        # handler rather than needing a duplicate of it.
        inner.append(
            {
                "match": [{"not": [_ip_match(allow_ranges)]}],
                "handle": [_static_response(403, "Forbidden.")],
                "terminal": True,
            }
        )

    for limit in rate_limits:
        if CAPABILITY_RATE_LIMIT in capabilities:
            inner.append({"handle": [_rate_limit_handler(limit)]})
        else:
            warnings.append(
                f"Rate limit '{limit['name']}' is not enforced: this Caddy build has no "
                f"{CAPABILITY_RATE_LIMIT} module."
            )

    if route.get("auth_policy") and route["auth_policy"] != "public":
        # Enforced at the edge as a presence check on the credential; the
        # credential's *validity* is the upstream's business or the API-key
        # matcher's, depending on policy. Janus does not put itself in the
        # request path to verify a token — that would make the control plane a
        # dependency of every request, which is the thing this architecture is
        # organised to avoid.
        header = "x-api-key" if route["auth_policy"] == "api_key" else "authorization"
        inner.append(
            {
                "match": [{"not": [{"header": {header: ["*"]}}]}],
                "handle": [
                    _static_response(
                        401,
                        "Credentials required.",
                        {"WWW-Authenticate": ['Bearer realm="janus"']},
                    )
                ],
                "terminal": True,
            }
        )

    rewrite = _rewrite_handler(route)
    if rewrite:
        inner.append({"handle": [rewrite]})

    headers = _header_handler(route)
    if headers:
        inner.append({"handle": [headers]})

    action = route.get("action", "proxy")

    if route.get("maintenance"):
        inner.append(
            {
                "handle": [
                    _maintenance_handler(
                        route.get("maintenance_status") or 503,
                        route.get("maintenance_body"),
                    )
                ]
            }
        )
    elif action == "redirect":
        if not route.get("redirect_to"):
            warnings.append(f"Route '{route['name']}' redirects but has no target; skipped.")
            return None
        inner.append(
            {
                "handle": [
                    _static_response(
                        route.get("redirect_status") or 302,
                        None,
                        {"Location": [route["redirect_to"]]},
                    )
                ]
            }
        )
    elif action == "static":
        inner.append(
            {
                "handle": [
                    _static_response(
                        route.get("static_status") or 200, route.get("static_body") or ""
                    )
                ]
            }
        )
    elif action == "maintenance":
        inner.append({"handle": [_maintenance_handler(503, route.get("static_body"))]})
    else:
        upstream = upstreams.get(route.get("upstream_id") or -1)
        if upstream is None:
            warnings.append(f"Route '{route['name']}' has no upstream; skipped.")
            return None
        if upstream.get("maintenance"):
            inner.append({"handle": [_maintenance_handler(503, None)]})
        elif not [t for t in upstream.get("targets", []) if t.get("enabled", True)]:
            warnings.append(
                f"Upstream '{upstream['name']}' has no enabled targets; "
                f"route '{route['name']}' will answer 503."
            )
            inner.append(
                {"handle": [_static_response(503, "No healthy backend is available.")]}
            )
        else:
            inner.append(
                {
                    "handle": [
                        _upstream_handler(
                            upstream,
                            route=route,
                            canary=upstreams.get(route.get("canary_upstream_id") or -1),
                            canary_percent=int(route.get("canary_percent") or 0),
                        )
                    ]
                }
            )

    return {
        "match": [_match_for_route(route)],
        "handle": [{"handler": "subroute", "routes": inner}],
        # Terminal, so the first matching Janus route wins and the walk stops.
        # Without this every subsequent route's matcher is still evaluated and
        # a broad catch-all further down can append its own response.
        "terminal": True,
    }


def build_server(
    *,
    gateway: dict[str, Any],
    routes: list[dict[str, Any]],
    upstreams: list[dict[str, Any]],
    ip_rules: list[dict[str, Any]],
    rate_limits: list[dict[str, Any]],
    policies: list[dict[str, Any]],
    domains: list[dict[str, Any]] | None = None,
    capabilities: frozenset[str] = frozenset(),
    logger_name: str = "janus",
) -> GeneratedConfig:
    """The complete Janus-owned Caddy server object.

    Args:
        gateway: Its listen address and maintenance state.
        routes: Desired routes, each already carrying its host, methods and
            scoped IP rules. Ordering is done here, not by the caller.
        upstreams: Pools with their targets.
        ip_rules: Rules scoped globally or to a domain. Route-scoped rules
            travel on the route.
        rate_limits: Limits scoped globally or to a domain.
        policies: Security policies, highest priority first.
        domains: The hostnames this gateway serves and their TLS mode. This is
            what decides automatic HTTPS — see :func:`_automatic_https`.
        capabilities: Handler modules this gateway's build provides.
        logger_name: The access logger Janus reads. Must match
            :func:`build_logging`.
    """
    by_id = {u["id"]: u for u in upstreams}
    warnings: list[str] = []
    caddy_routes: list[dict[str, Any]] = []

    # 1. Global blocks. First, so a blocked address never reaches a policy, a
    #    rate limiter or an upstream.
    global_blocks = [r["cidr"] for r in ip_rules if r["action"] == "block" and not r.get("domain")]
    if global_blocks:
        caddy_routes.append(
            {
                "match": [_ip_match(global_blocks)],
                "handle": [_static_response(403, "Forbidden.")],
                "terminal": True,
            }
        )

    # 2. Per-domain blocks.
    for rule in ip_rules:
        if rule["action"] == "block" and rule.get("domain"):
            caddy_routes.append(
                {
                    "match": [{"host": [rule["domain"]], **_ip_match([rule["cidr"]])}],
                    "handle": [_static_response(403, "Forbidden.")],
                    "terminal": True,
                }
            )

    # 3. Global allowlist, as a fence: if any global allow rule exists, traffic
    #    from outside every allowed range is refused.
    global_allows = [r["cidr"] for r in ip_rules if r["action"] == "allow" and not r.get("domain")]
    if global_allows:
        caddy_routes.append(
            {
                "match": [{"not": [_ip_match(global_allows)]}],
                "handle": [_static_response(403, "Forbidden.")],
                "terminal": True,
            }
        )

    # 4. Policies, highest priority first.
    for policy in sorted(policies, key=lambda p: -int(p.get("priority", 0))):
        built = _policy_route(policy, warnings=warnings, capabilities=capabilities)
        if built is not None:
            caddy_routes.append(built)

    # 5. Gateway-wide maintenance, before any proxying.
    if gateway.get("maintenance"):
        caddy_routes.append(
            {
                "handle": [
                    _maintenance_handler(
                        gateway.get("maintenance_status") or 503,
                        gateway.get("maintenance_body"),
                    )
                ],
                "terminal": True,
            }
        )

    # 6. The routes themselves.
    ordered = sorted(routes, key=lambda r: (-int(r.get("priority", 0)), r["id"]))
    emitted = 0
    for route in ordered:
        if not route.get("enabled", True):
            continue
        scoped_limits = [
            limit
            for limit in rate_limits
            if limit.get("route_id") == route["id"] and limit.get("enabled", True)
        ]
        built = _route_for(
            route,
            by_id,
            warnings=warnings,
            capabilities=capabilities,
            rate_limits=scoped_limits,
        )
        if built is not None:
            caddy_routes.append(built)
            emitted += 1

    payload: dict[str, Any] = {
        "listen": [gateway.get("listen") or ":8080"],
        "routes": caddy_routes,
        # Every request is logged through Janus's own logger, which is what the
        # collector reads. `build_logging` defines where it writes.
        "logs": {"default_logger_name": logger_name},
        # Caddy defaults to `X-Forwarded-For` from any peer, which makes
        # `client_ip` — and therefore every block and allow rule — trivially
        # spoofable when the gateway is directly reachable. Janus states the
        # trusted set explicitly and empty by default: with no trusted proxies,
        # `client_ip` is the real peer and a forged header is ignored.
        "trusted_proxies": {"source": "static", "ranges": gateway.get("trusted_proxies") or []},
        "automatic_https": _automatic_https(domains or []),
    }

    result = GeneratedConfig(payload)
    result.warnings = warnings
    result.counts = {
        "routes": emitted,
        "upstreams": len([u for u in upstreams if u.get("enabled", True)]),
        "caddy_routes": len(caddy_routes),
    }
    return result


def _policy_route(
    policy: dict[str, Any], *, warnings: list[str], capabilities: frozenset[str]
) -> dict[str, Any] | None:
    """One security policy as one Caddy route.

    Returns `None` for policies with no edge representation — a `log` action
    needs no route, because every request is logged anyway.
    """
    if not policy.get("enabled", True):
        return None

    # `operator` is deliberately not read: every subject below has exactly one
    # way to be matched at the edge, so the operator is descriptive in the UI
    # rather than something the generator branches on.
    subject, value = policy["subject"], policy["value"]

    match: dict[str, Any] = {}
    if subject in {"ip", "cidr", "client"}:
        match = _ip_match([value])
    elif subject == "path":
        match = {"path": [value]}
    elif subject == "method":
        match = {"method": [value.upper()]}
    elif subject == "header":
        name, _, expected = value.partition(":")
        match = {"header": {name.strip().lower(): [expected.strip() or "*"]}}
    elif subject == "api_key":
        match = {"header": {"x-api-key": [value]}}
    else:
        warnings.append(f"Policy '{policy['name']}' matches on '{subject}', which the "
                        "gateway cannot express; not emitted.")
        return None

    if policy.get("domain"):
        match["host"] = [policy["domain"]]

    action = policy["action"]
    if action == "block":
        handle = [_static_response(403, "Forbidden.")]
    elif action == "allow":
        # An explicit allow is a no-op handler that terminates the walk before
        # any later block can see the request. That *is* the semantics of an
        # allow rule ordered above a block, and expressing it as an empty
        # subroute keeps it visible in `config show`.
        handle = [{"handler": "subroute", "routes": []}]
        return {"match": [match], "handle": handle, "terminal": False}
    elif action == "require_auth":
        handle = [_static_response(401, "Credentials required.")]
        match = {**match, "not": [{"header": {"authorization": ["*"]}}]}
    elif action == "rate_limit":
        if CAPABILITY_RATE_LIMIT not in capabilities:
            warnings.append(
                f"Policy '{policy['name']}' rate-limits, but this Caddy build has no "
                f"{CAPABILITY_RATE_LIMIT} module; not enforced."
            )
            return None
        handle = [_rate_limit_handler(policy["rate_limit"])]
    else:
        return None

    return {"match": [match], "handle": handle, "terminal": True}


def build_logging(*, logger_name: str, log_path: str) -> dict[str, Any]:
    """The named logger the access-log collector reads.

    JSON encoding, because the collector parses it and a human-readable console
    format would need a parser that guesses. `include` names Janus's own logger
    so this configuration cannot start capturing Caddy's admin or TLS logs — it
    is scoped as narrowly as the server object is.
    """
    return {
        "writer": {"output": "file", "filename": log_path, "roll": True, "roll_size_mb": 64},
        "encoder": {"format": "json"},
        "include": [f"http.log.access.{logger_name}"],
        "level": "INFO",
    }


def _automatic_https(domains: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether Caddy provisions certificates, decided by Janus rather than inferred.

    This has to be explicit, and the reason is a trap worth naming. Caddy turns
    on automatic HTTPS as soon as a server has routes with `host` matchers, and
    then answers plain HTTP on the listen port with *"Client sent an HTTP
    request to an HTTPS server."* So a gateway on `:8080` with one hostname
    route silently stops speaking HTTP — nothing in the Janus configuration
    said anything about TLS, and every request fails with a 400.

    So the domain table decides:

    * no domains, or every domain `tls_mode="off"` → automatic HTTPS off, the
      gateway speaks plain HTTP. This is the laptop and the behind-a-load-
      balancer case.
    * any domain wanting `auto` or `internal` → automatic HTTPS on, with the
      `off` hostnames listed in `skip` so they keep working over HTTP.

    `internal` additionally means Caddy's local CA rather than a public issuer,
    which is what a private network wants and what a public one must not have.
    """
    wanting_tls = [d for d in domains if d.get("enabled", True) and d.get("tls_mode") in
                   {"auto", "internal", "custom"}]
    if not wanting_tls:
        return {"disable": True}

    skip = [
        d["hostname"]
        for d in domains
        if d.get("enabled", True) and d.get("tls_mode") == "off"
    ]
    policy: dict[str, Any] = {"disable": False}
    if skip:
        policy["skip"] = skip
    return policy
