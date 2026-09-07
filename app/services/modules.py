"""Which Caddy modules a gateway's build has, and what that costs it.

Two sources, and the difference between them is reported rather than hidden:

* **Detected** — the local `caddy` binary was asked. Reliable, and only
  meaningful when that binary *is* the gateway's binary.
* **Declared** — an operator recorded what they built. The only option for a
  Caddy on another host, and an assumption rather than an observation.

When neither is available Janus assumes core modules only. That is the
conservative direction: omitting a handler produces a clearly reported "not
enforced" warning, while emitting one the build lacks produces a rejected
deployment.
"""

from __future__ import annotations

from typing import Any

from app.caddy.modules import (
    CATALOG,
    BuildInfo,
    build_command,
    catalog_status,
    detect,
    entry_for_module,
    script_command,
    unsupported_modules,
)
from app.config import config
from database.models import Gateway

__all__ = ["build_for", "feature_gaps", "overview", "record_declared"]


async def build_for(gateway: Gateway) -> BuildInfo:
    """What this gateway's Caddy build provides.

    A declaration wins over local detection, because the operator who built the
    remote binary knows more about it than this machine's `caddy` does.
    """
    declared = gateway.declared_modules or None
    if declared:
        return BuildInfo(
            modules=frozenset(declared),
            version=gateway.caddy_version,
            source="declared",
        )

    detected = await detect()
    if detected.source == "binary":
        # Only meaningful when the local binary is the gateway's binary. That
        # is true for a single-host install and for the Docker image, which
        # ships `caddy` alongside Janus precisely so this holds.
        return detected
    return BuildInfo(source="unknown", error=detected.error)


#: Janus features that need a module, and what happens without it. Written out
#: rather than derived, because "what you lose" is a product statement.
FEATURES: tuple[dict[str, Any], ...] = (
    {
        "module": "http.handlers.rate_limit",
        "feature": "Rate limit enforcement",
        "without": (
            "Limits are stored and displayed but not enforced. Janus will not emit a "
            "handler it knows the build would reject, and says so on every deployment."
        ),
        "screens": ("Rate limits", "Policies"),
    },
    {
        "module": "http.authentication.providers.jwt",
        "feature": "JWT verification at the edge",
        "without": (
            "A jwt auth policy checks only that an Authorization header is present. "
            "The token's validity is left to the upstream."
        ),
        "screens": ("Routes",),
    },
    {
        "module": "http.handlers.waf",
        "feature": "Request inspection",
        "without": "No WAF stage. Policies still match on path, method, header and address.",
        "screens": ("Policies",),
    },
    {
        "module": "http.matchers.maxmind_geolocation",
        "feature": "Country matching",
        "without": "Policies cannot match on country.",
        "screens": ("Policies",),
    },
)


def feature_gaps(build: BuildInfo) -> list[dict[str, Any]]:
    """Janus features unavailable on this build, and what each would need."""
    gaps: list[dict[str, Any]] = []
    for feature in FEATURES:
        if feature["module"] in build.modules:
            continue
        entry = entry_for_module(feature["module"])
        gaps.append(
            {
                **feature,
                "plugin": entry.name if entry else feature["module"],
                "package": entry.package if entry else None,
                "key": entry.key if entry else None,
            }
        )
    return gaps


async def overview(
    gateway: Gateway,
    adding: list[str] | None = None,
    removing: list[str] | None = None,
) -> dict[str, Any]:
    """Everything the Modules screen and `janus modules` need.

    `adding` and `removing` are catalogue keys the operator has staged on the
    screen; the rebuild commands are regenerated from them so the page can show
    the effect of a change before it is made.
    """
    build = await build_for(gateway)
    catalog = catalog_status(build)

    return {
        "source": build.source,
        "version": build.version,
        "error": build.error,
        "module_count": len(build.modules),
        "standard_count": build.standard_count,
        "non_standard": list(build.non_standard),
        # Non-standard modules present that the catalogue does not describe.
        # Surfaced so an operator knows the generated build command is
        # incomplete for their binary rather than discovering it after a
        # rebuild.
        "unrecognised": unsupported_modules(build),
        "catalog": catalog,
        "installed": [row for row in catalog if row["installed"]],
        "available": [row for row in catalog if not row["installed"]],
        "gaps": feature_gaps(build),
        "build_command": build_command(build, adding or [], removing=removing or []),
        "script_command": script_command(build, adding or [], removing=removing or []),
        # Stated on the screen: a plugin is compiled into the binary, so there
        # is no install button that could honestly exist.
        "requires_rebuild": True,
        "simulated": config.caddy_simulate,
    }


async def record_declared(gateway: Gateway, modules: list[str] | None) -> Gateway:
    """Record the module ids an operator says this gateway's build has.

    Passing `None` clears the declaration and returns the gateway to local
    detection.
    """
    gateway.declared_modules = sorted(set(modules)) if modules else None
    await gateway.save()
    return gateway


def catalog_keys() -> list[str]:
    return [entry.key for entry in CATALOG]
