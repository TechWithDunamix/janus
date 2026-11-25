"""Base commands: JSON output, confirmation, and the local/remote split.

Two base classes, and the difference between them is the whole security story
of the CLI:

* :class:`ApiCommand` performs its work through the Janus API, authenticated
  with the token from `janus login`. Authorization is the API's, the same
  for every client. There is no way for a command built on this to do something its
  user may not do.
* :class:`LocalCommand` opens the database directly. Only five commands do
  this — migrate, seed, serve, worker, scheduler — plus the bootstrap
  `admin create`, which refuses once an account exists.
"""

from __future__ import annotations

import asyncio
import json as jsonlib
import os
import re
import shutil
import sys
from collections.abc import Sequence
from typing import Any

from sillo.console import Command, Flag
from sillo.console.terminal import is_interactive

from cli.client import ApiClient, CliAuthRequired, CliError

__all__ = ["ApiCommand", "LocalCommand", "cell", "json_flag", "render_table"]

#: A timestamp as the API sends it; shown to the second.
ISO_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$")


def cell(value: Any, *, wide: bool = False) -> str:
    """One table cell: ``-`` for nothing, lists joined, timestamps shortened, long text cut."""
    if value is None or value == "" or value == []:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value)
    elif isinstance(value, dict):
        value = jsonlib.dumps(value, default=str)
    text = str(value)
    if ISO_TIME.match(text):
        text = text[:10] + " " + text[11:19]
    limit = 400 if wide else 60
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_table(headers: Sequence[str], body: Sequence[Sequence[str]], width: int | None = None) -> list[str]:
    """Aligned text lines for a table.

    On a terminal that is too narrow the widest column gives way first (short columns such as STATUS stay
    readable); piped output is never cut, so scripts see every value.
    """
    widths = [max([len(h)] + [len(r[i]) for r in body]) for i, h in enumerate(headers)]
    if width is None and sys.stdout.isatty():
        width = shutil.get_terminal_size((120, 24)).columns
    if width:
        room = width - 1 - 2 * (len(widths) - 1)
        while sum(widths) > room and max(widths) > 8:
            widths[widths.index(max(widths))] -= 1

    def fit(text: str, size: int) -> str:
        return (text if len(text) <= size else text[: size - 1] + "…").ljust(size)

    lines = ["  ".join(fit(h, w) for h, w in zip(headers, widths, strict=True)).rstrip(),
             "  ".join("─" * w for w in widths)]
    lines += ["  ".join(fit(c, w) for c, w in zip(row, widths, strict=True)).rstrip() for row in body]
    return lines


def json_flag() -> Flag:
    """The shared ``--json`` flag."""
    return Flag("json", help="Emit machine-readable JSON instead of a table.")


