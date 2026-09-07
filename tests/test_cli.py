"""The CLI: that it requires authentication, and cannot bypass authorization.

The interesting assertions here are structural. Janus's defence against the CLI
becoming an RBAC bypass is not a check inside each command — it is that every
administrative command performs its work through the same API the dashboard's
authorization guards, and that the handful of local commands cannot do anything
a signed-in user could do instead. Those are properties of the command classes,
so they are asserted directly rather than by exercising forty commands.
"""

from __future__ import annotations

import os
import stat
import sys

import pytest
from sillo.console.arguments import ParsedInput
from sillo.console.output import Output
from sillo.console.prompt import Prompt

from app.cli import COMMANDS, build_console
from app.cli.base import ApiCommand, LocalCommand
from app.cli.client import ApiClient, CliAuthRequired, clear_token, load_token, save_token
from tests.helpers import make_user


def build_command(command_class, *, arguments=None, options=None, flags=None):
    """Bind a command the way the console binds one.

    `Command.__init__` takes the parsed input and the IO objects, so a test
    that instantiates a command supplies them. Going through the real
    constructor rather than poking attributes means these tests exercise the
    same wiring the console does.
    """
    values: dict = {}
    kinds: dict = {}
    for name, value in (arguments or {}).items():
        values[name], kinds[name] = value, "argument"
    for name, value in (options or {}).items():
        values[name], kinds[name] = value, "option"
    for name, value in (flags or {}).items():
        values[name], kinds[name] = value, "flag"

    output = Output(sys.stdout)
    return command_class(ParsedInput(values, kinds), output, Prompt(output))


