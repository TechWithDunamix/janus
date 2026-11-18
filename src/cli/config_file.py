"""Validate and seed Janus gateway resources from a versioned YAML file."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from cli.client import ApiClient, CliError


class _UniqueKeysLoader(yaml.SafeLoader):
    """Refuse ambiguous repeated YAML fields."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise yaml.constructor.ConstructorError(
                    None, None, "YAML field names must be strings", key_node.start_mark
                )
            if key in result:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate YAML field: {key}", key_node.start_mark
                )
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _mapping(value: Any, where: str, allowed: set[str]) -> dict:
    if not isinstance(value, dict):
        raise CliError(f"{where} must be a mapping")
    unknown = set(value) - allowed
    if unknown:
        raise CliError(f"{where}: unknown fields: {', '.join(sorted(unknown))}")
    return value


def _text(value: Any, where: str, maximum: int = 255) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise CliError(f"{where} must be text of 1–{maximum} characters")
    return value.strip()


def _slug(value: Any, where: str, maximum: int = 100) -> str:
    value = _text(value, where, maximum)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", value):
        raise CliError(f"{where} must use lowercase letters, digits, and hyphens")
    return value


def _integer(value: Any, where: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise CliError(f"{where} must be an integer >= {minimum}")
    return value


def _list(value: Any, where: str) -> list:
    if not isinstance(value, list):
        raise CliError(f"{where} must be a list")
    return value


def _identity(items: list, field: str, where: str) -> None:
    values = [item[field] for item in items]
    if len(values) != len(set(values)):
        raise CliError(f"{where} contains duplicate {field} values")


def load_config(path: str) -> dict:
    """Validate the complete file before any remote writes occur."""
    try:
        raw = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=_UniqueKeysLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise CliError(f"cannot read YAML: {exc}") from exc
    doc = _mapping(raw, "document", {"version", "gateways", "api_keys"})
    if type(doc.get("version")) is not int or doc["version"] != 1:
        raise CliError("version must be 1")
    gateways = _list(doc.get("gateways", []), "gateways")
    keys = _list(doc.get("api_keys", []), "api_keys")
    for i, gateway in enumerate(gateways):
        where = f"gateways[{i}]"
        _mapping(
            gateway,
            where,
            {"slug", "name", "admin_url", "listen", "region", "upstreams", "routes", "rate_limits"},
        )
        gateway["slug"] = _slug(gateway.get("slug"), f"{where}.slug")
        gateway["name"] = _text(gateway.get("name"), f"{where}.name", 100)
        for field, maximum in (("admin_url", 255), ("listen", 100), ("region", 60)):
            if field in gateway:
                gateway[field] = _text(gateway[field], f"{where}.{field}", maximum)
        upstreams = _list(gateway.get("upstreams", []), f"{where}.upstreams")
        routes = _list(gateway.get("routes", []), f"{where}.routes")
        limits = _list(gateway.get("rate_limits", []), f"{where}.rate_limits")
        for j, upstream in enumerate(upstreams):
            location = f"{where}.upstreams[{j}]"
            _mapping(upstream, location, {"slug", "name", "policy", "health_path", "targets"})
            upstream["slug"] = _slug(upstream.get("slug"), f"{location}.slug")
            upstream["name"] = _text(upstream.get("name"), f"{location}.name", 100)
            if "policy" in upstream:
                upstream["policy"] = _text(upstream["policy"], f"{location}.policy", 40)
            if "health_path" in upstream:
                upstream["health_path"] = _text(upstream["health_path"], f"{location}.health_path")
            targets = _list(upstream.get("targets", []), f"{location}.targets")
            if not targets:
                raise CliError(f"{location}.targets must contain at least one dial")
            for k, target in enumerate(targets):
                target_where = f"{location}.targets[{k}]"
                if isinstance(target, str):
                    _text(target, target_where)
                else:
                    _mapping(target, target_where, {"dial", "weight"})
                    target["dial"] = _text(target.get("dial"), f"{target_where}.dial")
                    if "weight" in target:
                        _integer(target["weight"], f"{target_where}.weight", 1)
            dials = [t if isinstance(t, str) else t["dial"] for t in targets]
            if len(dials) != len(set(dials)):
                raise CliError(f"{location}.targets contains duplicate dials")
        _identity(upstreams, "slug", f"{where}.upstreams")
        for j, route in enumerate(routes):
            location = f"{where}.routes[{j}]"
            _mapping(
                route,
                location,
                {
                    "slug",
                    "name",
                    "path",
                    "methods",
                    "upstream",
                    "priority",
                    "action",
                    "auth_policy",
                    "timeout_seconds",
                    "retries",
                    "strip_prefix",
                    "redirect_to",
                },
            )
            route["slug"] = _slug(route.get("slug"), f"{location}.slug", 120)
            route["name"] = _text(route.get("name"), f"{location}.name", 120)
            for field, maximum in (
                ("path", 500),
                ("methods", 120),
                ("upstream", 100),
                ("strip_prefix", 255),
                ("redirect_to", 500),
            ):
                if field in route:
                    route[field] = _text(route[field], f"{location}.{field}", maximum)
            if "path" in route and not route["path"].startswith("/"):
                raise CliError(f"{location}.path must start with /")
            if "priority" in route:
                _integer(route["priority"], f"{location}.priority")
            for field, minimum in (("timeout_seconds", 1), ("retries", 0)):
                if field in route:
                    _integer(route[field], f"{location}.{field}", minimum)
            if route.get("action", "proxy") not in {"proxy", "redirect"}:
                raise CliError(f"{location}.action must be proxy or redirect")
            if route.get("auth_policy", "public") not in {
                "public",
                "api_key",
                "jwt",
                "user",
                "role",
                "scope",
            }:
                raise CliError(f"{location}.auth_policy is invalid")
            if route.get("action", "proxy") == "proxy" and not route.get("upstream"):
                raise CliError(f"{location}.upstream is required for a proxy route")
            if route.get("action") == "redirect" and not route.get("redirect_to"):
                raise CliError(f"{location}.redirect_to is required for a redirect route")
            if route.get("action") == "redirect" and route.get("upstream"):
                raise CliError(f"{location}.upstream cannot be set on a redirect route")
        _identity(routes, "slug", f"{where}.routes")
        for j, limit in enumerate(limits):
            location = f"{where}.rate_limits[{j}]"
            _mapping(limit, location, {"name", "key", "limit", "window_seconds", "burst"})
            limit["name"] = _text(limit.get("name"), f"{location}.name", 120)
            if limit.get("key", "ip") not in {
                "ip",
                "api_key",
                "user",
                "route",
                "domain",
                "client",
                "global",
            }:
                raise CliError(f"{location}.key is invalid")
            _integer(limit.get("limit"), f"{location}.limit", 1)
            for field, minimum in (("window_seconds", 1), ("burst", 0)):
                if field in limit:
                    _integer(limit[field], f"{location}.{field}", minimum)
        _identity(limits, "name", f"{where}.rate_limits")
    _identity(gateways, "slug", "gateways")
    _identity(gateways, "name", "gateways")
    for i, key in enumerate(keys):
        where = f"api_keys[{i}]"
        _mapping(key, where, {"name", "scopes", "expires_in_days"})
        key["name"] = _text(key.get("name"), f"{where}.name", 120)
        scopes = _list(key.get("scopes", []), f"{where}.scopes")
        if any(
            not isinstance(s, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,120}", s) for s in scopes
        ) or len(scopes) != len(set(scopes)):
            raise CliError(f"{where}.scopes must contain unique, nonempty strings")
        if "expires_in_days" in key:
            _integer(key["expires_in_days"], f"{where}.expires_in_days")
    _identity(keys, "name", "api_keys")
    return doc


def plan(api: ApiClient, doc: dict) -> tuple[list[dict], dict[str, dict]]:
    """Read the real control API and preview missing resources."""
    gateway_rows = api.get("/api/gateways")["gateways"] if doc.get("gateways") else []
    existing = {g["slug"]: g for g in gateway_rows}
    names = {g["name"]: g for g in gateway_rows}
    actions: list[dict] = []
    for gateway in doc.get("gateways", []):
        slug = gateway["slug"]
        current = existing.get(slug)
        if not current and gateway["name"] in names:
            raise CliError(f"gateway name {gateway['name']} already belongs to another slug")
        actions.append({"action": "skip" if current else "create", "kind": "gateway", "name": slug})
        resources = {}
        if current:
            for kind, path, field in (
                ("upstream", "/api/upstreams", "upstreams"),
                ("route", "/api/routes", "routes"),
                ("rate_limit", "/api/security/rate-limits", "rate_limits"),
            ):
                if gateway.get(field) or (kind == "upstream" and gateway.get("routes")):
                    resources[kind] = {
                        r["slug" if kind != "rate_limit" else "name"]: r
                        for r in api.get(path, gateway=slug)[field]
                    }
        for kind, field in (
            ("upstream", "upstreams"),
            ("route", "routes"),
            ("rate_limit", "rate_limits"),
        ):
            present = resources.get(kind, {})
            for item in gateway.get(field, []):
                identity = item["name" if kind == "rate_limit" else "slug"]
                actions.append(
                    {
                        "action": "skip" if identity in present else "create",
                        "kind": kind,
                        "name": f"{slug}/{identity}",
                    }
                )
        known_upstreams = set(resources.get("upstream", {})) | {
            u["slug"] for u in gateway.get("upstreams", [])
        }
        for route in gateway.get("routes", []):
            if route.get("upstream") and route["upstream"] not in known_upstreams:
                raise CliError(
                    f"route {slug}/{route['slug']} references unknown upstream {route['upstream']}"
                )
    by_name: dict[str, list[dict]] = {}
    key_rows = api.get("/api/keys")["keys"] if doc.get("api_keys") else []
    for key in key_rows:
        by_name.setdefault(key["name"], []).append(key)
    for key in doc.get("api_keys", []):
        matches = by_name.get(key["name"], [])
        active = [row for row in matches if row["status"] == "active"]
        if len(active) > 1 or (matches and not active):
            raise CliError(
                f"key {key['name']} is duplicated or inactive; use a new name for replacement"
            )
        if active and set(active[0]["scopes"]) != set(key.get("scopes", [])):
            raise CliError(
                f"key {key['name']} has different scopes; use a new name for replacement"
            )
        actions.append(
            {"action": "skip" if active else "create", "kind": "key", "name": key["name"]}
        )
    return actions, existing


def apply(
    api: ApiClient, doc: dict, on_secret: Callable[[dict], None] | None = None
) -> tuple[list[dict], list[dict]]:
    """Seed missing resources through the API; emit each secret as it is issued."""
    actions, existing = plan(api, doc)
    creates = {(a["kind"], a["name"]) for a in actions if a["action"] == "create"}
    secrets: list[dict] = []
    for gateway in doc.get("gateways", []):
        slug = gateway["slug"]
        if ("gateway", slug) in creates:
            existing[slug] = api.post(
                "/api/gateways",
                {
                    k: v
                    for k, v in gateway.items()
                    if k not in {"upstreams", "routes", "rate_limits"}
                },
            )
        for upstream in gateway.get("upstreams", []):
            if ("upstream", f"{slug}/{upstream['slug']}") in creates:
                api.post("/api/upstreams", upstream, gateway=slug)
        for route in gateway.get("routes", []):
            if ("route", f"{slug}/{route['slug']}") in creates:
                api.post("/api/routes", route, gateway=slug)
        for limit in gateway.get("rate_limits", []):
            if ("rate_limit", f"{slug}/{limit['name']}") in creates:
                api.post("/api/security/rate-limits", limit, gateway=slug)
    for key in doc.get("api_keys", []):
        if ("key", key["name"]) in creates:
            payload = {**key, "scopes": ",".join(key.get("scopes", []))}
            response = api.post("/api/keys", payload)
            issued = {"name": key["name"], "secret": response["secret"]}
            secrets.append(issued)
            if on_secret:
                on_secret(issued)
    return actions, secrets
