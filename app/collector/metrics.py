"""Caddy's Prometheus metrics, for aggregate counters.

Metrics and access logs answer different questions and Janus uses both for what
each is good at. Metrics are cheap, always available, and already aggregated by
Caddy — they are the right source for "how many requests has this gateway
served" and "how many are in flight right now". They cannot answer "which
client got the 502s", because a counter has no room for a client in it. That is
what the access log is for.

Deliberately a small parser rather than a Prometheus client library. Janus
reads a handful of Caddy's own metric families from one endpoint; pulling in a
metrics library to do that would add a dependency to the deployment for the
sake of a function that fits on a screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["GatewaySnapshot", "parse_prometheus", "summarise"]


@dataclass
class GatewaySnapshot:
    """Aggregate counters as Caddy currently reports them.

    Every field is a *counter* since the process started, not a rate. Turning
    them into rates is the caller's job and needs two readings; presenting a
    counter as though it were a rate is one of the easier ways to put a wrong
    number on a dashboard.
    """

    requests_total: float = 0.0
    request_errors_total: float = 0.0
    requests_in_flight: float = 0.0
    #: Status-code counts, keyed by the code as a string.
    by_status: dict[str, float] = field(default_factory=dict)
    #: Sum and count of request durations, for a mean. Caddy exposes a
    #: histogram; the sum/count pair is what survives being read once.
    duration_sum: float = 0.0
    duration_count: float = 0.0
    available: bool = False

    @property
    def mean_latency_ms(self) -> float:
        if not self.duration_count:
            return 0.0
        return (self.duration_sum / self.duration_count) * 1000.0


def parse_prometheus(text: str) -> dict[str, list[tuple[dict[str, str], float]]]:
    """Parse an exposition into `{metric: [(labels, value)]}`.

    Handles the subset Caddy emits: comments, a metric name, optional labels in
    braces, and a float. Anything it cannot read is skipped rather than raised —
    a metrics endpoint that grows a format this does not understand should
    degrade to fewer numbers, not to an exception on the dashboard.
    """
    out: dict[str, list[tuple[dict[str, str], float]]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            head, _, value = line.rpartition(" ")
            if not head:
                continue
            number = float(value)
        except ValueError:
            continue

        if "{" in head:
            name, _, rest = head.partition("{")
            labels = _parse_labels(rest.rstrip("}"))
        else:
            name, labels = head, {}
        out.setdefault(name.strip(), []).append((labels, number))
    return out


def _parse_labels(text: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for part in _split_labels(text):
        key, _, value = part.partition("=")
        if key:
            labels[key.strip()] = value.strip().strip('"')
    return labels


def _split_labels(text: str) -> list[str]:
    """Split on commas that are not inside a quoted value.

    A label value may contain a comma — a path or a user agent routinely does —
    and splitting naively turns one label into two malformed ones.
    """
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    for char in text:
        if char == '"':
            quoted = not quoted
        if char == "," and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def summarise(text: str, server_name: str = "janus") -> GatewaySnapshot:
    """Fold a Caddy exposition into the counters Janus displays.

    Scoped to one server by the `server` label, because a Caddy instance Janus
    shares with another tenant would otherwise report that tenant's traffic as
    part of the gateway's totals.
    """
    snapshot = GatewaySnapshot()
    if not text.strip():
        return snapshot
    snapshot.available = True
    metrics = parse_prometheus(text)

    def scoped(name: str) -> list[tuple[dict[str, str], float]]:
        return [
            (labels, value)
            for labels, value in metrics.get(name, [])
            if labels.get("server", server_name) == server_name
        ]

    for labels, value in scoped("caddy_http_requests_total"):
        snapshot.requests_total += value
        code = labels.get("code") or labels.get("status")
        if code:
            snapshot.by_status[code] = snapshot.by_status.get(code, 0.0) + value

    for _, value in scoped("caddy_http_request_errors_total"):
        snapshot.request_errors_total += value
    for _, value in scoped("caddy_http_requests_in_flight"):
        snapshot.requests_in_flight += value
    for _, value in scoped("caddy_http_request_duration_seconds_sum"):
        snapshot.duration_sum += value
    for _, value in scoped("caddy_http_request_duration_seconds_count"):
        snapshot.duration_count += value

    return snapshot