class TestAuthenticationIsRequired:
    def test_a_client_without_a_token_refuses(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JANUS_HOME", str(tmp_path))
        monkeypatch.delenv("JANUS_TOKEN", raising=False)

        with pytest.raises(CliAuthRequired) as error:
            ApiClient(url="http://localhost:1").get("/api/routes")

        # The exact words the brief asks for, because an operator reading this
        # in a terminal needs the next command, not a diagnosis.
        assert "Authentication required." in str(error.value)
        assert "janus login" in str(error.value)

    def test_every_administrative_command_goes_through_the_api(self):
        """The structural guarantee.

        A command that opened the database directly would be a second path to
        the operation with none of the API's authorization on it. Only the
        local commands may do that, and they are named here so adding one is a
        deliberate act rather than an omission.
        """
        allowed_local = {
            "migrate", "seed", "work", "collect", "serve", "worker", "scheduler",
            # Bootstrap only, and it refuses once an account exists.
            "admin create",
            # Sign-in itself: it is how authentication starts.
            "login", "logout",
        }
        offenders = [
            command.name
            for command in COMMANDS
            if not issubclass(command, ApiCommand) and command.name not in allowed_local
        ]
        assert not offenders, f"commands bypassing the API: {offenders}"

    def test_the_local_commands_are_the_expected_ones(self):
        local = sorted(c.name for c in COMMANDS if issubclass(c, LocalCommand))
        assert local == ["admin create", "collect", "migrate", "seed", "work"]


class TestBootstrapIsNotAPermanentBackDoor:
    async def test_admin_create_refuses_once_an_account_exists(self, capsys):
        """Without this, the CLI is a permanent way around authorization for
        anyone with shell access."""
        from app.cli.team import AdminCreate

        await make_user("existing@test.local", role="Owner")

        command = build_command(
            AdminCreate, options={"email": "x@y.z", "name": "X", "role": "Owner"}
        )
        assert await command.run_async() == 1

        output = capsys.readouterr().out
        assert "already has accounts" in output
        assert "janus users create" in output


class TestDestructiveCommandsConfirm:
    #: Commands that delete or revoke something irreversible.
    DESTRUCTIVE = {
        "routes delete", "upstreams delete", "gateways delete",
        "ratelimit delete", "keys revoke", "users delete", "admin delete",
        "config rollback",
    }

    def test_each_destructive_command_offers_yes(self):
        """`--yes` is what makes automation possible without making a
        mis-typed command destructive by default."""
        by_name = {c.name: c for c in COMMANDS}
        for name in self.DESTRUCTIVE:
            command = by_name.get(name)
            assert command is not None, f"missing command: {name}"
            flags = {getattr(a, "name", None) for a in command.arguments}
            assert "yes" in flags, f"{name} has no --yes flag"

    def test_a_non_interactive_shell_without_yes_refuses(self, monkeypatch):
        """A pipeline that meant to delete a route can say so; one that did not
        should not discover the difference in production."""
        from app.cli.base import _Base

        monkeypatch.setattr("sillo.console.terminal.is_interactive", lambda: False)

        command = build_command(_Base, flags={"yes": False})
        assert command.confirm_destructive("Delete everything?") is False


class TestJsonOutput:
    def test_every_api_command_accepts_json(self):
        """A script that set --json and then has to parse a human sentence out
        of stderr is a script that will not."""
        for command in COMMANDS:
            if not issubclass(command, ApiCommand):
                continue
            flags = {getattr(a, "name", None) for a in command.arguments}
            assert "json" in flags, f"{command.name} has no --json flag"


class TestTokenStorage:
    def test_the_token_file_is_owner_readable_only(self, monkeypatch, tmp_path):
        """A bearer token granting full authority over the gateway estate must
        not be world-readable in a shared home directory."""
        monkeypatch.setenv("JANUS_HOME", str(tmp_path))
        path = save_token("http://localhost:8000", "secret-token", "a@b.c")

        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600
        assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700

    def test_a_stored_token_round_trips(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JANUS_HOME", str(tmp_path))
        save_token("http://localhost:8000", "secret-token", "a@b.c")
        stored = load_token()
        assert stored["token"] == "secret-token"
        assert stored["email"] == "a@b.c"

    def test_clearing_removes_it(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JANUS_HOME", str(tmp_path))
        save_token("http://localhost:8000", "t", "a@b.c")
        assert clear_token() is True
        assert load_token() is None

    def test_a_corrupt_token_file_reads_as_signed_out(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JANUS_HOME", str(tmp_path))
        save_token("http://localhost:8000", "t", "a@b.c")
        (tmp_path / "credentials.json").write_text("{ not json")
        assert load_token() is None


class TestTheConsole:
    def test_every_command_is_registered(self):
        console = build_console()
        assert len(console.commands) == len(COMMANDS)

    def test_space_separated_names_resolve(self):
        """The brief specifies `janus routes list`, not `janus routes:list`."""
        console = build_console()
        assert console._join_leading_tokens(["routes", "list"]) == ["routes list"]
        assert console._join_leading_tokens(["config", "apply", "--yes"]) == [
            "config apply",
            "--yes",
        ]
        assert console._join_leading_tokens(["analytics", "problematic"]) == [
            "analytics problematic"
        ]

    def test_the_longest_matching_name_wins(self):
        console = build_console()
        # `security blocklist` exists; `security` alone does not.
        assert console._join_leading_tokens(["security", "blocklist"]) == ["security blocklist"]

    def test_an_unknown_command_falls_through_to_the_framework(self):
        console = build_console()
        assert console._join_leading_tokens(["nonsense", "here"]) == ["nonsense", "here"]

    def test_no_two_commands_share_a_name(self):
        names = [c.name for c in COMMANDS]
        assert len(names) == len(set(names))


class TestEnvFileLoading:
    """`janus …` run by hand must see /etc/janus/janus.env, or it acts on the
    wrong database — the account you create never reaches the web tier's login."""

    def test_loads_the_named_file_without_overriding_the_real_environment(
        self, monkeypatch, tmp_path
    ):
        from app.cli import _load_env_file

        env_file = tmp_path / "janus.env"
        env_file.write_text(
            "# deployment config\n"
            'DATABASE_URL="postgres://janus:secret@db/janus"\n'
            "export QUEUE_BACKEND=redis\n"
            "ALREADY_SET=from-file\n"
        )
        monkeypatch.setenv("JANUS_ENV_FILE", str(env_file))
        monkeypatch.setenv("ALREADY_SET", "from-environment")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("QUEUE_BACKEND", raising=False)

        _load_env_file()

        assert os.environ["DATABASE_URL"] == "postgres://janus:secret@db/janus"
        assert os.environ["QUEUE_BACKEND"] == "redis"  # `export ` prefix stripped
        assert os.environ["ALREADY_SET"] == "from-environment"  # real env wins

    def test_a_missing_file_is_not_an_error(self, monkeypatch, tmp_path):
        from app.cli import _load_env_file

        monkeypatch.setenv("JANUS_ENV_FILE", str(tmp_path / "nope.env"))
        _load_env_file()  # must not raise
