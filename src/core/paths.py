"""Where Janus's files live on disk.

Janus keeps its Python packages and its front end side by side in ``src/``::

    <project>/
        src/
            app.html          Inertia root HTML view
            js/  views/       React front end (built by Vite)
            public/           static files served at /static
            main.py  core/  database/  routes/  cli/  modules/
        static/build/         compiled front end (`bun run build`)
        storage/              SQLite database, access logs, collector offsets

:data:`BASE_DIR` is ``<project>``, resolved in this order:

1. ``$JANUS_ROOT`` when set — for installs where the code is not run from
   the project directory.
2. The nearest parent of this file holding both ``pyproject.toml`` and
   ``src/main.py`` — a git checkout (``pip install -e .``, the supported install).
3. The current working directory — what systemd's ``WorkingDirectory=`` sets.
"""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    """Return the Janus project directory (see the module docstring for the order)."""
    explicit = os.getenv("JANUS_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "main.py").is_file():
            return parent
    return Path.cwd().resolve()


#: The project directory every relative runtime path is resolved against.
BASE_DIR = project_root()
#: Python packages, the React sources, the root view and public files.
SRC_DIR = BASE_DIR / "src"
#: Local state: the default SQLite database lives here.
STORAGE_DIR = BASE_DIR / "storage"
#: Static files served as-is at ``/static`` (favicon and similar).
PUBLIC_DIR = SRC_DIR / "public"
#: Vite's output directory; production serves ``assets/`` from here.
BUILD_DIR = BASE_DIR / "static" / "build"
