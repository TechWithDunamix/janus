"""Where Janus's files live on disk.

Janus keeps its Python packages in ``src/``::

    <project>/
        src/main.py, core/, database/, routes/api/, cli/, modules/
        storage/             default SQLite database and local state

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
#: Python packages.
SRC_DIR = BASE_DIR / "src"
#: Local state: the default SQLite database lives here.
STORAGE_DIR = BASE_DIR / "storage"
