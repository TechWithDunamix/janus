"""YAML setup is validated and seeded through Janus's real control API."""

from __future__ import annotations

import asyncio
import json

import pytest

from cli import build_console
from cli.client import CliError
from cli.config_file import apply, load_config, plan
from database.models import ApiKey, Gateway, GatewayRoute, RateLimit, Upstream
from modules.services import apikeys
from tests.helpers import api_token, auth_headers


def write_config(tmp_path):
    file = tmp_path / "janus.yml"
    file.write_text("""\
version: 1
gateways:
  - slug: edge
    name: Edge
    listen: ':8080'
    upstreams:
      - slug: orders
        name: Orders API
        targets:
          - dial: 127.0.0.1:9000
            weight: 2
    routes:
      - slug: orders-api
        name: Orders route
        path: /api/orders*
        methods: GET,POST
        upstream: orders
        priority: 100
        auth_policy: api_key
    rate_limits:
      - name: orders-ip
        key: ip
        limit: 100
        window_seconds: 60
api_keys:
  - name: orders-client
    scopes: [orders:read]
    expires_in_days: 90
""")
    return file


async def test_apply_uses_real_app_and_is_repeatable(client, owner, tmp_path):
    token = await api_token(client, owner)
    headers = auth_headers(token)
    loop = asyncio.get_running_loop()

    class AppApi:
        def request(self, method, path, payload=None, **params):
            response = asyncio.run_coroutine_threadsafe(
                client.request(method, path, json=payload, params=params, headers=headers), loop
            ).result(timeout=10)
            assert response.status_code < 400, response.text
            return response.json()

        def get(self, path, **params):
            return self.request("GET", path, **params)

        def post(self, path, payload=None, **params):
            return self.request("POST", path, payload, **params)

    api = AppApi()
    doc = load_config(str(write_config(tmp_path)))
    actions, _ = await asyncio.to_thread(plan, api, doc)
    assert [a["action"] for a in actions] == ["create"] * 5
    shown = []
    actions, secrets = await asyncio.to_thread(apply, api, doc, shown.append)
    assert len(secrets) == 1 and shown == secrets
    assert secrets[0]["secret"].startswith("jan_")
    assert await Gateway.filter(slug="edge").count() == 1
    assert await Upstream.filter(slug="orders").count() == 1
    assert await GatewayRoute.filter(slug="orders-api").count() == 1
    assert await RateLimit.filter(name="orders-ip").count() == 1
    assert await ApiKey.filter(name="orders-client").count() == 1
    issued_key = await apikeys.verify(secrets[0]["secret"])
    assert issued_key is not None and issued_key.scope_list == ["orders:read"]
    listed = await client.get("/api/keys", headers=headers)
    assert listed.status_code == 200
    assert "secret" not in listed.text
    generated = await client.get("/api/config", params={"gateway": "edge"}, headers=headers)
    assert generated.status_code == 200, generated.text
    assert "127.0.0.1:9000" in json.dumps(generated.json()["config"])
    actions, secrets = await asyncio.to_thread(apply, api, doc, shown.append)
    assert all(a["action"] == "skip" for a in actions)
    assert secrets == [] and len(shown) == 1


def test_cli_commands_show_keys_once(tmp_path, monkeypatch, capsys):
    class FakeApi:
        gateways = []
        keys = []

        def get(self, path, **params):
            if path == "/api/gateways":
                return {"gateways": self.gateways}
            if path == "/api/keys":
                return {"keys": self.keys}
            return {"upstreams": [], "routes": [], "rate_limits": []}

        def post(self, path, payload=None, **params):
            if path == "/api/keys":
                self.keys.append(
                    {
                        "name": payload["name"],
                        "status": "active",
                        "scopes": payload["scopes"].split(","),
                    }
                )
                return {"secret": "jan_secret_once"}
            if path == "/api/gateways":
                self.gateways.append(payload)
                return payload
            return {}

    file = tmp_path / "keys.yml"
    file.write_text("version: 1\napi_keys:\n  - name: app\n    scopes: [orders:read]\n")
    api = FakeApi()
    monkeypatch.setattr("cli.base.ApiClient", lambda: api)
    assert build_console().run(["config", "file", "plan", str(file)]) == 0
    assert "jan_secret_once" not in capsys.readouterr().out
    assert build_console().run(["config", "file", "apply", str(file)]) == 0
    assert "NEW KEY app: jan_secret_once" in capsys.readouterr().out
    assert build_console().run(["config", "file", "apply", str(file)]) == 0
    assert "jan_secret_once" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "content",
    [
        "version: 2\n",
        "version: 1\nversion: 1\n",
        "version: 1\ngateways: [{slug: edge, name: Edge, routes: [{slug: api, name: API}]}]\n",
        "version: 1\napi_keys: [{name: a, scopes: [x, x]}]\n",
        "version: 1\ngateways: [{slug: Edge, name: Edge}]\n",
    ],
)
def test_invalid_files_fail_before_writes(tmp_path, content):
    file = tmp_path / "invalid.yml"
    file.write_text(content)
    with pytest.raises(CliError):
        load_config(str(file))
