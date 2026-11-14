"""janus security / ratelimit / keys."""

from __future__ import annotations

from sillo.console import Argument, Flag, Option

from cli.base import ApiCommand
from cli.client import ApiClient

__all__ = [
    "Allowlist", "Blocklist", "KeyCreate", "KeyList", "KeyRevoke", "KeyRotate",
    "RateLimitCreate", "RateLimitDelete", "RateLimitList", "RateLimitUpdate",
    "SecurityAllow", "SecurityBlock", "SecurityDeny", "SecurityUnblock",
]

GATEWAY_OPTION = Option("gateway", help="Gateway slug or id.")


class SecurityBlock(ApiCommand):
    """``janus security block`` — Block an address or CIDR range.

    Arguments:
        CIDR: Address or range, e.g. 203.0.113.42 or 10.0.0.0/8.
        --reason VALUE: Why. Recorded in the audit log.
        --minutes VALUE: Block for this many minutes. Omit for permanent.
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "security block"
    help = "Block an address or CIDR range."
    arguments = [
        Argument("cidr", help="Address or range, e.g. 203.0.113.42 or 10.0.0.0/8."),
        Option("reason", help="Why. Recorded in the audit log."),
        Option("minutes", help="Block for this many minutes. Omit for permanent."),
        GATEWAY_OPTION,
    ]

    def run(self, api: ApiClient) -> int:
        """Call ``POST /api/security/block`` and print the result (raw JSON with ``--json``);
        returns the exit code.
        """
        result = api.post(
            "/api/security/block",
            {
                "cidr": self.argument("cidr"),
                "reason": self.option("reason") or "",
                "minutes": int(self.option("minutes") or 0),
            },
            gateway=self.option("gateway"),
        )
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Blocked {result['cidr']}.")
            self.muted("Run `janus config apply` to enforce it at the gateway.")
        return 0


class SecurityUnblock(ApiCommand):
    """``janus security unblock`` — Remove block rules for an address or range.

    Arguments:
        CIDR
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "security unblock"
    help = "Remove block rules for an address or range."
    arguments = [Argument("cidr"), GATEWAY_OPTION]

    def run(self, api: ApiClient) -> int:
        """Call ``POST /api/security/unblock`` and print the result (raw JSON with ``--json``);
        returns the exit code.
        """
        result = api.post(
            "/api/security/unblock", {"cidr": self.argument("cidr")},
            gateway=self.option("gateway"),
        )
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Removed {result['removed']} block rule(s).")
            self.muted("Run `janus config apply` to apply the change.")
        return 0


class SecurityAllow(ApiCommand):
    """``janus security allow`` — Allowlist an address or range.

    Arguments:
        CIDR
        --reason VALUE
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "security allow"
    help = "Allowlist an address or range."
    arguments = [Argument("cidr"), Option("reason"), GATEWAY_OPTION]

    def run(self, api: ApiClient) -> int:
        """Call ``POST /api/security/allow`` and print the result (raw JSON with ``--json``);
        returns the exit code.
        """
        result = api.post(
            "/api/security/allow",
            {"cidr": self.argument("cidr"), "reason": self.option("reason") or ""},
            gateway=self.option("gateway"),
        )
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Allowed {result['cidr']}.")
            # Worth saying every time: an allowlist is deny-by-default for
            # everything outside it, which surprises people on a public gateway.
            self.warn(
                "Any allow rule at this scope makes it deny-by-default for "
                "everything outside the allowed ranges."
            )
        return 0


class SecurityDeny(SecurityBlock):
    """``janus security deny`` — Alias for `security block`."""

    name = "security deny"
    help = "Alias for `security block`."


class _RuleList(ApiCommand):
    """Shared implementation of ``security blocklist`` / ``security allowlist``."""

    kind = "block"

    def run(self, api: ApiClient) -> int:
        """Call ``GET /api/security/rules?action=<kind>`` and print the rules."""
        rules = api.get(
            "/api/security/rules", action=self.kind, gateway=self.option("gateway")
        )["rules"]
        if self.wants_json:
            self.emit(rules)
            return 0
        if not rules:
            self.muted(f"No {self.kind} rules.")
            return 0
        self.table(
            ["ID", "CIDR", "SCOPE", "EXPIRES", "REASON"],
            [
                [
                    r["id"], r["cidr"],
                    "permanent" if r["permanent"] else "temporary",
                    r["expires_at"] or "—", (r["reason"] or "")[:50],
                ]
                for r in rules
            ],
        )
        return 0


class Blocklist(_RuleList):
    """``janus security blocklist`` — List block rules.

    Arguments:
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "security blocklist"
    help = "List block rules."
    kind = "block"
    arguments = [GATEWAY_OPTION]


