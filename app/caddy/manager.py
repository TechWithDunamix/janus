"""CaddyManager — everything Janus knows how to say to Caddy.

This is the only class in the project that talks to a gateway. Nothing else
imports :mod:`httpx` for this purpose, nothing else knows the shape of an admin
API path, and nothing else knows that Caddy calls a backend pool an "upstream
list" rather than an upstream. Services above it deal in Janus's own vocabulary
and hand this object dictionaries.

Three properties hold the design together:

**Janus writes only what Janus owns.** Every write goes to
`apps/http/servers/<server_name>` or `logging/logs/<server_name>`. There is no
method here that replaces the configuration root, so a Caddy instance carrying
other servers keeps them across every Janus deployment. :meth:`read_full` reads
the whole configuration — drift detection needs to — but nothing writes it back.

**A rejected configuration changes nothing.** Caddy loads configuration
atomically: a payload that fails to provision is refused and the previously
running configuration keeps serving. That is Caddy's behaviour, not something
Janus arranges, and `tests/test_caddy.py` asserts it against a real binary so
the claim stays true.

**Validation happens before the gateway sees it.** :meth:`validate` runs the
generated payload through `caddy validate` when a binary is reachable, which
provisions the whole configuration in a throwaway process and reports exactly
what a real load would. Where no binary is available it falls back to the
structural checks in :mod:`app.caddy.config_builder`, and says which of the two
it did — a caller must be able to tell a proven configuration from a plausible
one.
"""

from __future__ import annotations

import asyncio
import json as jsonlib
import shutil
from dataclasses import dataclass, field
from typing import Any

from app.caddy.config_builder import build_logging, build_server
from app.caddy.errors import CaddyApplyError, CaddyUnreachable
from app.caddy.transport import SimulatedTransport, Transport, build_transport
from app.config import config

__all__ = ["CaddyManager", "GatewayStatus", "ValidationResult"]


@dataclass
class ValidationResult:
    """Whether a configuration is safe to apply, and how confidently we know.

    `method` is not decoration. `caddy` means a real Caddy provisioned this
    payload in a throwaway process and it worked; `structural` means only
    Janus's own checks ran. A deployment record keeps this so an operator
    reviewing an incident can tell which kind of "validated" they are looking
    at.
    """

    ok: bool
    method: str = "structural"
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class GatewayStatus:
    """What a health probe found."""

    reachable: bool
    version: str | None = None
    error: str | None = None
    simulated: bool = False
    #: Handler modules this build provides, as reported by `caddy list-modules`
    #: when a local binary is available. Empty means "core modules only were
    #: assumed", which is the safe reading: the generator then declines to emit
    #: anything outside core rather than emitting config that would be refused.
    capabilities: frozenset[str] = field(default_factory=frozenset)


