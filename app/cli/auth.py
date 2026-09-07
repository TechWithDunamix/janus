"""janus login / logout / whoami."""

from __future__ import annotations

import os

from sillo.console import Command, Option

from app.cli.base import ApiCommand, json_flag
from app.cli.client import ApiClient, CliError, clear_token, load_token, save_token

__all__ = ["Login", "Logout", "Whoami"]


class Login(Command):
    """Exchange credentials for a token and store it."""

    name = "login"
    help = "Sign in to a Janus control plane."

    arguments = [
        Option("url", help="Control plane URL. Defaults to $JANUS_URL or localhost:8000."),
        Option("email", help="Address to sign in as. Prompted for if omitted."),
        Option("label", default="janus CLI", help="How this session appears in the team screen."),
        json_flag(),
    ]

    def handle(self) -> int:
        url = (
            self.option("url")
            or os.getenv("JANUS_URL")
            or (load_token() or {}).get("url")
            or "http://localhost:8000"
        )
        email = self.option("email") or self.ask("Email")
        # `secret` never echoes and never appears in shell history, which is
        # why the password is not an option flag: `--password hunter2` would be
        # in `~/.zsh_history` and in the process list.
        password = self.secret("Password")

        client = ApiClient(url=url, token=None)
        try:
            result = client.request(
                "POST",
                "/api/auth/login",
                {"email": email, "password": password, "label": self.option("label")},
            )
        except CliError as error:
            if self.flag("json"):
                self.line(f'{{"error": {error!s:!r}}}')
            else:
                self.error(str(error))
            return 1

        path = save_token(url, result["token"], result["user"]["email"])
        if self.flag("json"):
            self.line(
                f'{{"ok": true, "email": "{result["user"]["email"]}", "url": "{url}"}}'
            )
        else:
            self.success(f"Signed in as {result['user']['email']} at {url}.")
            self.muted(f"Token stored in {path} (owner-readable only).")
        return 0


class Logout(Command):
    """Revoke the stored token and delete it."""

    name = "logout"
    help = "Sign out and revoke this CLI session."

    arguments = [json_flag()]

    def handle(self) -> int:
        stored = load_token()
        if stored is None:
            self.muted("Not signed in.")
            return 0

        # Revoke server-side first. Deleting the local file alone would leave a
        # working token in the database until it expired, which is not what
        # "log out" means on a shared machine.
        try:
            ApiClient().post("/api/auth/logout")
        except CliError:
            self.warn("Could not reach the control plane; revoking locally only.")

        clear_token()
        self.success("Signed out.")
        return 0


class Whoami(ApiCommand):
    """Who the stored token belongs to, and what it may do."""

    name = "whoami"
    help = "Show the signed-in identity and its permissions."

    def run(self, api: ApiClient) -> int:
        me = api.get("/api/auth/whoami")
        if self.wants_json:
            self.emit(me)
            return 0

        self.line(me["email"], style=None)
        self.pairs(
            [
                ["Name", me["name"]],
                ["Roles", ", ".join(me["roles"]) or "—"],
                ["Owner", "yes" if me["is_superuser"] else "no"],
                ["Control plane", api.url],
            ]
        )
        self.blank()
        self.muted(f"{len(me['permissions'])} permissions:")
        for permission in me["permissions"]:
            self.bullet(permission)
        return 0