class Allowlist(_RuleList):
    """``janus security allowlist`` — List allow rules.

    Arguments:
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "security allowlist"
    help = "List allow rules."
    kind = "allow"
    arguments = [GATEWAY_OPTION]


# ---------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------


class RateLimitList(ApiCommand):
    """``janus ratelimit list`` — List rate limits.

    Arguments:
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "ratelimit list"
    help = "List rate limits."
    arguments = [GATEWAY_OPTION]

    def run(self, api: ApiClient) -> int:
        """Call ``GET /api/security/rate-limits`` and print the result (raw JSON with ``--json``);
        returns the exit code.
        """
        rows = api.get("/api/security/rate-limits", gateway=self.option("gateway"))["rate_limits"]
        if self.wants_json:
            self.emit(rows)
            return 0
        if not rows:
            self.muted("No rate limits defined.")
            return 0
        self.table(
            ["ID", "NAME", "KEYED BY", "RATE", "BURST", "ENABLED"],
            [
                [r["id"], r["name"], r["key"], r["rate"], r["burst"],
                 "yes" if r["enabled"] else "no"]
                for r in rows
            ],
        )
        return 0


class RateLimitCreate(ApiCommand):
    """``janus ratelimit create`` — Create a rate limit.

    Arguments:
        --name VALUE
        --key VALUE: Default ``ip``. One of ``ip``, ``api_key``, ``user``, ``route``, ``domain``,
            ``global``.
        --limit VALUE: Requests allowed per window. Default ``100``.
        --window VALUE: Window length in seconds. Default ``60``.
        --burst VALUE: Default ``0``.
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "ratelimit create"
    help = "Create a rate limit."
    arguments = [
        Option("name"),
        Option("key", default="ip", choices=["ip", "api_key", "user", "route", "domain", "global"]),
        Option("limit", default="100", help="Requests allowed per window."),
        Option("window", default="60", help="Window length in seconds."),
        Option("burst", default="0"),
        GATEWAY_OPTION,
    ]

    def run(self, api: ApiClient) -> int:
        """Call ``POST /api/security/rate-limits`` and print the result (raw JSON with ``--json``);
        returns the exit code.
        """
        name = self.option("name") or self.ask("Rate limit name")
        result = api.post(
            "/api/security/rate-limits",
            {
                "name": name,
                "key": self.option("key"),
                "limit": int(self.option("limit")),
                "window_seconds": int(self.option("window")),
                "burst": int(self.option("burst")),
            },
            gateway=self.option("gateway"),
        )
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Created “{result['name']}” at {result['rate']}.")
        return 0


class RateLimitUpdate(ApiCommand):
    """``janus ratelimit update`` — Update a rate limit.

    Arguments:
        ID
        --limit VALUE
        --window VALUE
        --burst VALUE
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "ratelimit update"
    help = "Update a rate limit."
    arguments = [
        Argument("id"), Option("limit"), Option("window"), Option("burst"), GATEWAY_OPTION,
    ]

    def run(self, api: ApiClient) -> int:
        """Call ``DELETE /api/security/rate-limits/{id}``, then ``POST /api/security/rate-limits``,
        then ``GET /api/security/rate-limits`` and print the result (raw JSON with ``--json``);
        returns the exit code.
        """
        # Delete-then-create would change the id every edit and break any
        # policy pointing at it, so the update goes through the same create
        # endpoint with the existing name.
        rows = api.get("/api/security/rate-limits", gateway=self.option("gateway"))["rate_limits"]
        target = next((r for r in rows if str(r["id"]) == self.argument("id")), None)
        if target is None:
            self.error(f"No rate limit {self.argument('id')}.")
            return 1

        payload = {
            "name": target["name"],
            "key": target["key"],
            "limit": int(self.option("limit") or target["limit"]),
            "window_seconds": int(self.option("window") or target["window_seconds"]),
            "burst": int(self.option("burst") or target["burst"]),
        }
        api.delete(f"/api/security/rate-limits/{target['id']}", gateway=self.option("gateway"))
        result = api.post("/api/security/rate-limits", payload, gateway=self.option("gateway"))
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Updated “{result['name']}” to {result['rate']}.")
        return 0


