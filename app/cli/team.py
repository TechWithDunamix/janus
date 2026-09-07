"""janus admin / users / roles."""

from __future__ import annotations

from sillo.console import Argument, Flag, Option

from app.cli.base import ApiCommand, LocalCommand
from app.cli.client import ApiClient

__all__ = [
    "AdminCreate", "AdminDelete", "AdminDisable", "AdminEnable", "AdminList",
    "RoleAssign", "RoleCreate", "RoleList", "RolePermissions",
    "UserCreate", "UserDelete", "UserDisable", "UserEnable", "UserList", "UserRole",
]


class AdminCreate(LocalCommand):
    """Create the first account, or refuse.

    The one command that writes a user without an authenticated actor, and it
    is deliberately restricted to the case where there is no one to
    authenticate as: an installation with zero accounts cannot be administered
    any other way. The moment an account exists, this refuses and points at
    `janus users create`, which goes through the API and therefore through
    RBAC.

    Without that refusal, the CLI would be a permanent way around
    authorization for anyone with shell access — and "anyone with shell access
    can already read the database" is not the same as "the CLI should hand them
    an Owner account".
    """

    name = "admin create"
    help = "Create the first administrator (bootstrap only)."

    arguments = [
        Option("email", help="Address for the new account."),
        Option("name", help="Display name."),
        Option("role", default="Owner", help="Role to grant."),
    ]

    async def run_async(self) -> int:
        from sillo.permissions import Group

        from app.authz import ensure_roles
        from app.services import audit
        from database.models import User

        if await User.all().exists():
            self.error("This installation already has accounts.")
            self.muted("Use `janus users create`, which enforces permissions.")
            return 1

        email = (self.option("email") or self.ask("Email")).strip().lower()
        if "@" not in email:
            self.error("A valid email address is required.")
            return 1

        name = self.option("name") or self.ask("Full name")
        password = self.secret("Password", confirm=True)
        if len(password) < 10:
            self.error("Choose a password of at least 10 characters.")
            return 1

        await ensure_roles()
        user = User(
            email=email, username=email, full_name=name, is_active=True, is_superuser=True
        )
        user.set_password(password)
        await user.save()

        role = await Group.get_or_none(name=self.option("role"))
        if role is not None:
            await role.add_user(user)

        await audit.record(
            action="user.created", resource_type="user", resource_id=user.pk,
            resource_label=user.email, actor=user, actor_label=f"{user.email} (bootstrap)",
            after={"email": user.email, "role": self.option("role")}, origin="cli",
        )
        self.success(f"Created {email} as {self.option('role')}.")
        self.muted("Sign in with: janus login")
        return 0


class _UserList(ApiCommand):
    def run(self, api: ApiClient) -> int:
        users = api.get("/api/users")["users"]
        if self.wants_json:
            self.emit(users)
            return 0
        self.table(
            ["ID", "EMAIL", "NAME", "ROLES", "ACTIVE", "LAST LOGIN"],
            [
                [
                    u["id"], u["email"], u["name"] or "—", ", ".join(u["roles"]) or "—",
                    "yes" if u["active"] else "no", u["last_login"] or "never",
                ]
                for u in users
            ],
        )
        return 0


class UserList(_UserList):
    name = "users list"
    help = "List users."


class AdminList(_UserList):
    name = "admin list"
    help = "List users (alias of `users list`)."


class UserCreate(ApiCommand):
    name = "users create"
    help = "Create a user."
    arguments = [
        Option("email"), Option("name"),
        Option("role", default="Viewer", help="Role to grant."),
        Flag("superuser", help="Grant Owner-level override."),
    ]

    def run(self, api: ApiClient) -> int:
        email = (self.option("email") or self.ask("Email")).strip().lower()
        name = self.option("name") or ""
        # Prompted, never a flag: a password on the command line ends up in
        # shell history and in the process list.
        password = self.secret("Password", confirm=True)
        result = api.post(
            "/api/users",
            {
                "email": email, "name": name, "password": password,
                "role": self.option("role"), "superuser": self.flag("superuser"),
            },
        )
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Created {result['email']} as {result['role']}.")
        return 0


