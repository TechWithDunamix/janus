"""The Caddy integration layer.

Everything Janus knows about Caddy lives under this package, and nothing
outside it constructs an admin API request. Services import
:class:`CaddyManager` and speak Janus's vocabulary; the translation into
Caddy's happens here and only here.
"""

from app.caddy.config_builder import CAPABILITY_RATE_LIMIT, build_logging, build_server
from app.caddy.errors import (
    CaddyApplyError,
    CaddyError,
    CaddyUnreachable,
    CaddyValidationError,
)
from app.caddy.manager import CaddyManager, GatewayStatus, ValidationResult, manager_for
from app.caddy.transport import HttpTransport, SimulatedTransport, build_transport

__all__ = [
    "CADDY_CAPABILITY_RATE_LIMIT",
    "CaddyApplyError",
    "CaddyError",
    "CaddyManager",
    "CaddyUnreachable",
    "CaddyValidationError",
    "GatewayStatus",
    "HttpTransport",
    "SimulatedTransport",
    "ValidationResult",
    "build_logging",
    "build_server",
    "build_transport",
    "manager_for",
]

CADDY_CAPABILITY_RATE_LIMIT = CAPABILITY_RATE_LIMIT
