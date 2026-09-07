"""Turning Caddy's access log into request-level analytics.

Caddy writes one JSON object per request to the file Janus configured in
`build_logging`. This module reads that file forward from wherever it last
stopped, parses each line, attributes it to a route and a client, and folds it
into the per-minute rollups the dashboard actually queries.

**What is deliberately dropped.** The log contains full request and response
headers. Everything except a small allowlist is discarded before a row is
written: no `Authorization`, no `Cookie`, no `X-Api-Key` value — the key is
reduced to its prefix so a request can be attributed to a key without the key
being readable in a table the whole team can see. That filtering happens here,
at the boundary, rather than at display time, because a secret that reaches the
database is already leaked.

**Why it tails rather than streams.** Caddy has no push mechanism for access
logs. Tailing a rolled file with a stored offset is the mechanism that works
with a Caddy on another host and a shared volume, needs no message broker, and
survives Janus being down — the log keeps being written and the next run
catches up.
"""

from __future__ import annotations

import contextlib
import json as jsonlib
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from database.models import (
    LATENCY_BUCKETS_MS,
    ApiKey,
    Client,
    Gateway,
    GatewayRoute,
    RequestLog,
    RouteRollup,
    bucket_for,
)

__all__ = ["CollectorState", "ingest_file", "ingest_lines", "parse_line"]

#: Request headers worth keeping. Everything else is dropped — including every
#: header that could carry a credential.
KEPT_REQUEST_HEADERS = ("User-Agent", "Referer")

#: Header names whose *presence* is recorded but whose value never is.
CREDENTIAL_HEADERS = frozenset({"authorization", "cookie", "x-api-key", "proxy-authorization"})


@dataclass
class CollectorState:
    """Where the reader stopped, so the next run resumes rather than replays.

    The inode is stored alongside the offset because log rotation replaces the
    file: an offset of 4 MB into a freshly rolled file would skip the first 4 MB
    of new requests. When the inode changes, the offset resets to zero.
    """

    path: str
    offset: int = 0
    inode: int | None = None
    #: Lines that were not valid JSON or not an access record. Counted rather
    #: than raised: one malformed line must not stop ingestion of the file.
    skipped: int = 0
    ingested: int = 0
    warnings: list[str] = field(default_factory=list)


def parse_line(line: str) -> dict[str, Any] | None:
    """One Caddy access-log line as a flat record, or `None` to skip it.

    Returns `None` for anything that is not a `handled request` entry — the
    same file carries Caddy's own informational lines when the logger is
    misconfigured, and treating those as requests would invent traffic.
    """
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        entry = jsonlib.loads(line)
    except ValueError:
        return None
    if entry.get("msg") != "handled request":
        return None

    request = entry.get("request") or {}
    headers = request.get("headers") or {}

    def header(name: str) -> str | None:
        value = headers.get(name) or headers.get(name.lower()) or headers.get(name.title())
        if isinstance(value, list):
            return str(value[0])[:400] if value else None
        return str(value)[:400] if value else None

    # `ts` is a float of epoch seconds. Caddy can be configured to emit RFC3339
    # instead; both are handled because a Caddy Janus did not configure — one
    # already running with its own logging — may well be doing the latter.
    raw_ts = entry.get("ts")
    if isinstance(raw_ts, int | float):
        occurred = datetime.fromtimestamp(float(raw_ts), tz=UTC)
    elif isinstance(raw_ts, str):
        try:
            occurred = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    uri = request.get("uri") or "/"
    path = uri.split("?", 1)[0]

    api_key_header = headers.get("X-Api-Key") or headers.get("x-api-key")
    if isinstance(api_key_header, list):
        api_key_header = api_key_header[0] if api_key_header else None

    return {
        "occurred_at": occurred,
        "method": (request.get("method") or "GET").upper()[:10],
        "host": (request.get("host") or "")[:255],
        "path": path[:1000],
        "status": int(entry.get("status") or 0),
        # Caddy reports duration in seconds as a float.
        "latency_ms": float(entry.get("duration") or 0.0) * 1000.0,
        "bytes_in": int(entry.get("bytes_read") or 0),
        "bytes_out": int(entry.get("size") or 0),
        # `client_ip` respects trusted-proxy configuration; `remote_ip` is the
        # immediate peer. Preferring the former means the numbers agree with
        # the blocklist, which matches on the same value.
        "client_ip": (request.get("client_ip") or request.get("remote_ip") or "")[:64],
        "user_agent": header("User-Agent"),
        "referer": header("Referer"),
        "protocol": (request.get("proto") or "")[:16] or None,
        # Only whether a credential was presented, never what it was.
        "had_credential": any(h in CREDENTIAL_HEADERS for h in (k.lower() for k in headers)),
        # The prefix is enough to attribute traffic to a key and useless for
        # authenticating as one. Sliced to `ApiKey.PREFIX_LENGTH`, which is the
        # same length the issuer stores — anything else and every lookup misses
        # silently.
        "api_key_prefix": (api_key_header or "")[: ApiKey.PREFIX_LENGTH] or None,
    }


