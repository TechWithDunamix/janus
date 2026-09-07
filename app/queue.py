"""The job queue connection.

Memory on a laptop, Redis when `QUEUE_BACKEND=redis`. The binding step is not
optional: without :func:`bind_jobs`, `Job.dispatch` raises "call on_connection
first" and every background job silently does nothing.
"""

from __future__ import annotations

from typing import Any

from app.config import config

__all__ = ["bind_jobs", "connection", "queue_url"]

_connection: Any = None


def queue_url() -> str | None:
    """The Redis URL, or `None` for the in-memory backend."""
    return config.redis_url if config.queue_backend == "redis" else None


def connection() -> Any:
    """The shared queue connection, created on first use."""
    global _connection
    if _connection is None:
        from sillo.work.queue import RedisConnection, SyncConnection

        url = queue_url()
        _connection = RedisConnection(url) if url else SyncConnection()
    return _connection


def bind_jobs() -> None:
    """Bind every job class to the connection.

    Called from a startup hook and from the CLI, because a worker process does
    not run the application's startup hooks and would otherwise dispatch into
    nothing.
    """
    from app.jobs import ALL_JOBS

    conn = connection()
    for job in ALL_JOBS:
        job.on_connection(conn)
