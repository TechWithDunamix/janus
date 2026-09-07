"""Caddy module (plugin) management.

Caddy is a single static binary. Plugins are **compiled in**, not loaded at
runtime, which means Janus cannot install one into a running gateway the way a
package manager would. Pretending otherwise would be the dishonest option, so
this module does the four things that are actually possible:

1. **Detect** what a build provides, from `caddy list-modules --versions` and
   the Go module graph in `caddy build-info`.
2. **Explain** which Janus features each missing module would unlock, so the
   question "should I rebuild?" has an answer rather than a shrug.
3. **Generate** the exact `xcaddy build` command — *including every plugin
   already present*, because a rebuild replaces the binary and a command that
   lists only the new plugin silently drops the others.
4. **Record** what a gateway's build has, for the case Janus cannot introspect:
   a Caddy on another host is a different binary, and the local one says
   nothing about it.

That last point is the one worth being careful about. `caddy list-modules` runs
whatever binary is on *this* machine. For a remote gateway that is an
assumption, not an observation, and the UI labels it as such.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CATALOG",
    "BuildInfo",
    "CatalogEntry",
    "build_command",
    "script_command",
    "wanted_packages",
    "catalog_status",
    "detect",
    "entry_for_module",
]


@dataclass(frozen=True)
class CatalogEntry:
    """A plugin Janus knows about, and what it is for."""

    key: str
    name: str
    #: The Go package `xcaddy build --with` takes.
    package: str
    #: Module IDs the plugin registers. Presence of any one means it is loaded.
    provides: tuple[str, ...]
    summary: str
    #: What Janus can do once it is present. Empty means Janus does not use it
    #: directly — it is offered because it is useful at an edge, not because
    #: some Janus screen is waiting on it.
    unlocks: tuple[str, ...] = ()
    category: str = "general"
    homepage: str = ""
    #: Set where a plugin needs configuration Janus does not generate, so the
    #: UI can say "installing this is not the whole job".
    caveat: str = ""


#: The plugins Janus offers. Curated rather than exhaustive: a list of every
#: Caddy plugin in existence would be a directory, and a directory is not a
#: decision aid. These are the ones that matter at an API gateway.
CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        key="ratelimit",
        name="Rate limiting",
        package="github.com/mholt/caddy-ratelimit",
        provides=("http.handlers.rate_limit",),
        summary=(
            "Sliding-window rate limiting, keyed by any Caddy placeholder — client "
            "address, header, path or authenticated user."
        ),
        unlocks=(
            "Enforce the limits on the Rate Limits screen instead of only recording them",
            "Rate-limit actions in security policies",
        ),
        category="traffic",
        homepage="https://github.com/mholt/caddy-ratelimit",
    ),
    CatalogEntry(
        key="jwt",
        name="JWT authentication",
        package="github.com/ggicci/caddy-jwt",
        provides=("http.authentication.providers.jwt",),
        summary=(
            "Validates a JSON Web Token at the edge — signature, expiry and claims — "
            "and exposes its claims as placeholders for later matchers."
        ),
        unlocks=(
            "Verify JWTs at the gateway rather than only checking a header is present",
            "Route on a token claim, for the `role` and `scope` auth policies",
        ),
        category="security",
        homepage="https://github.com/ggicci/caddy-jwt",
        caveat=(
            "Needs your signing key or JWKS endpoint in the gateway's configuration. "
            "Janus does not hold signing keys, so that part is configured on the route."
        ),
    ),
    CatalogEntry(
        key="coraza",
        name="Coraza WAF",
        package="github.com/corazawaf/coraza-caddy/v2",
        provides=("http.handlers.waf",),
        summary=(
            "A web application firewall implementing the OWASP Core Rule Set. Inspects "
            "requests and bodies for injection, traversal and scanner signatures."
        ),
        unlocks=("Rule-based request inspection ahead of your upstreams",),
        category="security",
        homepage="https://github.com/corazawaf/coraza-caddy",
        caveat=(
            "The Core Rule Set is a separate download and needs tuning. An untuned WAF "
            "in blocking mode will refuse legitimate traffic on its first day."
        ),
    ),
    CatalogEntry(
        key="crowdsec",
        name="CrowdSec bouncer",
        package="github.com/hslatman/caddy-crowdsec-bouncer",
        provides=("http.handlers.crowdsec", "layer4.matchers.crowdsec"),
        summary=(
            "Blocks addresses from CrowdSec's community and local decision lists — "
            "reputation-based blocking that updates without you writing rules."
        ),
        unlocks=("Shared threat intelligence alongside your own blocklist",),
        category="security",
        homepage="https://github.com/hslatman/caddy-crowdsec-bouncer",
        caveat="Requires a running CrowdSec local API to pull decisions from.",
    ),
    CatalogEntry(
        key="cache",
        name="HTTP cache",
        package="github.com/caddyserver/cache-handler",
        provides=("http.handlers.cache",),
        summary=(
            "RFC-compliant HTTP caching in front of your upstreams, with support for "
            "stale-while-revalidate and several storage backends."
        ),
        unlocks=("Cache responses at the edge to take read load off a backend",),
        category="traffic",
        homepage="https://github.com/caddyserver/cache-handler",
    ),
    CatalogEntry(
        key="replace-response",
        name="Response rewriting",
        package="github.com/caddyserver/replace-response",
        provides=("http.handlers.replace_response",),
        summary=(
            "Substitutes strings or regular expressions in a response body as it "
            "streams through."
        ),
        unlocks=("Rewrite absolute URLs or leaked internal hostnames in a response",),
        category="traffic",
        homepage="https://github.com/caddyserver/replace-response",
    ),
    CatalogEntry(
        key="geoip",
        name="GeoIP matching",
        package="github.com/porech/caddy-maxmind-geolocation",
        provides=("http.matchers.maxmind_geolocation",),
        summary="Matches requests by country or subdivision using a MaxMind database.",
        unlocks=("Country-based policies, once a `country` subject is added",),
        category="security",
        homepage="https://github.com/porech/caddy-maxmind-geolocation",
        caveat="Needs a MaxMind GeoLite2 database file on the gateway host.",
    ),
    CatalogEntry(
        key="transform-encoder",
        name="Log formats",
        package="github.com/caddyserver/transform-encoder",
        provides=("caddy.logging.encoders.transform",),
        summary=(
            "Emits access logs in a custom or common format — Apache combined, or "
            "whatever a downstream collector expects."
        ),
        unlocks=(),
        category="observability",
        homepage="https://github.com/caddyserver/transform-encoder",
        caveat=(
            "Janus ingests Caddy's **JSON** access log. Switching the Janus logger to "
            "another encoder stops ingestion; use this for a second, separate logger."
        ),
    ),
    CatalogEntry(
        key="l4",
        name="Layer 4 proxying",
        package="github.com/mholt/caddy-l4",
        provides=("layer4",),
        summary=(
            "TCP and UDP proxying with connection matchers — for protocols that are "
            "not HTTP."
        ),
        unlocks=(),
        category="traffic",
        homepage="https://github.com/mholt/caddy-l4",
        caveat="Janus manages the HTTP app only. Layer 4 servers are configured outside it.",
    ),
    CatalogEntry(
        key="dns-cloudflare",
        name="Cloudflare DNS (ACME)",
        package="github.com/caddy-dns/cloudflare",
        provides=("dns.providers.cloudflare",),
        summary=(
            "Solves the ACME DNS-01 challenge through Cloudflare, which is what "
            "wildcard certificates and internal-only hostnames require."
        ),
        unlocks=("Wildcard certificates for domains that are not publicly reachable",),
        category="tls",
        homepage="https://github.com/caddy-dns/cloudflare",
        caveat="Needs a Cloudflare API token with DNS edit permission on the zone.",
    ),
    CatalogEntry(
        key="dns-route53",
        name="Route53 DNS (ACME)",
        package="github.com/caddy-dns/route53",
        provides=("dns.providers.route53",),
        summary="Solves the ACME DNS-01 challenge through AWS Route53.",
        unlocks=("Wildcard certificates for Route53-hosted zones",),
        category="tls",
        homepage="https://github.com/caddy-dns/route53",
        caveat="Needs AWS credentials with Route53 change permission.",
    ),
    CatalogEntry(
        key="events-exec",
        name="Event handlers",
        package="github.com/mholt/caddy-events-exec",
        provides=("events.handlers.exec",),
        summary="Runs a command when Caddy emits an event — a certificate renewal, say.",
        unlocks=(),
        category="observability",
        homepage="https://github.com/mholt/caddy-events-exec",
    ),
)


def entry_for_module(module_id: str) -> CatalogEntry | None:
    """The catalogue entry that provides a module id, if Janus knows one."""
    return next((e for e in CATALOG if module_id in e.provides), None)


@dataclass
class BuildInfo:
    """What a Caddy build contains.

    `source` says how this was determined, and the UI shows it: `binary` means
    the local `caddy` was asked, `declared` means an operator recorded it, and
    `unknown` means neither — in which case Janus assumes core modules only and
    declines to emit anything outside them.
    """

    modules: frozenset[str] = frozenset()
    version: str | None = None
    #: Go packages present in the build, from `caddy build-info`. Used to
    #: recognise a plugin even when its module ids are not in the catalogue.
    packages: frozenset[str] = frozenset()
    standard_count: int = 0
    non_standard: tuple[str, ...] = ()
    source: str = "unknown"
    error: str | None = None

    @property
    def known(self) -> bool:
        return self.source in {"binary", "declared"} and bool(self.modules)


async def detect(binary: str | None = None) -> BuildInfo:
    """Read a local Caddy binary's module list and build graph.

    Returns a `BuildInfo` with `source="unknown"` when no binary is reachable,
    rather than raising: not having Caddy on the control plane's host is a
    normal deployment, not an error.
    """
    path = binary or shutil.which("caddy")
    if not path:
        return BuildInfo(source="unknown", error="No caddy binary on PATH.")

    code, out, err = await _run(path, "list-modules", "--versions")
    if code != 0:
        return BuildInfo(source="unknown", error=(err or out).strip()[:300])

    modules: set[str] = set()
    non_standard: list[str] = []
    standard_count = 0
    in_non_standard = False

    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Caddy prints "Standard modules: N" / "Non-standard modules: N"
        # summary lines, and lists the non-standard ones under their own
        # heading. Both are parsed rather than only counted, because the point
        # is to name the plugins present, not to total them.
        lowered = line.lower()
        if lowered.startswith("standard modules:"):
            standard_count = _trailing_int(line)
            in_non_standard = False
            continue
        if lowered.startswith("non-standard modules:"):
            in_non_standard = True
            continue
        if lowered.startswith("unknown modules:"):
            in_non_standard = False
            continue

        module_id = line.split()[0]
        if "." not in module_id and module_id not in {"layer4"}:
            continue
        modules.add(module_id)
        if in_non_standard:
            non_standard.append(module_id)

    version = await _version(path)
    packages = await _packages(path)

    # Anything the catalogue provides that is not standard is worth listing as
    # non-standard even if Caddy's own grouping did not say so — the grouping
    # differs between versions.
    for entry in CATALOG:
        for provided in entry.provides:
            if provided in modules and provided not in non_standard:
                non_standard.append(provided)

    return BuildInfo(
        modules=frozenset(modules),
        version=version,
        packages=frozenset(packages),
        standard_count=standard_count,
        non_standard=tuple(sorted(set(non_standard))),
        source="binary",
    )


def catalog_status(build: BuildInfo) -> list[dict[str, Any]]:
    """The catalogue, annotated with whether each entry is present."""
    rows: list[dict[str, Any]] = []
    for entry in CATALOG:
        installed = any(module in build.modules for module in entry.provides)
        # A plugin can be compiled in and register no module id Janus knows —
        # the Go package graph catches that case.
        if not installed and build.packages:
            installed = any(entry.package in package for package in build.packages)

        rows.append(
            {
                "key": entry.key,
                "name": entry.name,
                "package": entry.package,
                "provides": list(entry.provides),
                "summary": entry.summary,
                "unlocks": list(entry.unlocks),
                "category": entry.category,
                "homepage": entry.homepage,
                "caveat": entry.caveat,
                "installed": installed,
            }
        )
    return rows


def wanted_packages(
    build: BuildInfo, adding: list[str], removing: list[str] | None = None
) -> list[str]:
    """The Go packages a rebuilt binary should contain: everything already
    present, plus everything being added, minus everything being removed —
    sorted and de-duplicated.

    "Removing" a plugin from a static binary means rebuilding without it, so
    that is what this expresses. Both build commands are computed from this;
    getting it wrong in one place and not the other is how the script and the
    `xcaddy` line would disagree about what they build.
    """
    drop = set(removing or [])
    wanted: set[str] = set()
    for entry in CATALOG:
        if entry.key in drop:
            continue
        installed = any(module in build.modules for module in entry.provides) or (
            bool(build.packages) and any(entry.package in p for p in build.packages)
        )
        if installed or entry.key in adding:
            wanted.add(entry.package)
    return sorted(wanted)


def build_command(
    build: BuildInfo,
    adding: list[str],
    version: str | None = None,
    removing: list[str] | None = None,
) -> str:
    """The `xcaddy build` line that produces the wanted binary.

    Includes everything **already installed** as well as what is being added,
    less what is being removed. That inclusion is the part people get wrong:
    `xcaddy build` produces a fresh binary from the packages named on the
    command line, so a command listing only the new plugin silently drops every
    plugin the current binary has. The result looks like a successful build and
    behaves like a downgrade.
    """
    lines = [f"xcaddy build {version or build.version or ''}".rstrip()]
    for package in wanted_packages(build, adding, removing):
        lines.append(f"    --with {package}")
    return " \\\n".join(lines)


def script_command(
    build: BuildInfo,
    adding: list[str],
    version: str | None = None,
    removing: list[str] | None = None,
) -> str:
    """The `scripts/setup-caddy.sh` line that does the whole rebuild.

    Same package set as :func:`build_command` — the script builds with `xcaddy`
    when a Go toolchain is present and downloads a custom build from
    caddyserver.com when it is not — but it also backs up the current binary,
    swaps the new one in, restarts Caddy and redeploys the gateway. It runs on
    the host Caddy lives on, which for a co-located install is this one.
    """
    packages = wanted_packages(build, adding, removing)
    ver = version or build.version
    if not packages:
        # Nothing left to compile in: a plain Caddy with no plugins. (If this
        # is undoing a rebuild you just ran, `--rollback --restart` restores
        # the backup instead — the screen says so.)
        if removing:
            lines = ["./scripts/setup-caddy.sh --plain"]
            if ver:
                lines.append(f"    --version {ver}")
            lines.append("    --restart --redeploy")
            return " \\\n".join(lines)
        return "./scripts/setup-caddy.sh --rollback"
    lines = ["./scripts/setup-caddy.sh"]
    if ver:
        lines.append(f"    --version {ver}")
    for package in packages:
        lines.append(f"    --with {package}")
    lines.append("    --restart --redeploy")
    return " \\\n".join(lines)


def unsupported_modules(build: BuildInfo) -> list[str]:
    """Non-standard modules present that the catalogue does not describe.

    Shown so an operator rebuilding knows the generated command is incomplete
    for their binary.
    """
    known = {module for entry in CATALOG for module in entry.provides}
    return sorted(module for module in build.non_standard if module not in known)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _trailing_int(line: str) -> int:
    try:
        return int(line.rsplit(":", 1)[-1].strip())
    except ValueError:
        return 0


async def _version(binary: str) -> str | None:
    code, out, _ = await _run(binary, "version")
    return out.strip().split()[0] if code == 0 and out.strip() else None


async def _packages(binary: str) -> set[str]:
    """Go module paths compiled into the binary, from `caddy build-info`."""
    code, out, _ = await _run(binary, "build-info")
    if code != 0:
        return set()
    packages: set[str] = set()
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] in {"dep", "mod"}:
            packages.add(parts[1].strip())
    return packages


async def _run(program: str, *args: str) -> tuple[int, str, str]:
    """Run a subprocess, reporting a missing binary rather than raising.

    A configured path that does not exist is a normal misconfiguration, and
    every caller here already handles a non-zero exit — turning it into an
    `OSError` would mean the same condition takes two forms depending on
    whether the path was guessed or given.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            program, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except OSError as error:
        return 1, "", str(error)
    out, err = await process.communicate()
    return process.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