class _Matcher:
    """Attributes a parsed record to a route, in memory.

    Route matching is done here rather than by asking the database per request
    because ingestion processes thousands of rows at a time and a query per row
    would dominate the run. The rules mirror Caddy's own precedence — host
    first, then longest path, then method — so an attribution disagrees with
    the gateway only where Caddy's matcher is doing something this cannot
    express, which the route model does not permit anyway.
    """

    def __init__(self, routes: list[GatewayRoute], hosts: dict[int, str]) -> None:
        self.routes = sorted(routes, key=lambda r: (-r.priority, -len(r.path or ""), r.pk))
        self.hosts = hosts

    def match(self, record: dict[str, Any]) -> GatewayRoute | None:
        for route in self.routes:
            host = self.hosts.get(route.domain_id) if route.domain_id else None
            if host and host != record["host"]:
                continue
            if not _path_matches(route.path or "/*", record["path"]):
                continue
            methods = route.method_list
            if methods and record["method"] not in methods:
                continue
            return route
        return None


def _path_matches(pattern: str, path: str) -> bool:
    """Caddy's path matcher, for the two forms Janus routes use.

    A trailing `*` is a prefix match; anything else is exact. Caddy supports
    more, but the route model only ever produces these two, so implementing
    more here would be implementing a matcher for configurations that cannot
    exist.
    """
    if pattern.endswith("*"):
        return path.startswith(pattern[:-1])
    return path == pattern


async def ingest_lines(
    gateway: Gateway,
    lines: list[str],
    *,
    simulated: bool = False,
) -> tuple[int, int]:
    """Parse and store a batch of log lines.

    Returns `(ingested, skipped)`. Everything is written in bulk: one
    `bulk_create` for the request rows and one upsert pass over the touched
    rollup buckets, because the alternative is two queries per request and an
    ingestion that cannot keep up with a gateway doing anything interesting.
    """
    routes = await GatewayRoute.filter(gateway_id=gateway.pk)
    domains = {d.pk: d.hostname for d in await gateway.domains.all()}
    matcher = _Matcher(routes, domains)

    keys = {k.prefix: k for k in await ApiKey.all()}
    clients: dict[str, Client] = {}
    rows: list[RequestLog] = []
    #: (route_id, minute) → accumulating counters, folded once at the end.
    buckets: dict[tuple[int | None, datetime], dict[str, Any]] = {}
    ingested = skipped = 0

    for line in lines:
        record = parse_line(line)
        if record is None:
            skipped += 1
            continue

        route = matcher.match(record)
        client = await _client_for(record["client_ip"], clients)
        api_key = keys.get(record["api_key_prefix"]) if record["api_key_prefix"] else None

        rows.append(
            RequestLog(
                gateway_id=gateway.pk,
                route_id=route.pk if route else None,
                upstream_id=route.upstream_id if route else None,
                client_id=client.pk if client else None,
                api_key_id=api_key.pk if api_key else None,
                occurred_at=record["occurred_at"],
                method=record["method"],
                host=record["host"],
                path=record["path"],
                status=record["status"],
                latency_ms=record["latency_ms"],
                bytes_in=record["bytes_in"],
                bytes_out=record["bytes_out"],
                client_ip=record["client_ip"],
                user_agent=record["user_agent"],
                referer=record["referer"],
                protocol=record["protocol"],
                edge_action=_edge_action(record, route),
                simulated=simulated,
            )
        )
        _fold(buckets, route.pk if route else None, record)
        ingested += 1

    if rows:
        await RequestLog.bulk_create(rows, batch_size=500)
    await _write_rollups(gateway, buckets, simulated=simulated)
    await _touch_clients(clients)
    return ingested, skipped