class CaddyManager:
    """A conversation with one Caddy instance."""

    def __init__(
        self,
        *,
        admin_url: str | None = None,
        server_name: str | None = None,
        simulate: bool | None = None,
        timeout: float | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.admin_url = admin_url or config.caddy_admin_url
        self.server_name = server_name or config.caddy_server_name
        self.simulate = config.caddy_simulate if simulate is None else simulate
        self.timeout = timeout or config.caddy_timeout
        #: Injectable so a test can hand in a `SimulatedTransport` it also
        #: holds a reference to, and assert on what was written.
        self.transport: Transport = transport or build_transport(
            self.admin_url, simulate=self.simulate, timeout=self.timeout
        )
        #: Where this gateway's Caddy writes its access log. Set by
        #: `manager_for` from the gateway row.
        self.access_log: str = config.caddy_access_log

    # -- paths -------------------------------------------------------------

    @property
    def server_path(self) -> str:
        return f"/config/apps/http/servers/{self.server_name}"

    @property
    def logger_path(self) -> str:
        return f"/config/logging/logs/{self.server_name}"

    # -- reading -----------------------------------------------------------

    async def read_server(self) -> dict[str, Any] | None:
        """The Janus-owned server object as Caddy currently holds it."""
        return await self.transport.get(self.server_path)

    async def read_full(self) -> dict[str, Any] | None:
        """Caddy's entire configuration.

        Read-only, and used for exactly two things: reporting what else is
        running on the instance, and confirming during drift detection that the
        Janus server is where it is expected to be. Never written back.
        """
        return await self.transport.get("/config/")

    async def read_foreign_servers(self) -> list[str]:
        """Servers on this instance that Janus does not own.

        Surfaced in the UI so an operator can see that Janus is a tenant of
        this Caddy rather than its sole owner — which is the situation the
        "never overwrite what you do not own" rule exists for.
        """
        full = await self.read_full() or {}
        servers = full.get("apps", {}).get("http", {}).get("servers", {}) or {}
        return sorted(name for name in servers if name != self.server_name)

    # -- health ------------------------------------------------------------

    async def status(self) -> GatewayStatus:
        """Probe the admin API."""
        if isinstance(self.transport, SimulatedTransport):
            return GatewayStatus(
                reachable=True,
                version="simulated",
                simulated=True,
                capabilities=await self.capabilities(),
            )
        try:
            await self.transport.get("/config/")
        except CaddyUnreachable as error:
            return GatewayStatus(reachable=False, error=error.detail or str(error))
        except CaddyApplyError as error:
            return GatewayStatus(reachable=False, error=str(error))
        return GatewayStatus(
            reachable=True,
            version=await self._binary_version(),
            capabilities=await self.capabilities(),
        )

    async def _binary_version(self) -> str | None:
        binary = shutil.which("caddy")
        if not binary:
            return None
        code, out, _ = await _run(binary, "version")
        return out.strip().split()[0] if code == 0 and out.strip() else None

    async def capabilities(self) -> frozenset[str]:
        """Handler modules available to this gateway.

        Caddy's admin API does not expose its module list, so this shells out
        to a local binary when one exists. When it does not, the answer is the
        empty set and the generator emits core handlers only — the conservative
        direction, because emitting a handler the build lacks produces a
        rejected deployment, while omitting one produces a clearly reported
        "not enforced" warning.
        """
        binary = shutil.which("caddy")
        if not binary:
            return frozenset()
        code, out, _ = await _run(binary, "list-modules")
        if code != 0:
            return frozenset()
        return frozenset(line.strip() for line in out.splitlines() if line.strip())

    async def upstream_health(self) -> list[dict[str, Any]]:
        """What Caddy's own health checkers currently believe.

        `/reverse_proxy/upstreams` is Caddy's, not Janus's: these are live
        numbers from the process doing the proxying. Janus caches them onto
        `UpstreamTarget` for the dashboard but never substitutes its own
        opinion — a control plane guessing at backend health is a control plane
        that will eventually be wrong in an interesting way.
        """
        try:
            data = await self.transport.get("/reverse_proxy/upstreams")
        except (CaddyUnreachable, CaddyApplyError):
            return []
        return data if isinstance(data, list) else []

    async def metrics(self) -> str:
        """Caddy's Prometheus exposition, as text."""
        try:
            return await self.transport.raw("/metrics")
        except (CaddyUnreachable, CaddyApplyError):
            return ""

    # -- generating --------------------------------------------------------

    def generate(self, snapshot: dict[str, Any], capabilities: frozenset[str] = frozenset()):
        """Build the server object from a desired-state snapshot.

        The snapshot is plain data assembled by
        `app/services/configuration.py::snapshot`, deliberately not ORM
        objects: generation must be reproducible from a stored version, and a
        stored version cannot contain model instances.
        """
        return build_server(
            gateway=snapshot["gateway"],
            routes=snapshot["routes"],
            upstreams=snapshot["upstreams"],
            ip_rules=snapshot["ip_rules"],
            rate_limits=snapshot["rate_limits"],
            policies=snapshot["policies"],
            domains=snapshot.get("domains") or [],
            capabilities=capabilities,
            logger_name=self.server_name,
        )

    # -- validating --------------------------------------------------------

    async def validate(
        self, server: dict[str, Any], *, use_binary: bool = True
    ) -> ValidationResult:
        """Check a payload before any gateway sees it.

        Wraps the server object in a complete configuration first, because
        `caddy validate` provisions the whole thing — a bare server object is
        not a configuration and would fail for the wrong reason.

        Args:
            use_binary: Whether the local `caddy` is a valid judge of this
                payload. It is not when the gateway's capabilities were
                *declared* rather than detected: the operator has said the
                remote build differs from this host's, so this binary would
                reject handlers the real gateway supports and the failure would
                be about the wrong binary. Structural checks still run.
        """
        structural = _structural_check(server)
        if structural is not None:
            return ValidationResult(ok=False, method="structural", error=structural)

        binary = shutil.which("caddy") if use_binary else None
        if not binary:
            return ValidationResult(
                ok=True,
                method="structural",
                warnings=(
                    []
                    if use_binary
                    else [
                        "Validated structurally only: this gateway's modules are declared "
                        "rather than detected, so the local caddy binary is not a valid "
                        "judge of what its build accepts."
                    ]
                ),
            )

        full = {
            "admin": {"disabled": True},
            "apps": {"http": {"servers": {self.server_name: server}}},
        }
        code, out, err = await _run(
            binary,
            "validate",
            "--config",
            "-",
            "--adapter",
            "",
            stdin=jsonlib.dumps(full).encode(),
        )
        # The exit code is the authority: 0 for a configuration that
        # provisioned, 1 for one that did not. `Valid configuration` goes to
        # stdout and the `Error:` line to stderr, with Caddy's structured logs
        # interleaved on stderr either way — so neither stream alone is a
        # reliable signal, and matching on text was how an entirely valid
        # configuration first got reported here as rejected.
        if code != 0:
            message = _first_error_line(err) or "Caddy rejected the configuration."
            return ValidationResult(ok=False, method="caddy", error=message)
        return ValidationResult(
            ok=True,
            method="caddy",
            warnings=(
                []
                if "Valid configuration" in out
                else ["Caddy validated the configuration without confirming it."]
            ),
        )

    # -- applying ----------------------------------------------------------

    async def apply(self, server: dict[str, Any], *, log_path: str | None = None) -> None:
        """Write the Janus-owned subtrees.

        The logger goes first. If the server were applied first, Caddy would
        immediately start routing requests at a logger that does not exist yet
        and log them to the default sink instead — a gap in the access log at
        exactly the moment a deployment is most likely to be interesting.

        Raises:
            CaddyApplyError: Caddy refused it. The gateway is still running
                whatever it was running before.
        """
        await self._write(
            self.logger_path,
            build_logging(
                logger_name=self.server_name, log_path=log_path or self.access_log
            ),
        )
        await self._write(self.server_path, server)

    async def _write(self, path: str, payload: Any) -> None:
        """Set the object at `path`, creating the parents it needs.

        Two things about Caddy's admin API make this more than one call:

        * `POST` sets or replaces an object, but only when its *parent* path
          already exists. A Caddy that has never had logging configured has no
          `logging` key at all, and posting to `/config/logging/logs/janus`
          fails with `invalid traversal path`.
        * `PUT` creates, and refuses a path that already exists.

        So the walk goes down from the shallowest missing ancestor: the first
        parent that does not exist is created with just enough structure to
        hold this one object, and everything below it comes along inside that
        write. Nothing outside Janus's own subtree is touched, because the
        object being created contains only Janus's key.
        """
        segments = [segment for segment in path.strip("/").split("/") if segment]
        if segments and segments[0] == "config":
            segments = segments[1:]

        # Find the deepest existing ancestor.
        depth = len(segments) - 1
        while depth > 0:
            ancestor = "/config/" + "/".join(segments[:depth])
            if await self.transport.get(ancestor) is not None:
                break
            depth -= 1

        if depth == len(segments) - 1:
            # Every parent is present; a plain set is all that is needed.
            await self.transport.post(path, payload)
            return

        # Wrap the payload in the missing levels and create them in one write.
        nested: Any = payload
        for segment in reversed(segments[depth + 1 :]):
            nested = {segment: nested}
        target = "/config/" + "/".join(segments[: depth + 1])
        if await self.transport.get(target) is None:
            await self.transport.put(target, nested)
        else:
            await self.transport.post(target, nested)

    async def verify(self, expected: dict[str, Any]) -> bool:
        """Confirm the gateway is running what was just sent.

        Compares only the keys Janus writes. Caddy normalises a configuration
        on load — it fills in defaults and reorders object keys — so a
        byte-for-byte comparison reports drift on a perfectly correct
        deployment. Comparing the route list and the listen address is the
        strongest check that does not produce false positives.
        """
        live = await self.read_server()
        if live is None:
            return False
        return live.get("listen") == expected.get("listen") and jsonlib.dumps(
            live.get("routes"), sort_keys=True
        ) == jsonlib.dumps(expected.get("routes"), sort_keys=True)

    async def remove(self) -> None:
        """Delete the Janus-owned subtrees, leaving everything else."""
        await self.transport.delete(self.server_path)
        await self.transport.delete(self.logger_path)

    # -- drift -------------------------------------------------------------

    async def detect_drift(self, expected: dict[str, Any]) -> tuple[bool, str | None]:
        """Whether the gateway has stopped matching the desired state.

        Returns `(drifted, description)`. Three distinct outcomes, and the
        description says which, because they need different responses: the
        server is missing entirely (someone reloaded Caddy without Janus), the
        route list differs (someone edited it by hand), or the listen address
        moved.
        """
        live = await self.read_server()
        if live is None:
            return True, "The Janus server is not present in the gateway's configuration."
        if live.get("listen") != expected.get("listen"):
            return True, (
                f"Listen address is {live.get('listen')}, expected {expected.get('listen')}."
            )
        live_routes = jsonlib.dumps(live.get("routes"), sort_keys=True)
        want_routes = jsonlib.dumps(expected.get("routes"), sort_keys=True)
        if live_routes != want_routes:
            live_count = len(live.get("routes") or [])
            want_count = len(expected.get("routes") or [])
            if live_count != want_count:
                return True, (
                    f"The gateway is running {live_count} routes; "
                    f"the desired configuration has {want_count}."
                )
            return True, "Route definitions differ from the desired configuration."
        return False, None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _structural_check(server: dict[str, Any]) -> str | None:
    """Janus's own checks, which run whether or not a binary is available."""
    if not server.get("listen"):
        return "The server has no listen address."
    routes = server.get("routes")
    if routes is None or not isinstance(routes, list):
        return "The server has no route list."
    for index, route in enumerate(routes):
        if "handle" not in route:
            return f"Route {index} has no handler."
        for handler in route.get("handle", []):
            if not handler.get("handler"):
                return f"Route {index} has a handler with no name."
    return None


def _first_error_line(text: str) -> str | None:
    """Caddy's message out of its log noise.

    `caddy validate` writes structured JSON log lines to stderr and then a
    plain `Error: …` line. Only the last is worth showing an operator.
    """
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped.startswith("Error:"):
            return stripped[len("Error:") :].strip()
    return None


async def _run(
    program: str, *args: str, stdin: bytes | None = None
) -> tuple[int, str, str]:
    """Run a subprocess, returning `(code, stdout, stderr)`.

    Used only for the local `caddy` binary — validation and the module list.
    Never for anything that touches a running gateway: those go through the
    admin API, which is the supported interface and the only one available when
    Caddy is on another host.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            program,
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        # A binary that vanished between `which` and here, or a path with no
        # execute bit. Every caller already handles a non-zero exit; raising
        # would make the same condition take two shapes.
        return 1, "", str(error)
    out, err = await process.communicate(stdin)
    return process.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def manager_for(gateway: Any) -> CaddyManager:
    """The manager for a :class:`~database.models.Gateway` row."""
    manager = CaddyManager(
        admin_url=gateway.admin_url or config.caddy_admin_url,
        simulate=config.caddy_simulate,
    )
    # Each gateway logs to its own file; see `Gateway.access_log`.
    manager.access_log = gateway.access_log_path
    return manager
