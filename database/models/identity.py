"""Who is using Janus, and the sessions and sign-in history behind them.

Janus is a single-tenant control plane: one installation manages one estate of
gateways, and everyone with an account is acting on that same estate. That is
why authorization here is *global* rather than scoped to an organisation, and
why this model mixes in the framework's :class:`~sillo.permissions.PermissionMixin`
instead of carrying its own role column.

The roles themselves are :class:`sillo.permissions.Group` rows and the
permissions are :class:`sillo.permissions.Permission` rows — see `app/authz.py`
for the catalogue and the bootstrap that creates them. Nothing in this file
re-implements either.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sillo.permissions import PermissionMixin
from sillo.record import Model
from sillo.record.fields import PasswordField
from sillo.users import UserBaseModel, UserManager
from tortoise import fields

__all__ = ["LoginEvent", "User", "UserSession"]


class User(UserBaseModel, PermissionMixin):
    """An operator of this Janus installation.

    `is_superuser` is the Owner escape hatch and is checked first by
    `app/authz.py`: the account that bootstrapped the installation must not be
    able to lock itself out by editing its own role. Everyone else's authority
    comes entirely from their groups.
    """

    objects = UserManager()

    #: Declared rather than inherited. `UserBaseModel` types this as a plain
    #: CharField, which stores exactly what it is handed — so assigning a raw
    #: password and saving writes the plaintext. `PasswordField` hashes on the
    #: way to the database.
    password = PasswordField()

    full_name = fields.CharField(max_length=150, null=True)
    #: Free text shown beside the name in the team list — "SRE, EU region".
    title = fields.CharField(max_length=120, null=True)
    timezone_name = fields.CharField(max_length=64, default="UTC")

    #: Set when an administrator disables the account. Distinct from
    #: `is_active` only in that it records *when*, for the audit trail;
    #: `is_active` is what the login path actually checks.
    disabled_at = fields.DatetimeField(null=True, default=None)
    disabled_reason = fields.CharField(max_length=255, null=True)

    #: An invitation that has not been accepted yet. Until it is, the account
    #: exists with an unusable password and cannot sign in.
    invited_at = fields.DatetimeField(null=True, default=None)
    invited_by_id = fields.IntField(null=True)

    class Meta:
        table = "users"

    def __str__(self) -> str:
        return self.email

    @property
    def name(self) -> str:
        """A display name that is always something."""
        return self.full_name or self.email.split("@")[0]

    @property
    def is_pending_invite(self) -> bool:
        return self.invited_at is not None and self.last_login is None

    async def disable(self, reason: str = "") -> None:
        self.is_active = False
        self.disabled_at = datetime.now(UTC)
        self.disabled_reason = reason or None
        await self.save()
        # Every live session goes with it. Leaving them would mean a disabled
        # account keeps working until its cookie happens to expire, which is
        # not what "disable" means to the person clicking it.
        await UserSession.filter(user_id=self.pk, revoked_at=None).update(
            revoked_at=datetime.now(UTC), revoked_reason="account disabled"
        )

    async def enable(self) -> None:
        self.is_active = True
        self.disabled_at = None
        self.disabled_reason = None
        await self.save()


class UserSession(Model):
    """A signed-in browser or CLI token, so both can be listed and revoked.

    Sillo's session middleware owns the cookie itself; this is Janus's record
    *about* that session — who, from where, since when — which is what makes
    "view sessions" and "revoke sessions" possible at all. Revocation is
    enforced on every request by `app/authz.py::session_is_live`, so revoking
    takes effect on the next request rather than whenever the cookie expires.

    The session key is stored as a SHA-256 digest, never in the clear: this
    table is read by the team screen, and a readable session key in a list an
    operator can see is a session anyone with that screen can steal.
    """

    id = fields.IntField(pk=True)
    user = fields.ForeignKeyField("models.User", related_name="sessions", on_delete=fields.CASCADE)

    key_hash = fields.CharField(max_length=64, unique=True, index=True)

    #: `web` for a browser session, `cli` for one minted by `janus login`.
    #: The CLI's is a bearer token rather than a cookie, but it is the same
    #: kind of thing — an authenticated identity with a lifetime — and giving
    #: it its own table would mean revoking sessions twice.
    kind = fields.CharField(max_length=16, default="web")

    ip = fields.CharField(max_length=64, null=True)
    user_agent = fields.CharField(max_length=400, null=True)
    label = fields.CharField(max_length=120, null=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    last_seen_at = fields.DatetimeField(null=True)
    expires_at = fields.DatetimeField(null=True)
    revoked_at = fields.DatetimeField(null=True, default=None)
    revoked_reason = fields.CharField(max_length=120, null=True)

    class Meta:
        table = "user_sessions"
        indexes = (("user_id", "revoked_at"),)

    def __str__(self) -> str:
        return f"{self.kind} session for user {self.user_id}"

    @property
    def is_live(self) -> bool:
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= datetime.now(UTC):
            return False
        return True


class LoginEvent(Model):
    """Every sign-in attempt, successful or not.

    Kept separate from the audit log because the volume and the retention are
    different: failed logins are a security signal that arrives in bursts, and
    the security dashboard counts them per hour. The audit log records what an
    *authenticated* actor changed.
    """

    id = fields.IntField(pk=True)
    #: Nullable because a failed attempt may name an address with no account,
    #: and recording the attempt matters more than being able to join it.
    user = fields.ForeignKeyField(
        "models.User", related_name="login_events", null=True, on_delete=fields.SET_NULL
    )
    email = fields.CharField(max_length=255, index=True)
    successful = fields.BooleanField(default=False)
    #: `bad_password`, `unknown_user`, `disabled`, `ok`. A string rather than
    #: an enum column so a new reason does not need a migration.
    reason = fields.CharField(max_length=40, default="ok")
    kind = fields.CharField(max_length=16, default="web")
    ip = fields.CharField(max_length=64, null=True)
    user_agent = fields.CharField(max_length=400, null=True)
    created_at = fields.DatetimeField(auto_now_add=True, index=True)

    class Meta:
        table = "login_events"
        indexes = (("successful", "created_at"),)

    def __str__(self) -> str:
        return f"{self.email} {'ok' if self.successful else self.reason}"
