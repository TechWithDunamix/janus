"""Base commands: JSON output, confirmation, and the local/remote split.

Two base classes, and the difference between them is the whole security story
of the CLI:

* :class:`ApiCommand` performs its work through the Janus API, authenticated
  with the token from `janus login`. Authorization is the API's, which is the
  dashboard's. There is no way for a command built on this to do something its
  user may not do.
* :class:`LocalCommand` opens the database directly. Only five commands do
  this — migrate, seed, serve, worker, scheduler — plus the bootstrap
  `admin create`, which refuses once an account exists.
"""

from __future__ import annotations

import asyncio
import json as jsonlib
from typing import Any

from sillo.console import Command, Flag

from app.cli.client import ApiClient, CliAuthRequired, CliError

__all__ = ["ApiCommand", "LocalCommand", "json_flag"]


def json_flag() -> Flag:
    return Flag("json", help="Emit machine-readable JSON instead of a table.")


class _Base(Command):
    """Shared output helpers."""

    @property
    def wants_json(self) -> bool:
        return bool(self.flag("json"))

    def emit(self, payload: Any) -> None:
        """Print JSON. Used by every command's `--json` path."""
        self.line(jsonlib.dumps(payload, indent=2, default=str))

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

    def confirm_destructive(self, question: str) -> bool:
        """Ask before something irreversible, unless `--yes` was passed.

        In a non-interactive shell without `--yes` this refuses rather than
        assuming yes. A pipeline that meant to delete a route can say so; one
        that did not should not discover the difference in production.
        """
        if self.flag("yes"):
            return True
        from sillo.console.terminal import is_interactive

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
        super().__init_subclass__(**kwargs)
        declared = list(getattr(cls, "arguments", []) or [])
        names = {getattr(a, "name", None) for a in declared}
        for extra in cls.base_arguments:
            if extra.name not in names:
                declared.append(extra)
        cls.arguments = declared

    def run(self, api: ApiClient) -> int | None:  # pragma: no cover - overridden
        raise NotImplementedError

    def handle(self) -> int:
        try:
            return self.run(ApiClient()) or 0
        except (CliAuthRequired, CliError) as error:
            return self.fail_with(error)


class LocalCommand(_Base):
    """A command that opens the database directly.

    Deliberately rare. Anything a signed-in user could do through the dashboard
    belongs on :class:`ApiCommand`, so that its authorization is the
    dashboard's; this base exists for the operations that have no user behind
    them — running migrations, seeding, starting a process.
    """

    base_arguments = [json_flag()]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        declared = list(getattr(cls, "arguments", []) or [])
        names = {getattr(a, "name", None) for a in declared}
        for extra in cls.base_arguments:
            if extra.name not in names:
                declared.append(extra)
        cls.arguments = declared

    async def run_async(self) -> int | None:  # pragma: no cover - overridden
        raise NotImplementedError

    def handle(self) -> int:
        try:
            return asyncio.run(self._with_database()) or 0
        except CliError as error:
            return self.fail_with(error)

    async def _with_database(self) -> int | None:
        from database.config import database

        async with database():
            return await self.run_async()
