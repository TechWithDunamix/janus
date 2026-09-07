"""How a Caddy admin API call actually travels.

Two implementations behind one protocol:

* :class:`HttpTransport` speaks to a real Caddy over HTTP, through the client
  the rest of the project already depends on.
* :class:`SimulatedTransport` implements the same admin API contract in
  process, against a dictionary.

The simulator exists so the whole control plane — generation, validation,
apply, verify, drift, rollback — can be exercised on a laptop and in CI without
a Caddy binary. It is a stand-in for the *admin API's contract*, not for Caddy:
it does not proxy anything, and it cannot tell you whether a configuration will
actually provision. Everything produced while it is in use is marked
`simulated` in the database and labelled in the UI, so nothing that came out of
it is ever presented as a real gateway observation.

Choosing between them is `CADDY_SIMULATE`, and `check_production` refuses to
start a production process with the simulator on.
"""

from __future__ import annotations

import copy
import json as jsonlib
from typing import Any, Protocol

import httpx

from app.caddy.errors import CaddyApplyError, CaddyUnreachable

__all__ = ["HttpTransport", "SimulatedTransport", "Transport", "build_transport"]


class Transport(Protocol):
    """The five calls the manager makes. Nothing else touches the network."""

    async def get(self, path: str) -> Any: ...
    async def post(self, path: str, payload: Any) -> None: ...
    async def put(self, path: str, payload: Any) -> None: ...
    async def patch(self, path: str, payload: Any) -> None: ...
    async def delete(self, path: str) -> None: ...
    async def raw(self, path: str) -> str: ...


