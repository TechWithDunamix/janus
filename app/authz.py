"""Roles, permissions, and the one function that answers "may they?".

Janus's RBAC *is* the framework's. Roles are :class:`sillo.permissions.Group`
rows, permissions are :class:`sillo.permissions.Permission` rows, and the
membership tables between them are the framework's too. This module supplies
the catalogue and the default role definitions, and exposes a single check that
the route guard, the CLI and the shared props all call.

That "single check" is the load-bearing part. The web dashboard and the CLI are
two front doors onto the same operations, and the way a CLI becomes an RBAC
bypass is by growing its own idea of who may do what. Here both end up in
:func:`require`, so a permission added to a route is a permission the CLI
enforces on the next call without anyone remembering to update it.
"""

from __future__ import annotations

from typing import Any

from sillo.core.http import HttpContext
from sillo.permissions import Group, Permission

__all__ = [
    "ALL_PERMISSIONS",
    "DEFAULT_ROLES",
    "PERMISSION_NAMES",
    "PermissionDenied",
    "can",
    "ensure_roles",
    "permissions_for",
    "permissions_of",
    "require",
    "role_names_of",
]


class PermissionDenied(Exception):
    """Raised when the actor may not do what was asked.

    Carries the permission so the 403 screen and the CLI's error can both name
    the missing one, rather than a generic refusal that leaves an operator
    guessing which role they need.
    """

    def __init__(self, permission: str) -> None:
        super().__init__(f"Missing permission: {permission}")
        self.permission = permission


#: Every permission Janus recognises, grouped as the roles screen renders them.
#:
#: `read` and `write` rather than a verb per operation. The finer split was
#: tried and thrown away: `routes.create`, `routes.update`, `routes.delete` and
#: `routes.enable` are four permissions no operator has ever wanted to hold
#: separately, and every additional one is another thing a role can be
#: accidentally missing. Where a distinction genuinely matters — rolling back a
#: configuration is not the same authority as writing one — it gets its own
#: name.
ALL_PERMISSIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "Gateway": (
        ("gateway.read", "View gateways and their status"),
        ("gateway.write", "Create, edit and delete gateways"),
        ("routes.read", "View routes"),
        ("routes.write", "Create, edit, enable and delete routes"),
        ("upstreams.read", "View upstreams and targets"),
        ("upstreams.write", "Create, edit and delete upstreams"),
        ("domains.read", "View domains and TLS status"),
        ("domains.write", "Add and edit domains"),
    ),
    "Configuration": (
        ("configuration.read", "View generated configuration and history"),
        ("configuration.write", "Generate and deploy configuration"),
        ("configuration.rollback", "Roll back to an earlier version"),
    ),
    "Analytics": (("analytics.read", "View analytics and reports"),),
    "Security": (
        ("security.read", "View clients, rules, limits and policies"),
        ("security.write", "Block clients, edit rules, limits and policies"),
        ("apikeys.read", "View API keys"),
        ("apikeys.write", "Create, rotate and revoke API keys"),
    ),
    "Team": (
        ("users.read", "View users and sessions"),
        ("users.write", "Invite, disable and remove users"),
        ("roles.read", "View roles and permissions"),
        ("roles.write", "Create roles and assign permissions"),
        ("audit.read", "View the audit log"),
    ),
}

#: Flat, for validation and for the CLI's `roles permissions` output.
PERMISSION_NAMES: tuple[str, ...] = tuple(
    name for group in ALL_PERMISSIONS.values() for name, _ in group
)


def _every_permission() -> tuple[str, ...]:
    return PERMISSION_NAMES


