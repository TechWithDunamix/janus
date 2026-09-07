"""How the CLI talks to Janus, and where it keeps its token.

**Why the CLI goes over HTTP.** Every administrative command could reach the
database directly — it is the same machine often enough. It deliberately does
not. Authorization in Janus is enforced in one place, the `@endpoint`
decorator in `routes/api/__init__.py`, and a CLI with its own database
connection would be a second path to every operation with none of that
enforcement on it. Running `janus routes delete` as an Analyst has to fail, and
the only way to be sure it fails is for it to be the same request the dashboard
would make.

The exceptions are marked and few: `migrate`, `seed`, `serve`, `worker` and the
initial `admin create` are *local* commands. They need the database rather than
the API, they cannot be performed by a signed-in user through any other route,
and `admin create` refuses to run once an account exists — see
`app/cli/admin.py`.
"""

from __future__ import annotations

import json as jsonlib
import os
import stat
from pathlib import Path
from typing import Any

import httpx

__all__ = ["ApiClient", "CliAuthRequired", "CliError", "clear_token", "load_token", "save_token"]


class CliError(Exception):
    """A failure with a message already fit to print."""


class CliAuthRequired(CliError):
    """No usable token. The message is the one the brief specifies verbatim."""

    def __init__(self) -> None:
        super().__init__("Authentication required.\nRun: janus login")


def _config_dir() -> Path:
    """Where the token lives.

    `JANUS_HOME` first so a test — or a second installation — can point
    somewhere else without touching the developer's real credentials.
    """
    override = os.getenv("JANUS_HOME")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~")) / ".config" / "janus"


def _token_path() -> Path:
    return _config_dir() / "credentials.json"


def save_token(url: str, token: str, email: str) -> Path:
    """Write the token, readable only by its owner.

    `0o600` on both the file and the directory. A bearer token that grants an
    operator's full authority over the gateway estate should not be
    world-readable in a shared home directory, and the default umask does not
    guarantee that.
    """
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, stat.S_IRWXU)
    payload = {"url": url.rstrip("/"), "token": token, "email": email}
    path.write_text(jsonlib.dumps(payload, indent=2))
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def load_token() -> dict[str, str] | None:
    path = _token_path()
    if not path.is_file():
        return None
    try:
        data = jsonlib.loads(path.read_text())
    except (ValueError, OSError):
        return None
    if not data.get("token"):
        return None
    return data


def clear_token() -> bool:
    path = _token_path()
    if path.is_file():
        path.unlink()
        return True
    return False


class ApiClient:
    """A thin wrapper over the Janus API.

    Every method returns decoded JSON or raises :class:`CliError` with a
    message already fit to print. Errors from the API are passed through
    verbatim — a 403 says which permission was missing, and rewording that in
    the CLI would lose the one detail that tells an operator what to ask for.
    """

    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        stored = load_token() or {}
        self.url = (url or os.getenv("JANUS_URL") or stored.get("url") or "http://localhost:8000").rstrip("/")
        self.token = token or os.getenv("JANUS_TOKEN") or stored.get("token")
        self.email = stored.get("email")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request(self, method: str, path: str, payload: Any = None, **params: Any) -> Any:
        if not self.token and not path.startswith("/api/auth/login"):
            raise CliAuthRequired()

        url = f"{self.url}{path}"
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.request(
                    method,
                    url,
                    headers=self._headers(),
                    content=None if payload is None else jsonlib.dumps(payload).encode(),
                    params={k: v for k, v in params.items() if v is not None},
                )
        except httpx.HTTPError as error:
            raise CliError(
                f"Could not reach Janus at {self.url}: {error}\n"
                "Is the control plane running? Set JANUS_URL to point elsewhere."
            ) from error

        if response.status_code == 401:
            raise CliAuthRequired()

        try:
            body = response.json()
        except ValueError:
            if response.is_success:
                return {}
            raise CliError(f"HTTP {response.status_code} from {path}") from None

        if not response.is_success:
            raise CliError(str(body.get("error") or f"HTTP {response.status_code}"))
        return body

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, None, **params)

    def post(self, path: str, payload: Any = None, **params: Any) -> Any:
        return self.request("POST", path, payload or {}, **params)

    def patch(self, path: str, payload: Any = None, **params: Any) -> Any:
        return self.request("PATCH", path, payload or {}, **params)

    def delete(self, path: str, **params: Any) -> Any:
        return self.request("DELETE", path, None, **params)