class HttpTransport:
    """The real thing: Caddy's admin API over HTTP.

    Every method maps onto one documented endpoint. `path` is the admin API
    path (`/config/apps/http/servers/janus`), never a full URL — the base
    address belongs to the transport so the manager never assembles one.
    """

    def __init__(self, base_url: str, *, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    async def _request(self, method: str, path: str, payload: Any = None) -> httpx.Response:
        url = self._url(path)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                return await client.request(
                    method,
                    url,
                    # `content` with an explicit header rather than `json=`:
                    # Caddy is strict about the content type on a config write
                    # and rejects a body sent without it, with a message about
                    # the payload rather than the header.
                    content=None if payload is None else jsonlib.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"} if payload is not None else {},
                )
        except httpx.HTTPError as error:
            raise CaddyUnreachable(url, str(error)) from error

    @staticmethod
    def _check(response: httpx.Response) -> None:
        """Turn a non-2xx into a `CaddyApplyError` carrying Caddy's message.

        Caddy answers a rejected configuration with a JSON body whose `error`
        names the module and route index that failed. That message is the most
        useful thing in the whole exchange, so it is unwrapped and passed on
        rather than being reduced to a status code.
        """
        if response.is_success:
            return
        detail = response.text
        try:
            body = response.json()
            if isinstance(body, dict) and "error" in body:
                detail = str(body["error"])
        except ValueError:
            pass
        raise CaddyApplyError(detail, status=response.status_code)

    async def get(self, path: str) -> Any:
        response = await self._request("GET", path)
        # A path that does not exist yet is not an error — an unconfigured
        # gateway has no Janus server, and the caller wants `None` for that,
        # not an exception it has to catch on every read.
        #
        # Caddy says "absent" two different ways, and only one of them is a
        # 404. Reading a path whose *parent* is missing — `/config/logging/logs`
        # on an instance that has never had logging configured — answers 400
        # with `invalid traversal path`. Treating that as a failure is how
        # `apply` came to report a traversal error instead of creating the
        # subtree it was about to write.
        if response.status_code == 404:
            return None
        if response.status_code == 400 and "invalid traversal path" in response.text:
            return None
        self._check(response)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    async def post(self, path: str, payload: Any) -> None:
        """Set or replace the object at `path`.

        The only verb that is idempotent for an object. Caddy's `PUT` *creates*
        and fails with `key already exists` when the path is already there, and
        its `PATCH` *replaces* and fails with 404 when it is not — so a
        deployment written against either one works exactly once and then
        breaks, in opposite directions depending on which was chosen. `POST`
        does the right thing on both a fresh gateway and a redeploy.
        """
        self._check(await self._request("POST", path, payload))

    async def put(self, path: str, payload: Any) -> None:
        self._check(await self._request("PUT", path, payload))

    async def patch(self, path: str, payload: Any) -> None:
        self._check(await self._request("PATCH", path, payload))

    async def delete(self, path: str) -> None:
        response = await self._request("DELETE", path)
        if response.status_code == 404:
            return
        self._check(response)

    async def raw(self, path: str) -> str:
        """A plain-text endpoint — `/metrics` is the only one Janus reads."""
        response = await self._request("GET", path)
        self._check(response)
        return response.text


class SimulatedTransport:
    """An in-process stand-in with the admin API's semantics.

    It holds a config dictionary and walks the same `/config/...` paths a real
    Caddy does, including the two behaviours the manager actually depends on:
    a missing path reads as `None`, and a write to a nested path creates the
    intermediate objects.

    It also refuses configurations the real one refuses, for the handful of
    mistakes the generator could plausibly make — an unknown handler, a server
    with no listen address. That is not a substitute for `caddy validate`; it
    is enough that a test asserting "a bad configuration is rejected and the
    old one survives" is testing something real.
    """

    #: Handler names the generator is allowed to emit. Kept here so the
    #: simulator rejects a typo the same way Caddy would, rather than silently
    #: accepting a configuration that would fail against the real thing.
    KNOWN_HANDLERS = frozenset(
        {
            "reverse_proxy",
            "static_response",
            "rewrite",
            "headers",
            "subroute",
            "vars",
            "error",
            "encode",
            "authentication",
            "request_body",
        }
    )

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = initial or {"apps": {"http": {"servers": {}}}}
        #: Every accepted write, so a test can assert what was sent.
        self.writes: list[tuple[str, Any]] = []

    # -- path walking ------------------------------------------------------

    @staticmethod
    def _segments(path: str) -> list[str]:
        cleaned = path.strip("/")
        if cleaned.startswith("config"):
            cleaned = cleaned[len("config") :].strip("/")
        return [segment for segment in cleaned.split("/") if segment]

    def _resolve(self, segments: list[str]) -> Any:
        node: Any = self._config
        for segment in segments:
            if not isinstance(node, dict) or segment not in node:
                return None
            node = node[segment]
        return node

    # -- validation --------------------------------------------------------

    def _validate(self, payload: Any) -> None:
        """Reject what a real Caddy would reject, for the cases that matter."""
        servers: dict[str, Any] = {}
        if isinstance(payload, dict) and "listen" in payload:
            servers = {"_": payload}
        elif isinstance(payload, dict):
            servers = payload.get("apps", {}).get("http", {}).get("servers", {}) or {}
            if not servers and all(isinstance(v, dict) for v in payload.values()):
                servers = {k: v for k, v in payload.items() if isinstance(v, dict) and "listen" in v}

        for name, server in servers.items():
            if not server.get("listen"):
                raise CaddyApplyError(
                    f"loading http app module: provision http: server {name}: "
                    "server cannot have empty listen address"
                )
            for index, route in enumerate(server.get("routes", []) or []):
                self._validate_handlers(name, index, route)

    def _validate_handlers(self, server: str, index: int, route: Any) -> None:
        for handler in route.get("handle", []) or []:
            name = handler.get("handler")
            if name not in self.KNOWN_HANDLERS:
                raise CaddyApplyError(
                    f"loading http app module: provision http: server {server}: "
                    f"setting up route handlers: route {index}: loading handler modules: "
                    f"position 0: loading module '{name}': unknown module: http.handlers.{name}"
                )
            # `subroute` nests a whole route list, which is how every Janus
            # route with a rewrite or a header transform is built — so the
            # check has to recurse or it never sees the real handlers.
            for nested_index, nested in enumerate(handler.get("routes", []) or []):
                self._validate_handlers(server, nested_index, nested)

    # -- the Transport protocol -------------------------------------------

    async def get(self, path: str) -> Any:
        return copy.deepcopy(self._resolve(self._segments(path)))

    async def put(self, path: str, payload: Any) -> None:
        self._validate(payload)
        segments = self._segments(path)
        if not segments:
            self._config = copy.deepcopy(payload)
        else:
            node: dict[str, Any] = self._config
            for segment in segments[:-1]:
                node = node.setdefault(segment, {})
            node[segments[-1]] = copy.deepcopy(payload)
        self.writes.append((path, copy.deepcopy(payload)))

    async def post(self, path: str, payload: Any) -> None:
        """Set or replace, exactly as Caddy's POST does."""
        await self.put(path, payload)

    async def patch(self, path: str, payload: Any) -> None:
        # Caddy's PATCH replaces the object at the path and 404s when it does
        # not exist. The manager only ever patches a path it has just read, so
        # matching that behaviour keeps the two transports interchangeable.
        segments = self._segments(path)
        if self._resolve(segments) is None:
            raise CaddyApplyError(f"unknown object path: {path}", status=404)
        await self.put(path, payload)

    async def delete(self, path: str) -> None:
        segments = self._segments(path)
        if not segments:
            return
        node: Any = self._config
        for segment in segments[:-1]:
            if not isinstance(node, dict) or segment not in node:
                return
            node = node[segment]
        if isinstance(node, dict):
            node.pop(segments[-1], None)

    async def raw(self, path: str) -> str:
        """Caddy exposes Prometheus text; the simulator has no counters.

        Returning an empty exposition rather than fabricated numbers is the
        point: aggregate statistics in a simulated installation come from the
        seeded request rows, which are labelled as simulated, and never from
        an invented metrics scrape that would look identical to a real one.
        """
        return ""


def build_transport(base_url: str, *, simulate: bool, timeout: float = 15.0) -> Transport:
    """The transport for a gateway, chosen by configuration."""
    if simulate:
        return SimulatedTransport()
    return HttpTransport(base_url, timeout=timeout)
