"""janus audit list."""

from __future__ import annotations

from sillo.console import Option

from cli.base import ApiCommand
from cli.client import ApiClient


class AuditList(ApiCommand):
    """``janus audit list [--action A]`` — who changed what, newest first."""

    name = "audit list"
    help = "Show the latest control-plane audit events."
    arguments = [Option("action", help="Only actions starting with this, e.g. route. or config.deployed")]

    def run(self, api: ApiClient) -> int:
        rows = api.get("/api/audit")["events"]
        if self.option("action"):
            rows = [r for r in rows if str(r.get("action") or "").startswith(self.option("action"))]
        return self.rows(rows, [("AT", "at"), ("ACTOR", "actor"), ("ACTION", "action"), ("RESOURCE", "resource"),
                                ("LABEL", "label"), ("ORIGIN", "origin")], empty="No audit events.")
