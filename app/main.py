"""The ASGI entry point.

    uvicorn app.main:app --reload --port 8000

Nothing but the factory call lives here, so the import stays cheap and there is
exactly one place — `app/bootstrap.py` — where the application is assembled.
"""

from __future__ import annotations

from app.bootstrap import create_app

app = create_app()