#: The five roles a fresh installation gets, and what each one holds.
#:
#: The shape to notice is that each is a superset of the one below it, except
#: Analyst — which is deliberately *not* on that ladder. An analyst reads
#: traffic data and nothing about the gateway's configuration, which is a
#: different axis from "how much may they change".
DEFAULT_ROLES: dict[str, dict[str, Any]] = {
    "Owner": {
        "description": "Everything, including roles and ownership.",
        "permissions": _every_permission(),
    },
    "Administrator": {
        "description": "Full operational management, including team and keys.",
        "permissions": tuple(p for p in PERMISSION_NAMES if p != "roles.write"),
    },
    "Operator": {
        "description": "Gateway, route, upstream and security management.",
        "permissions": (
            "gateway.read",
            "gateway.write",
            "routes.read",
            "routes.write",
            "upstreams.read",
            "upstreams.write",
            "domains.read",
            "domains.write",
            "configuration.read",
            "configuration.write",
            "analytics.read",
            "security.read",
            "security.write",
            "apikeys.read",
            # Read-only visibility of who holds what. An operator who can read
            # the audit log and sees "role.permissions_changed" needs to be
            # able to look at the role it names; withholding that made the
            # model incoherent rather than tighter.
            "roles.read",
            "users.read",
            "audit.read",
        ),
    },
    "Analyst": {
        "description": "Analytics and reporting, with no configuration access.",
        "permissions": (
            "gateway.read",
            "routes.read",
            "upstreams.read",
            "domains.read",
            "analytics.read",
            "security.read",
        ),
    },
    "Viewer": {
        "description": "Read-only across the control plane.",
        "permissions": tuple(p for p in PERMISSION_NAMES if p.endswith(".read")),
    },
}


async def ensure_roles() -> dict[str, Group]:
    """Create the permission catalogue and the default roles if absent.

    Idempotent, and safe to run on every boot and from `janus migrate`. It
    *adds* permissions to the default roles but never removes them: an
    installation that deliberately narrowed Operator should not have that
    undone by a deploy. New permissions introduced by an upgrade do get granted
    to Owner, because a permission no account holds is a feature nobody can
    reach.
    """
    for group_label, entries in ALL_PERMISSIONS.items():
        for name, description in entries:
            await Permission.define(name, f"{group_label}: {description}")

    roles: dict[str, Group] = {}
    for name, spec in DEFAULT_ROLES.items():
        role = await Group.get_or_create(name, spec["description"])
        held = set(await role.get_permissions())
        missing = [p for p in spec["permissions"] if p not in held]
        # Owner always converges on the full catalogue; the others are only
        # populated when they are new, so local edits survive an upgrade.
        if missing and (name == "Owner" or not held):
            await role.add_permissions(*missing)
        roles[name] = role
    return roles


async def permissions_of(user: Any) -> set[str]:
    """Everything a user may do.

    A superuser holds everything by definition — that is the escape hatch that
    keeps the installation recoverable when someone edits the Owner role badly.
    Everyone else's authority is the union of their groups' permissions and any
    granted directly, which is what the framework's mixin already computes.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    if getattr(user, "is_superuser", False):
        return set(PERMISSION_NAMES)
    return await user.load_permissions()


async def role_names_of(user: Any) -> list[str]:
    if user is None or not getattr(user, "is_authenticated", False):
        return []
    return sorted(await user.get_groups())


def _user_of(ctx: HttpContext) -> Any | None:
    """`ctx.user`, or `None` — never raising.

    A session naming a row that no longer loads leaves `scope["user"]` unset
    and the property raises. That has to read as "signed out", not as a 500 on
    every page.
    """
    try:
        user = ctx.user
    except Exception:  # noqa: BLE001
        return None
    return user if getattr(user, "is_authenticated", False) else None


async def permissions_for(ctx: HttpContext) -> list[str]:
    """The current request's permissions, sorted — what the UI reads to hide
    controls it knows will refuse. Cosmetic: :func:`require` is the gate."""
    return sorted(await permissions_of(_user_of(ctx)))


async def can(actor: Any, *permissions: str) -> bool:
    """Whether an actor holds every named permission."""
    if not permissions:
        return True
    held = await permissions_of(actor)
    return all(permission in held for permission in permissions)


async def require(actor: Any, *permissions: str) -> None:
    """Raise :class:`PermissionDenied` unless the actor holds them all.

    The single choke point. The HTTP guard in `routes/web/_kit.py`, the API
    guard and every CLI command reach this same function, which is what stops
    the CLI from becoming a way around the dashboard's authorization.
    """
    if not permissions:
        return
    held = await permissions_of(actor)
    for permission in permissions:
        if permission not in held:
            raise PermissionDenied(permission)
