"""janus config — show, validate, diff, apply, history, rollback."""

from __future__ import annotations

import json as jsonlib

from sillo.console import Argument, Flag, Option

from app.cli.base import ApiCommand
from app.cli.client import ApiClient

__all__ = [
    "ConfigApply", "ConfigDiff", "ConfigHistory", "ConfigRollback", "ConfigShow",
    "ConfigValidate",
]

GATEWAY_OPTION = Option("gateway", help="Gateway slug or id.")


class ConfigShow(ApiCommand):
    name = "config show"
    help = "Show the configuration Janus would deploy."
    arguments = [GATEWAY_OPTION]

    def run(self, api: ApiClient) -> int:
        result = api.get("/api/config", gateway=self.option("gateway"))
        if self.wants_json:
            self.emit(result)
            return 0
        self.line(jsonlib.dumps(result["config"], indent=2))
        for warning in result["warnings"]:
            self.warn(warning)
        return 0


class ConfigValidate(ApiCommand):
    name = "config validate"
    help = "Validate the pending configuration without deploying it."
    arguments = [GATEWAY_OPTION]

    def run(self, api: ApiClient) -> int:
        result = api.get("/api/config/validate", gateway=self.option("gateway"))
        if self.wants_json:
            self.emit(result)
            return 0 if result["ok"] else 1

        if result["ok"]:
            self.success("Valid configuration.")
            # Stated because they are not the same guarantee: `caddy` means a
            # real Caddy provisioned this payload, `structural` means only
            # Janus's own checks ran. The *reason* a binary was not used
            # differs — absent, or not a valid judge of a declared build — and
            # the warnings say which, so this does not claim the wrong one.
            if result["method"] == "caddy":
                self.muted("Validated by Caddy itself.")
            elif not any("declared" in w for w in result["warnings"]):
                self.muted("Structural checks only — no Caddy binary was available.")
        else:
            self.error("Invalid configuration.")
            self.line(result["error"] or "")
        for warning in result["warnings"]:
            self.warn(warning)
        return 0 if result["ok"] else 1


class ConfigDiff(ApiCommand):
    name = "config diff"
    help = "Show what would change if you deployed now."
    arguments = [GATEWAY_OPTION]

    def run(self, api: ApiClient) -> int:
        result = api.get("/api/config/diff", gateway=self.option("gateway"))
        if self.wants_json:
            self.emit(result)
            return 0
        if not result["has_changes"]:
            self.success("No changes — the gateway is running the desired configuration.")
            return 0
        for line in result["diff"]:
            if line.startswith("+") and not line.startswith("+++"):
                self.success(line)
            elif line.startswith("-") and not line.startswith("---"):
                self.error(line)
            elif line.startswith("@@"):
                self.muted(line)
            else:
                self.line(line)
        return 0


class ConfigApply(ApiCommand):
    name = "config apply"
    help = "Generate, validate, apply and verify the configuration."
    arguments = [
        Option("note", help="Recorded against the version."),
        Flag("force", help="Deploy even if nothing changed."),
        Flag("yes", short="y", help="Skip the confirmation prompt."),
        GATEWAY_OPTION,
    ]

    def run(self, api: ApiClient) -> int:
        # The diff is fetched for the confirmation prompt, not to decide
        # whether to deploy. Deciding here was wrong: the diff describes the
        # *server object*, and a deployment also writes the access logger — so
        # a log-path change showed an empty diff, the CLI returned early, and
        # the gateway was never told. `deploy` makes that call itself and
        # reports `unchanged` when it means it.
        diff = api.get("/api/config/diff", gateway=self.option("gateway"))

        if not self.wants_json and diff["has_changes"]:
            changed = len([line for line in diff["diff"] if line.startswith(("+", "-"))]) - 2
            if not self.confirm_destructive(f"Deploy {max(0, changed)} configuration change(s)?"):
                self.muted("Cancelled.")
                return 1

        result = api.post(
            "/api/config/apply",
            {"note": self.option("note") or "", "force": self.flag("force")},
            gateway=self.option("gateway"),
        )
        if self.wants_json:
            self.emit(result)
            return 0 if result["ok"] else 1

        if result["unchanged"]:
            self.success("Already up to date.")
        elif result["ok"]:
            self.success(f"Deployed v{result['version']}.")
        else:
            self.error(f"Deployment failed at stage: {result['stage']}")
            self.line(result["error"] or "")
            self.muted("The gateway is still running its previous configuration.")
        for warning in result["warnings"]:
            self.warn(warning)
        return 0 if result["ok"] else 1


class ConfigHistory(ApiCommand):
    name = "config history"
    help = "List configuration versions."
    arguments = [GATEWAY_OPTION]

    def run(self, api: ApiClient) -> int:
        result = api.get("/api/config/history", gateway=self.option("gateway"))
        if self.wants_json:
            self.emit(result)
            return 0
        # Six columns, not eight, and every one trimmed to a known width. The
        # table renderer squeezes to the terminal, and an eighty-column shell —
        # which is what a CI log is — turns a wide table into a row of
        # ellipses that says nothing. Author is shown as the local part; the
        # full address is in `--json`.
        self.table(
            ["VER", "STATUS", "ROUTES", "AUTHOR", "WHEN", "SUMMARY"],
            [
                [
                    f"v{v['version']}",
                    v["status"],
                    v["routes"],
                    (v["author"] or "system").split("@")[0][:12],
                    v["created_at"][5:16].replace("T", " "),
                    (v["summary"] or "")[:30],
                ]
                for v in result["versions"]
            ],
        )
        self.blank()
        self.muted("Full detail, including the note and any error, is in --json.")
        return 0


class ConfigRollback(ApiCommand):
    name = "config rollback"
    help = "Redeploy an earlier version."
    arguments = [
        Argument("version", help="Version number to restore."),
        Flag("yes", short="y"),
        GATEWAY_OPTION,
    ]

    def run(self, api: ApiClient) -> int:
        version = int(self.argument("version"))
        if not self.confirm_destructive(f"Roll the gateway back to v{version}?"):
            self.muted("Cancelled.")
            return 1

        result = api.post(
            "/api/config/rollback", {"version": version}, gateway=self.option("gateway")
        )
        if self.wants_json:
            self.emit(result)
            return 0 if result["ok"] else 1

        if result["ok"]:
            self.success(f"Rolled back — now running v{result['version']} (restored from v{version}).")
        else:
            self.error(f"Rollback failed at {result['stage']}: {result['error']}")
        return 0 if result["ok"] else 1