class RateLimitDelete(ApiCommand):
    """``janus ratelimit delete`` — Delete a rate limit.

    Arguments:
        ID
        --yes
        --gateway VALUE: Gateway slug or id (default: the first enabled gateway).
    """

    name = "ratelimit delete"
    help = "Delete a rate limit."
    arguments = [Argument("id"), Flag("yes", short="y"), GATEWAY_OPTION]

    def run(self, api: ApiClient) -> int:
        """Call ``DELETE /api/security/rate-limits/{id}`` and print the result (raw JSON with
        ``--json``); returns the exit code.
        """
        if not self.confirm_destructive(f"Delete rate limit {self.argument('id')}?"):
            self.muted("Cancelled.")
            return 1
        result = api.delete(
            f"/api/security/rate-limits/{int(self.argument('id'))}",
            gateway=self.option("gateway"),
        )
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Deleted {result['deleted']}.")
        return 0


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


class KeyList(ApiCommand):
    """``janus keys list`` — List gateway API keys."""

    name = "keys list"
    help = "List gateway API keys."

    def run(self, api: ApiClient) -> int:
        """Call ``GET /api/keys`` and print the result (raw JSON with ``--json``); returns the exit
        code.
        """
        keys = api.get("/api/keys")["keys"]
        if self.wants_json:
            self.emit(keys)
            return 0
        if not keys:
            self.muted("No keys issued.")
            return 0
        self.table(
            ["ID", "NAME", "PREFIX", "STATUS", "REQUESTS", "LAST USED"],
            [
                # The prefix, never the secret. There is no command that can
                # show a secret after the one that created it.
                [k["id"], k["name"], f"{k['prefix']}…", k["status"],
                 f"{k['requests']:,}", k["last_used_at"] or "never"]
                for k in keys
            ],
        )
        return 0


class KeyCreate(ApiCommand):
    """``janus keys create`` — Issue an API key.

    Arguments:
        --name VALUE
        --scopes VALUE: Comma-separated scopes.
        --expires-in-days VALUE: Expire after this many days.
    """

    name = "keys create"
    help = "Issue an API key."
    arguments = [
        Option("name"),
        Option("scopes", help="Comma-separated scopes."),
        Option("expires-in-days", help="Expire after this many days."),
    ]

    def run(self, api: ApiClient) -> int:
        """Call ``POST /api/keys`` and print the result (raw JSON with ``--json``); returns the exit
        code.
        """
        name = self.option("name") or self.ask("Key name")
        result = api.post(
            "/api/keys",
            {
                "name": name,
                "scopes": self.option("scopes") or "",
                "expires_in_days": int(self.option("expires-in-days") or 0),
            },
        )
        if self.wants_json:
            self.emit(result)
            return 0
        self.success(f"Created key “{result['name']}”.")
        self.blank()
        self.panel(result["secret"], title="Secret — shown once")
        self.warn("This secret is not stored and cannot be shown again. Copy it now.")
        return 0


class KeyRevoke(ApiCommand):
    """``janus keys revoke`` — Revoke an API key permanently.

    Arguments:
        ID
        --reason VALUE
        --yes
    """

    name = "keys revoke"
    help = "Revoke an API key permanently."
    arguments = [Argument("id"), Option("reason"), Flag("yes", short="y")]

    def run(self, api: ApiClient) -> int:
        """Call ``POST /api/keys/{id}/revoke`` and print the result (raw JSON with ``--json``);
        returns the exit code.
        """
        if not self.confirm_destructive(
            f"Revoke key {self.argument('id')}? This cannot be undone."
        ):
            self.muted("Cancelled.")
            return 1
        result = api.post(
            f"/api/keys/{int(self.argument('id'))}/revoke",
            {"reason": self.option("reason") or ""},
        )
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Revoked “{result['revoked']}”.")
        return 0


class KeyRotate(ApiCommand):
    """``janus keys rotate`` — Issue a new secret for a key and revoke the old one.

    Arguments:
        ID
    """

    name = "keys rotate"
    help = "Issue a new secret for a key and revoke the old one."
    arguments = [Argument("id")]

    def run(self, api: ApiClient) -> int:
        """Call ``POST /api/keys/{id}/rotate`` and print the result (raw JSON with ``--json``);
        returns the exit code.
        """
        result = api.post(f"/api/keys/{int(self.argument('id'))}/rotate")
        if self.wants_json:
            self.emit(result)
            return 0
        self.success(f"Rotated “{result['name']}”. The previous secret no longer works.")
        self.blank()
        self.panel(result["secret"], title="New secret — shown once")
        self.warn("This secret is not stored and cannot be shown again. Copy it now.")
        return 0
