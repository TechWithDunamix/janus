"""What can go wrong between Janus and Caddy.

Three failure modes, three types, because the caller does something different
with each: a transport failure is retried, a validation failure is shown to the
operator with the offending configuration, and an apply failure means the
gateway is still running the *previous* configuration and the deployment record
needs to say so.
"""

from __future__ import annotations

__all__ = ["CaddyApplyError", "CaddyError", "CaddyUnreachable", "CaddyValidationError"]


class CaddyError(Exception):
    """Base for every failure in the Caddy integration."""


class CaddyUnreachable(CaddyError):
    """The admin API could not be reached at all.

    Distinct from a rejected configuration: nothing was attempted, so the
    gateway's state is unchanged and unknown rather than known-bad.
    """

    def __init__(self, url: str, detail: str = "") -> None:
        super().__init__(f"Caddy admin API unreachable at {url}" + (f": {detail}" if detail else ""))
        self.url = url
        self.detail = detail


class CaddyValidationError(CaddyError):
    """A configuration was rejected before it was applied.

    Carries Caddy's own message, which names the module and the route index
    that failed — far more useful than anything Janus could reconstruct, so it
    is passed through verbatim to the operator.
    """

    def __init__(self, message: str, *, payload: object | None = None) -> None:
        super().__init__(message)
        self.payload = payload


class CaddyApplyError(CaddyError):
    """Caddy refused a configuration at load time.

    The important property, and the reason this is not fatal: Caddy loads
    configuration atomically. A rejected load leaves the previously running
    configuration in place and still serving. Verified against Caddy 2.11 —
    see `tests/test_caddy.py::test_a_rejected_config_leaves_the_old_one_serving`.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