class _Base(Command):
    """Shared output helpers."""

    @property
    def wants_json(self) -> bool:
        """Whether ``--json`` was passed."""
        return bool(self.flag("json"))

    def emit(self, payload: Any) -> None:
        """Print JSON. Used by every command's `--json` path."""
        self.line(jsonlib.dumps(payload, indent=2, default=str))

    def table(self, headers: Sequence[str], rows: Sequence[Sequence[Any]], align: Any = None) -> None:
        """Draw a table (see :func:`render_table`)."""
        for line in render_table(list(headers), [[str(c) for c in row] for row in rows]):
            self.line(line)

    def rows(self, rows: list[dict], columns: list[tuple[str, str]], empty: str = "Nothing yet.") -> int:
        """Print ``rows`` as a table of ``columns`` (``(heading, key)``), or JSON with ``--json``."""
        if self.wants_json:
            self.emit(rows)
            return 0
        if not rows:
            self.muted(empty)
            return 0
        self.table([h for h, _ in columns], [[cell(row.get(k)) for _, k in columns] for row in rows])
        return 0

    def show(self, item: dict, keys: list[tuple[str, str]] | None = None) -> int:
        """Print one object as aligned ``label  value`` pairs, or JSON with ``--json``."""
        if self.wants_json:
            self.emit(item)
            return 0
        pairs = keys or [(k.replace("_", " "), k) for k in item]
        self.pairs([(label, cell(item.get(k), wide=True)) for label, k in pairs])
        return 0

    def fail_with(self, error: Exception) -> int:
        """Report a failure the way the CLI reports every failure.

        JSON mode still gets JSON — a script that set `--json` and then has to
        parse a human sentence out of stderr is a script that will not.
        """
        message = str(error)
        if self.wants_json:
            self.emit({"error": message})
        else:
            for line in message.splitlines():
                self.error(line)
        return 1

    def read_password(self, question: str = "Password", *, confirm: bool = False) -> str:
        """A hidden prompt, or ``$JANUS_PASSWORD`` when set (CI and scripts, where there is no terminal).

        Never a flag: a password on the command line ends up in shell history and ``ps``.
        """
        value = os.environ.get("JANUS_PASSWORD")
        if value:
            return value
        if not is_interactive():
            raise CliError("No terminal to read a password from. Set JANUS_PASSWORD instead.")
        return self.secret(question, confirm=confirm)

    def confirm_destructive(self, question: str) -> bool:
        """Ask before something irreversible, unless `--yes` was passed.

        In a non-interactive shell without `--yes` this refuses rather than
        assuming yes. A pipeline that meant to delete a route can say so; one
        that did not should not discover the difference in production.
        """
        if self.flag("yes"):
            return True
        if not is_interactive():
            self.error("Refusing to continue without confirmation. Pass --yes.")
            return False
        return self.confirm(question, default=False)


class ApiCommand(_Base):
    """A command that acts through the Janus API.

    Subclasses implement :meth:`run` and receive a ready :class:`ApiClient`.
    Authentication failures produce the exact message the operator needs:

        Authentication required.
        Run: janus login
    """

    #: Added to every subclass so `--json` works everywhere without repeating it.
    base_arguments = [json_flag()]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Add ``--json`` to every subclass's arguments unless it already declares it."""
        super().__init_subclass__(**kwargs)
        declared = list(getattr(cls, "arguments", []) or [])
        names = {getattr(a, "name", None) for a in declared}
        for extra in cls.base_arguments:
            if extra.name not in names:
                declared.append(extra)
        cls.arguments = declared

    def run(self, api: ApiClient) -> int | None:  # pragma: no cover - overridden
        """Do the work with an authenticated :class:`~cli.client.ApiClient`; return the exit code.
        """
        raise NotImplementedError

    def handle(self) -> int:
        """Build the client, run, and turn auth/API errors into a one-line message and exit code 1.
        """
        try:
            return self.run(ApiClient()) or 0
        except (CliAuthRequired, CliError) as error:
            return self.fail_with(error)


class LocalCommand(_Base):
    """A command that opens the database directly.

    Deliberately rare. Anything a signed-in user could do through the API
    belongs on :class:`ApiCommand`, so that its authorization is the API's;
    this base exists for the operations that have no user behind
    them — running migrations, seeding, starting a process.
    """

    base_arguments = [json_flag()]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Add ``--json`` to every subclass's arguments unless it already declares it."""
        super().__init_subclass__(**kwargs)
        declared = list(getattr(cls, "arguments", []) or [])
        names = {getattr(a, "name", None) for a in declared}
        for extra in cls.base_arguments:
            if extra.name not in names:
                declared.append(extra)
        cls.arguments = declared

    async def run_async(self) -> int | None:  # pragma: no cover - overridden
        """Do the work with the database connected; return the exit code."""
        raise NotImplementedError

    def handle(self) -> int:
        """Run :meth:`run_async` inside a database connection and report errors as one line."""
        try:
            return asyncio.run(self._with_database()) or 0
        except CliError as error:
            return self.fail_with(error)

    async def _with_database(self) -> int | None:
        """Open the ORM connection, run the command, close the connection."""
        from database.config import database

        async with database():
            return await self.run_async()
