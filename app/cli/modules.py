"""janus modules — what a gateway's Caddy build has, and what it is missing."""

from __future__ import annotations

from sillo.console import Argument, Option

from app.cli.base import ApiCommand
from app.cli.client import ApiClient

__all__ = ["ModulesBuild", "ModulesDeclare", "ModulesList"]

GATEWAY_OPTION = Option("gateway", help="Gateway slug or id.")


class ModulesList(ApiCommand):
    name = "modules list"
    help = "Show which Caddy plugins this gateway's build provides."
    arguments = [GATEWAY_OPTION]

    def run(self, api: ApiClient) -> int:
        data = api.get("/api/modules", gateway=self.option("gateway"))
        if self.wants_json:
            self.emit(data)
            return 0

        self.pairs([
            ["Caddy", data["version"] or "unknown"],
            ["Modules", f"{data['module_count']} ({data['standard_count']} standard)"],
            # `detected` is an observation; `declared` is an operator's word for
            # it. Saying which is the whole point of printing this line.
            ["Source", {
                "binary": "detected from the local caddy binary",
                "declared": "declared by an operator",
                "unknown": "unknown — assuming core modules only",
            }.get(data["source"], data["source"])],
        ])
        if data["error"]:
            self.muted(f"  {data['error']}")

        self.blank()
        self.table(
            ["PLUGIN", "STATUS", "PROVIDES"],
            [
                [
                    row["name"],
                    "installed" if row["installed"] else "—",
                    ", ".join(row["provides"])[:40],
                ]
                for row in data["catalog"]
            ],
        )

        if data["gaps"]:
            self.blank()
            self.rule("WHAT IS UNAVAILABLE")
            for gap in data["gaps"]:
                self.blank()
                self.line(gap["feature"])
                self.muted(f"  {gap['without']}")
                if gap["package"]:
                    self.muted(f"  Needs: {gap['package']}")

        if data["unrecognised"]:
            self.blank()
            self.warn(
                f"{len(data['unrecognised'])} non-standard module(s) Janus does not "
                "recognise. The generated build command will not include them."
            )
        return 0


class ModulesBuild(ApiCommand):
    """Print the `xcaddy build` command for a wanted set of plugins."""

    name = "modules build"
    help = "Print the command that changes which plugins this build has."
    arguments = [
        Argument("plugins", help="Comma-separated plugin keys to add, e.g. ratelimit,jwt. '-' for none."),
        Option("remove", help="Comma-separated plugin keys to remove from the build."),
        Option("mode", help="'script' (default) for scripts/setup-caddy.sh, or 'xcaddy'."),
        GATEWAY_OPTION,
    ]

    def run(self, api: ApiClient) -> int:
        raw = self.argument("plugins")
        adding = "" if raw.strip() in {"-", "none"} else raw
        removing = self.option("remove") or ""
        data = api.get(
            "/api/modules", add=adding, remove=removing, gateway=self.option("gateway")
        )
        mode = (self.option("mode") or "script").lower()
        command = data["script_command"] if mode == "script" else data["build_command"]

        if self.wants_json:
            self.emit({
                "command": command, "mode": mode,
                "adding": [k for k in adding.split(",") if k],
                "removing": [k for k in removing.split(",") if k],
            })
            return 0

        self.line(command)
        self.blank()
        # The part people get wrong, said every time it is printed.
        if mode == "script":
            self.muted(
                "Run this on the host Caddy lives on, from this checkout. It builds or\n"
                "downloads the binary, backs the current one up, swaps it in, restarts\n"
                "Caddy and redeploys. Reverse with: scripts/setup-caddy.sh --rollback --restart"
            )
        else:
            self.muted(
                "This includes the plugins already in the build. xcaddy produces a fresh\n"
                "binary from exactly the packages listed, so a command naming only the new\n"
                "plugin silently drops the others."
            )
        if data["unrecognised"]:
            self.blank()
            self.warn("Not included, because Janus does not recognise them:")
            for module in data["unrecognised"]:
                self.bullet(module)
        self.blank()
        self.muted("Replace the binary and restart Caddy; plugins are compiled in, not loaded.")
        return 0


class ModulesDeclare(ApiCommand):
    """Record what a remote gateway's build has.

    `caddy list-modules` asks the binary on *this* machine, which says nothing
    about a Caddy on another host. This is how an operator tells Janus what
    they built.
    """

    name = "modules declare"
    help = "Record the module ids a remote gateway's build provides."
    arguments = [
        Argument("modules", help="Comma-separated module ids, or 'none' to clear."),
        GATEWAY_OPTION,
    ]

    def run(self, api: ApiClient) -> int:
        raw = self.argument("modules")
        listed = [] if raw.strip().lower() == "none" else [
            m.strip() for m in raw.split(",") if m.strip()
        ]
        result = api.post(
            "/api/modules/declare", {"modules": listed}, gateway=self.option("gateway")
        )
        if self.wants_json:
            self.emit(result)
        elif listed:
            self.success(f"Recorded {len(listed)} module(s).")
        else:
            self.success("Cleared. Janus will detect from the local binary again.")
        return 0