class _UserToggle(ApiCommand):
    enabled = True

    def run(self, api: ApiClient) -> int:
        result = api.post(
            "/api/users/toggle",
            {"email": self.argument("email"), "enabled": self.enabled,
             "reason": self.option("reason") or ""},
        )
        if self.wants_json:
            self.emit(result)
        else:
            self.success(
                f"{result['email']} is now {'active' if result['active'] else 'disabled'}."
            )
            if not result["active"]:
                self.muted("Their sessions were revoked.")
        return 0


class UserEnable(_UserToggle):
    name = "users enable"
    help = "Enable a user."
    enabled = True
    arguments = [Argument("email"), Option("reason")]


class UserDisable(_UserToggle):
    name = "users disable"
    help = "Disable a user and revoke their sessions."
    enabled = False
    arguments = [Argument("email"), Option("reason")]


class AdminEnable(UserEnable):
    name = "admin enable"
    help = "Enable a user (alias of `users enable`)."


class AdminDisable(UserDisable):
    name = "admin disable"
    help = "Disable a user (alias of `users disable`)."


class UserDelete(ApiCommand):
    name = "users delete"
    help = "Delete a user."
    arguments = [Argument("email"), Flag("yes", short="y")]

    def run(self, api: ApiClient) -> int:
        if not self.confirm_destructive(f"Delete user {self.argument('email')}?"):
            self.muted("Cancelled.")
            return 1
        result = api.post("/api/users/delete", {"email": self.argument("email")})
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Deleted {result['deleted']}.")
        return 0


class AdminDelete(UserDelete):
    name = "admin delete"
    help = "Delete a user (alias of `users delete`)."


class UserRole(ApiCommand):
    name = "users role"
    help = "Change a user's role."
    arguments = [Argument("email"), Argument("role")]

    def run(self, api: ApiClient) -> int:
        result = api.post(
            "/api/users/role", {"email": self.argument("email"), "role": self.argument("role")}
        )
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"{result['email']} is now {result['role']}.")
        return 0


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


class RoleList(ApiCommand):
    name = "roles list"
    help = "List roles."

    def run(self, api: ApiClient) -> int:
        roles = api.get("/api/roles")["roles"]
        if self.wants_json:
            self.emit(roles)
            return 0
        self.table(
            ["NAME", "MEMBERS", "PERMISSIONS", "DESCRIPTION"],
            [
                [r["name"], r["members"], len(r["permissions"]), r["description"] or ""]
                for r in roles
            ],
        )
        return 0


class RolePermissions(ApiCommand):
    name = "roles permissions"
    help = "Show what a role may do."
    arguments = [Argument("role")]

    def run(self, api: ApiClient) -> int:
        roles = api.get("/api/roles")["roles"]
        found = next((r for r in roles if r["name"].lower() == self.argument("role").lower()), None)
        if found is None:
            self.error(f"No role '{self.argument('role')}'.")
            return 1
        if self.wants_json:
            self.emit(found)
            return 0
        self.line(found["name"])
        self.muted(found["description"] or "")
        self.blank()
        for permission in found["permissions"]:
            self.bullet(permission)
        return 0


class RoleCreate(ApiCommand):
    name = "roles create"
    help = "Create a role."
    arguments = [
        Option("name"), Option("description"),
        Option("permissions", help="Comma-separated permission names."),
    ]

    def run(self, api: ApiClient) -> int:
        name = self.option("name") or self.ask("Role name")
        permissions = [
            p.strip() for p in (self.option("permissions") or "").split(",") if p.strip()
        ]
        result = api.post(
            "/api/roles",
            {"name": name, "description": self.option("description") or "",
             "permissions": permissions},
        )
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"Created role “{result['name']}” with {len(permissions)} permission(s).")
        return 0


class RoleAssign(ApiCommand):
    name = "roles assign"
    help = "Assign a role to a user."
    arguments = [Argument("role"), Argument("email")]

    def run(self, api: ApiClient) -> int:
        result = api.post(
            "/api/users/role", {"email": self.argument("email"), "role": self.argument("role")}
        )
        if self.wants_json:
            self.emit(result)
        else:
            self.success(f"{result['email']} is now {result['role']}.")
        return 0
