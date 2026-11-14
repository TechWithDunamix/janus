"""The ASGI entry point.

    uvicorn main:app --reload --port 8000

Nothing but the factory call lives here, so the import stays cheap and there is
exactly one place — `core/bootstrap.py` — where the application is assembled.
"""

from __future__ import annotations

from core.bootstrap import create_app

app = create_app()
