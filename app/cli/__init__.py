"""The `janus` console.

Built on :mod:`sillo.console` — the framework's command class, parameter
declaration, dispatcher, prompts and output primitives. Janus supplies the
commands and nothing else; there is no argument parser, no table renderer and
no prompt implementation in this package.

The command set is grouped by noun, and the grouping is in the names
(`routes list`, `routes create`) rather than in nested parsers, because
`sillo.console` dispatches on the whole name and a flat registry keeps
`janus --help` a single readable list.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path


def _load_env_file() -> None:
    """Populate ``os.environ`` from a deployment env file before config is read.

    Only the systemd units get ``EnvironmentFile=``. A hand-run ``janus migrate``
    or ``janus admin create`` would otherwise fall back to the built-in defaults
    (SQLite, memory queue) and quietly act on a *different* database than the
    running service — the account you create never shows up at the login the
    web tier serves. Load ``$JANUS_ENV_FILE`` if set, else ``/etc/janus/janus.env``,
    else ``./.env``. Real environment variables always win (``setdefault``); the
    first file to define a key wins over later ones.
    """
    candidates: list[Path] = []
    explicit = os.environ.get("JANUS_ENV_FILE")
    if explicit:
        candidates.append(Path(explicit))
    candidates += [Path("/etc/janus/janus.env"), Path.cwd() / ".env"]
    for path in candidates:
        try:
            if not path.is_file():
                continue
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if line.startswith("export "):
                line = line[7:].lstrip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


_load_env_file()

from sillo.console import Command, Console  # noqa: E402

from app.cli import analytics, auth, config, gateway, local, modules, security, team  # noqa: E402

__all__ = ["COMMANDS", "JanusConsole", "build_console", "main"]


class JanusConsole(Console):
    """Sillo's console, resolving space-separated command names.

    `sillo.console.Console` takes the first token as the whole command name,
    which suits its own `group:name` convention. Janus's commands are specified
    as `janus routes list` and `janus config apply`, so the name is one, two or
    three tokens.

    This overrides exactly one step — turning the leading tokens into a name —
    and hands everything else back to the framework: parameter parsing, the
    help renderer, prompts, output, the dispatcher and the exit codes are all
    `sillo.console`'s. Longest match wins, so `routes list` beats a
    hypothetical `routes`, and a name that is not a prefix of anything falls
    through to the framework's own "unknown command" handling with its
    suggestion.
    """

    #: The most tokens any registered name uses. Computed rather than fixed so
    #: adding `analytics routes errors` later needs no change here.
    @property
    def _max_name_tokens(self) -> int:
        return max((len(name.split()) for name in self.commands), default=1)

    def _join_leading_tokens(self, tokens: list[str]) -> list[str]:
        """Rewrite `[routes, list, --json]` as `["routes list", --json]`."""
        for size in range(min(self._max_name_tokens, len(tokens)), 0, -1):
            candidate = " ".join(tokens[:size])
            if self.resolve(candidate) is not None:
                return [candidate, *tokens[size:]]
        return tokens

    def _prepare(self, argv: Sequence[str] | None) -> int | tuple[type[Command], object]:
        import sys

        tokens = list(sys.argv[1:] if argv is None else argv)
        if tokens and tokens[0] not in ("-h", "--help", "help", "-V", "--version"):
            tokens = self._join_leading_tokens(tokens)
        return super()._prepare(tokens)

COMMANDS = (
    # Session
    auth.Login, auth.Logout, auth.Whoami,
    # Gateways
    gateway.GatewayList, gateway.GatewayShow, gateway.GatewayStatus,
    gateway.GatewayCreate, gateway.GatewayDelete,
    # Routes
    gateway.RouteList, gateway.RouteShow, gateway.RouteCreate, gateway.RouteUpdate,
    gateway.RouteDelete, gateway.RouteEnable, gateway.RouteDisable,
    # Upstreams
    gateway.UpstreamList, gateway.UpstreamShow, gateway.UpstreamCreate,
    gateway.UpstreamDelete, gateway.UpstreamHealth,
    # Caddy plugins
    modules.ModulesList, modules.ModulesBuild, modules.ModulesDeclare,
    # Security
    security.SecurityBlock, security.SecurityUnblock, security.SecurityAllow,
    security.SecurityDeny, security.Blocklist, security.Allowlist,
    security.RateLimitList, security.RateLimitCreate, security.RateLimitUpdate,
    security.RateLimitDelete,
    security.KeyList, security.KeyCreate, security.KeyRevoke, security.KeyRotate,
    # Team
    team.AdminCreate, team.AdminList, team.AdminEnable, team.AdminDisable,
    team.AdminDelete,
    team.UserList, team.UserCreate, team.UserEnable, team.UserDisable,
    team.UserDelete, team.UserRole,
    team.RoleList, team.RoleCreate, team.RolePermissions, team.RoleAssign,
    # Analytics
    analytics.AnalyticsOverview, analytics.AnalyticsRoutes, analytics.AnalyticsErrors,
    analytics.AnalyticsClients, analytics.AnalyticsTraffic, analytics.AnalyticsProblematic,
    analytics.AnalyticsPerformance,
    # Configuration
    config.ConfigShow, config.ConfigValidate, config.ConfigDiff, config.ConfigApply,
    config.ConfigHistory, config.ConfigRollback,
    # Local
    local.Migrate, local.Seed, local.Work, local.Collect, local.Serve,
    local.Worker, local.Scheduler,
)


def build_console() -> Console:
    console = JanusConsole(prog="janus")
    for command in COMMANDS:
        console.add(command)
    return console


def main(argv: list[str] | None = None) -> int:
    return build_console().main(argv)
