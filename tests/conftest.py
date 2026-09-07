"""Test fixtures.

Every test runs against a fresh in-memory SQLite database, and the application
under test is the one `create_app` builds — not a stripped-down variant. A
harness that assembles the application differently is a harness that can pass
while the real thing is broken.

**Why the client is httpx rather than `sillo.testclient.TestClient`.**
`TestClient` runs the application's lifespan on its own event loop through a
portal thread. The database fixture initialises Tortoise on *pytest-asyncio's*
loop, so a request handler would then be using a connection bound to a
different loop from the one it is running on — which with SQLite means a
different in-memory database entirely, and in practice a hang rather than a
clear error. Driving the ASGI app directly with `httpx.ASGITransport` keeps
everything on one loop.

The cost is that the application's startup hooks do not run. That is deliberate
and the two that matter are covered: the database fixture initialises Tortoise
itself, and it calls `ensure_roles()` — the same function the startup hook
calls.
"""

from __future__ import annotations

import os

import pytest

# Set before anything imports `app.config`, which resolves settings at import.
os.environ.setdefault("DATABASE_URL", "sqlite://:memory:")
os.environ.setdefault("CADDY_SIMULATE", "true")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("VITE_DEV", "false")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DB_GENERATE_SCHEMAS", "false")

from tortoise import Tortoise  # noqa: E402

from app.authz import ensure_roles  # noqa: E402
from database.config import MODEL_MODULES  # noqa: E402


@pytest.fixture(autouse=True)
async def database():
    """A fresh in-memory database for every test."""
    await Tortoise.init(db_url="sqlite://:memory:", modules={"models": MODEL_MODULES})
    await Tortoise.generate_schemas()
    await ensure_roles()
    yield
    await Tortoise._drop_databases()
    await Tortoise.close_connections()


@pytest.fixture
def app():
    """The application, assembled by the same factory the server uses."""
    from app.bootstrap import create_app

    return create_app()


@pytest.fixture
async def client(app):
    """An HTTP client speaking to the application in-process.

    Redirects are never followed automatically: half the assertions in the
    suite are about *where* a redirect points, and a client that followed them
    would turn "refused and sent to /login" into an indistinguishable 200.
    """
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as http_client:
        yield http_client


@pytest.fixture
async def gateway():
    from tests.helpers import make_gateway

    return await make_gateway()


@pytest.fixture
async def owner():
    from tests.helpers import make_user

    return await make_user("owner@test.local", role="Owner", superuser=True)
