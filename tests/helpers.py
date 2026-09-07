"""Builders and sign-in helpers the tests share.

Separate from `conftest.py` on purpose: another project on the path also has a
`tests` package with a conftest in it, and importing helpers out of a conftest
is how a test ends up loading the wrong one. A named module cannot be mistaken.
"""

from __future__ import annotations

from database.models import Domain, Gateway, Upstream, UpstreamTarget, User

__all__ = [
    "api_token",
    "auth_headers",
    "make_domain",
    "make_gateway",
    "make_upstream",
    "make_user",
    "sign_in",
]

PASSWORD = "correct-horse-battery"


async def make_user(
    email: str = "operator@test.local",
    *,
    role: str | None = "Operator",
    password: str = PASSWORD,
    superuser: bool = False,
    active: bool = True,
) -> User:
    """A user holding exactly one role."""
    from sillo.permissions import Group

    user = User(
        email=email,
        username=email,
        full_name=email.split("@")[0],
        is_active=active,
        is_superuser=superuser,
    )
    user.set_password(password)
    await user.save()
    if role:
        group = await Group.get_or_none(name=role)
        if group is not None:
            await group.add_user(user)
    return user


async def make_gateway(name: str = "Test gateway", **kwargs) -> Gateway:
    return await Gateway.create(
        name=name,
        slug=kwargs.pop("slug", name.lower().replace(" ", "-")),
        listen=kwargs.pop("listen", ":8080"),
        **kwargs,
    )


async def make_upstream(
    gateway: Gateway,
    name: str = "api-service",
    dials: tuple[str, ...] = ("10.0.0.1:8000",),
) -> Upstream:
    upstream = await Upstream.create(
        gateway_id=gateway.pk, name=name, slug=name, policy="weighted_round_robin"
    )
    for index, dial in enumerate(dials, start=1):
        await UpstreamTarget.create(upstream_id=upstream.pk, dial=dial, weight=index)
    return upstream


async def make_domain(gateway: Gateway, hostname: str = "api.test.local") -> Domain:
    return await Domain.create(gateway_id=gateway.pk, hostname=hostname, tls_mode="off")


async def sign_in(client, user: User, password: str = PASSWORD) -> None:
    """Sign a user in through the real login route.

    Deliberately not by writing the session directly: the login path is where
    the session shape, the CSRF exchange and the `UserSession` row are decided,
    and a test that skipped it would not notice any of them breaking.
    """
    await client.get("/login")
    response = await client.post(
        "/login",
        json={"email": user.email, "password": password},
        headers=csrf(client),
    )
    assert response.status_code == 302, f"sign-in failed: {response.status_code} {response.text[:200]}"


async def api_token(client, user: User, password: str = PASSWORD) -> str:
    """A CLI bearer token, minted the way `janus login` mints one."""
    response = await client.post(
        "/api/auth/login", json={"email": user.email, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def csrf(client) -> dict[str, str]:
    """Headers for a browser-style POST."""
    return {"X-XSRF-TOKEN": client.cookies.get("XSRF-TOKEN") or ""}
