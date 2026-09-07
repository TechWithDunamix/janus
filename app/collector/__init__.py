"""Ingestion: getting what happened at the edge into Janus.

Two sources, because Caddy has two and they answer different questions.
:mod:`app.collector.metrics` reads aggregate counters from the Prometheus
endpoint; :mod:`app.collector.access_log` reads request-level records from the
JSON access log. Neither invents a number, and where a source is unavailable
the answer is "unavailable" rather than a zero that looks like quiet traffic.
"""

from app.collector.access_log import (
    CollectorState,
    ingest_file,
    ingest_lines,
    load_state,
    parse_line,
    save_state,
)
from app.collector.metrics import GatewaySnapshot, parse_prometheus, summarise

__all__ = [
    "CollectorState",
    "GatewaySnapshot",
    "ingest_file",
    "ingest_lines",
    "load_state",
    "parse_line",
    "parse_prometheus",
    "save_state",
    "summarise",
]