def _edge_action(record: dict[str, Any], route: GatewayRoute | None) -> str:
    """What the edge did, inferred from status and route policy.

    Inference, and labelled as such: Caddy's access log records the status it
    returned, not which handler produced it. A 403 from a Janus block rule and
    a 403 the upstream returned look identical in the log. The distinction that
    *can* be made honestly is that a request which never reached an upstream —
    no route matched, or the route has an auth policy and the status is 401 —
    was handled at the edge.
    """
    status = record["status"]
    if status == 429:
        return "rate_limited"
    if status == 403 and route is None:
        return "blocked"
    if status == 401 and route is not None and route.auth_policy != "public":
        return "unauthorized"
    if status == 503 and route is not None and route.maintenance:
        return "maintenance"
    return ""


async def _client_for(ip: str, cache: dict[str, Client]) -> Client | None:
    if not ip:
        return None
    if ip in cache:
        return cache[ip]
    client, _ = await Client.get_or_create(
        kind="ip",
        identifier=ip,
        defaults={"first_seen_at": datetime.now(UTC), "last_seen_at": datetime.now(UTC)},
    )
    cache[ip] = client
    return client


def _fold(
    buckets: dict[tuple[int | None, datetime], dict[str, Any]],
    route_id: int | None,
    record: dict[str, Any],
) -> None:
    """Add one request into its per-minute bucket."""
    minute = record["occurred_at"].replace(second=0, microsecond=0)
    key = (route_id, minute)
    bucket = buckets.get(key)
    if bucket is None:
        bucket = buckets[key] = {
            "requests": 0,
            "status_1xx": 0, "status_2xx": 0, "status_3xx": 0,
            "status_4xx": 0, "status_5xx": 0,
            "status_401": 0, "status_403": 0, "status_404": 0,
            "status_429": 0, "status_502": 0, "status_503": 0, "status_504": 0,
            "blocked": 0, "rate_limited": 0, "timeouts": 0,
            "bytes_in": 0, "bytes_out": 0,
            "latency_sum_ms": 0.0, "latency_max_ms": 0.0,
            "latency_buckets": [0] * (len(LATENCY_BUCKETS_MS) + 1),
            "clients": set(),
        }

    status = record["status"]
    bucket["requests"] += 1
    bucket[f"status_{status // 100}xx"] = bucket.get(f"status_{status // 100}xx", 0) + 1
    if status in (401, 403, 404, 429, 502, 503, 504):
        bucket[f"status_{status}"] += 1
    if status == 429:
        bucket["rate_limited"] += 1
    if status == 403:
        bucket["blocked"] += 1
    if status == 504:
        bucket["timeouts"] += 1
    bucket["bytes_in"] += record["bytes_in"]
    bucket["bytes_out"] += record["bytes_out"]
    bucket["latency_sum_ms"] += record["latency_ms"]
    bucket["latency_max_ms"] = max(bucket["latency_max_ms"], record["latency_ms"])
    bucket["latency_buckets"][bucket_for(record["latency_ms"])] += 1
    bucket["clients"].add(record["client_ip"])


async def _write_rollups(
    gateway: Gateway,
    buckets: dict[tuple[int | None, datetime], dict[str, Any]],
    *,
    simulated: bool,
) -> None:
    """Merge folded counters into the rollup table.

    Read-modify-write rather than an upsert with SQL expressions, because the
    latency histogram is a JSON list that has to be summed element-wise and no
    portable SQL does that. The read is one query for the whole batch.
    """
    if not buckets:
        return

    minutes = sorted({minute for _, minute in buckets})
    existing = {
        (row.route_id, row.minute.replace(tzinfo=UTC) if row.minute.tzinfo is None else row.minute): row
        for row in await RouteRollup.filter(
            gateway_id=gateway.pk, minute__gte=minutes[0], minute__lte=minutes[-1]
        )
    }

    to_create: list[RouteRollup] = []
    for (route_id, minute), bucket in buckets.items():
        row = existing.get((route_id, minute))
        histogram = bucket["latency_buckets"]
        if row is None:
            to_create.append(
                RouteRollup(
                    gateway_id=gateway.pk,
                    route_id=route_id,
                    minute=minute,
                    requests=bucket["requests"],
                    status_1xx=bucket["status_1xx"], status_2xx=bucket["status_2xx"],
                    status_3xx=bucket["status_3xx"], status_4xx=bucket["status_4xx"],
                    status_5xx=bucket["status_5xx"],
                    status_401=bucket["status_401"], status_403=bucket["status_403"],
                    status_404=bucket["status_404"], status_429=bucket["status_429"],
                    status_502=bucket["status_502"], status_503=bucket["status_503"],
                    status_504=bucket["status_504"],
                    blocked=bucket["blocked"], rate_limited=bucket["rate_limited"],
                    timeouts=bucket["timeouts"],
                    bytes_in=bucket["bytes_in"], bytes_out=bucket["bytes_out"],
                    latency_sum_ms=bucket["latency_sum_ms"],
                    latency_max_ms=bucket["latency_max_ms"],
                    latency_buckets=histogram,
                    distinct_clients=len(bucket["clients"]),
                    simulated=simulated,
                )
            )
        else:
            for field_name in (
                "requests", "status_1xx", "status_2xx", "status_3xx", "status_4xx",
                "status_5xx", "status_401", "status_403", "status_404", "status_429",
                "status_502", "status_503", "status_504", "blocked", "rate_limited",
                "timeouts", "bytes_in", "bytes_out",
            ):
                setattr(row, field_name, getattr(row, field_name) + bucket[field_name])
            row.latency_sum_ms += bucket["latency_sum_ms"]
            row.latency_max_ms = max(row.latency_max_ms, bucket["latency_max_ms"])
            merged = list(row.latency_buckets or [0] * len(histogram))
            row.latency_buckets = [a + b for a, b in zip(merged, histogram, strict=False)]
            row.distinct_clients = max(row.distinct_clients, len(bucket["clients"]))
            await row.save()

    if to_create:
        await RouteRollup.bulk_create(to_create, batch_size=500)


async def _touch_clients(clients: dict[str, Client]) -> None:
    now = datetime.now(UTC)
    for client in clients.values():
        client.last_seen_at = now
        if client.first_seen_at is None:
            client.first_seen_at = now
        await client.save()


async def ingest_file(gateway: Gateway, state: CollectorState) -> CollectorState:
    """Read new lines from the access log and ingest them.

    Advances `state.offset` only after a successful ingest, so a crash
    mid-batch replays that batch rather than losing it. Replaying can duplicate
    request rows; that is the deliberate trade — for traffic analytics, a
    slight over-count after a crash is a smaller problem than a silent hole.
    """
    path = Path(state.path)
    if not path.is_file():
        state.warnings.append(f"No access log at {state.path}.")
        return state

    stat = path.stat()
    if state.inode is not None and stat.st_ino != state.inode:
        # Rotated. Start at the beginning of the new file.
        state.offset = 0
    state.inode = stat.st_ino

    if stat.st_size < state.offset:
        # Truncated in place.
        state.offset = 0
    if stat.st_size == state.offset:
        return state

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(state.offset)
        lines = handle.readlines()
        position = handle.tell()

    # A final line without a newline is a line Caddy is still writing. Leaving
    # it for the next pass is why the offset is rewound rather than trusted.
    if lines and not lines[-1].endswith("\n"):
        position -= len(lines[-1].encode("utf-8"))
        lines = lines[:-1]

    ingested, skipped = await ingest_lines(gateway, lines)
    state.offset = position
    state.ingested += ingested
    state.skipped += skipped
    return state


def state_path(gateway: Gateway) -> Path:
    """Where the collector's offset for a gateway is remembered."""
    from app.config import BASE_DIR

    return BASE_DIR / "storage" / f"collector-{gateway.pk}.json"


def load_state(gateway: Gateway, log_path: str) -> CollectorState:
    path = state_path(gateway)
    if path.is_file():
        with contextlib.suppress(ValueError, OSError):
            data = jsonlib.loads(path.read_text())
            return CollectorState(
                path=log_path, offset=int(data.get("offset", 0)), inode=data.get("inode")
            )
    return CollectorState(path=log_path)


def save_state(gateway: Gateway, state: CollectorState) -> None:
    path = state_path(gateway)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(jsonlib.dumps({"offset": state.offset, "inode": state.inode}))
    # Atomic replace: a crash between writing and renaming leaves the previous
    # offset, which replays a batch. A partial write would leave an offset that
    # is not a number and lose the position entirely.
    os.replace(tmp, path)
