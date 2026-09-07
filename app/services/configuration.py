"""The configuration engine.

Everything Janus sends to a gateway goes through :func:`deploy`, and it runs
the same five stages every time:

    generate → validate → apply → verify → mark active

Each stage can stop the pipeline, and stopping is always safe. Generation and
validation happen entirely inside Janus, so a configuration that fails either
one never reaches the gateway at all. Apply is atomic in Caddy — a rejected
load leaves the previous configuration running — so even a failure at that
stage leaves a serving gateway. Verify reads back what is live and compares,
which is what catches the case where Caddy accepted the payload but is not
running what was asked for.

**Versions are the unit of change.** Every deployment is a
:class:`~database.models.ConfigVersion` holding the complete generated payload,
not a diff. Rolling back is therefore not an inverse operation with its own
bugs: it is a deployment of an earlier version's stored bytes, through this
same pipeline, with the same validation.
"""

from __future__ import annotations

import json as jsonlib
from datetime import UTC, datetime
from typing import Any

from tortoise.transactions import in_transaction

from app.caddy import CaddyApplyError, CaddyManager, CaddyUnreachable
from app.caddy.manager import manager_for
from app.config import config
from app.services import audit
from database.models import (
    ConfigVersion,
    Deployment,
    Domain,
    Gateway,
    GatewayRoute,
    IpRule,
    RateLimit,
    SecurityPolicy,
    Upstream,
    checksum_of,
)

__all__ = [
    "DeployResult",
    "deploy",
    "detect_drift",
    "diff_versions",
    "generate",
    "history",
    "rollback",
    "snapshot",
]


class DeployResult:
    """What a deployment did, in a form the UI, the API and the CLI all render."""

    def __init__(
        self,
        *,
        ok: bool,
        stage: str,
        version: ConfigVersion | None = None,
        deployment: Deployment | None = None,
        error: str | None = None,
        warnings: list[str] | None = None,
        unchanged: bool = False,
    ) -> None:
        self.ok = ok
        self.stage = stage
        self.version = version
        self.deployment = deployment
        self.error = error
        self.warnings = warnings or []
        #: True when the desired state already matched what is live. Not a
        #: failure, and deliberately not a new version — a control plane that
        #: writes a version every time someone clicks Deploy makes its own
        #: history useless.
        self.unchanged = unchanged

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stage": self.stage,
            "unchanged": self.unchanged,
            "error": self.error,
            "warnings": self.warnings,
            "version": self.version.version if self.version else None,
            "deployment_id": self.deployment.pk if self.deployment else None,
        }


# ---------------------------------------------------------------------------
# Desired state
# ---------------------------------------------------------------------------


async def snapshot(gateway: Gateway) -> dict[str, Any]:
    """The gateway's desired state as plain data.

    Plain dictionaries rather than ORM rows, for one reason that matters: a
    stored :class:`ConfigVersion` has to be regenerable, and a payload
    containing model instances cannot be stored. Everything downstream of this
    function — the generator, the differ, validation — works on data that
    round-trips through JSON.
    """
    upstreams = await Upstream.filter(gateway_id=gateway.pk).prefetch_related("targets")
    routes = await GatewayRoute.filter(gateway_id=gateway.pk).prefetch_related("domain")
    domains = await Domain.filter(gateway_id=gateway.pk)
    rules = await IpRule.filter(gateway_id=gateway.pk).prefetch_related("domain", "route")
    limits = await RateLimit.filter(gateway_id=gateway.pk, enabled=True).prefetch_related("route")
    policies = await SecurityPolicy.filter(gateway_id=gateway.pk, enabled=True).prefetch_related(
        "domain", "rate_limit"
    )

    now = datetime.now(UTC)
    # Expired temporary blocks are simply not emitted. Expiry is enforced by
    # regenerating rather than by Caddy, which has no concept of a rule with an
    # end time — so a block that has passed its expiry disappears at the next
    # deployment, and `app/jobs.py` schedules one when the earliest expiry
    # falls due.
    live_rules = [r for r in rules if r.expires_at is None or r.expires_at > now]

    route_rules: dict[int, list[dict[str, Any]]] = {}
    for rule in live_rules:
        if rule.route_id:
            route_rules.setdefault(rule.route_id, []).append(
                {"action": rule.action, "cidr": rule.cidr}
            )

    return {
        "gateway": {
            "id": gateway.pk,
            "listen": gateway.listen,
            "maintenance": gateway.maintenance,
            "maintenance_status": gateway.maintenance_status,
            "maintenance_body": gateway.maintenance_body,
            "trusted_proxies": [],
        },
        "domains": [
            {"hostname": d.hostname, "tls_mode": d.tls_mode, "enabled": d.enabled}
            for d in domains
        ],
        "upstreams": [
            {
                "id": u.pk,
                "name": u.name,
                "policy": u.policy,
                "enabled": u.enabled,
                "maintenance": u.maintenance,
                "max_fails": u.max_fails,
                "fail_duration_seconds": u.fail_duration_seconds,
                "health_path": u.health_path,
                "health_interval_seconds": u.health_interval_seconds,
                "health_timeout_seconds": u.health_timeout_seconds,
                "health_expect_status": u.health_expect_status,
                "dial_timeout_seconds": u.dial_timeout_seconds,
                "max_connections": u.max_connections,
                "targets": [
                    {"dial": t.dial, "weight": t.weight, "enabled": t.enabled}
                    for t in sorted(u.targets, key=lambda t: t.pk)
                ],
            }
            for u in upstreams
        ],
        "routes": [
            {
                "id": r.pk,
                "name": r.name,
                "slug": r.slug,
                "host": r.domain.hostname if r.domain else None,
                "path": r.path,
                "methods": r.method_list,
                "priority": r.priority,
                "enabled": r.enabled,
                "maintenance": r.maintenance,
                "action": r.action,
                "upstream_id": r.upstream_id,
                "canary_upstream_id": r.canary_upstream_id,
                "canary_percent": r.canary_percent,
                "redirect_to": r.redirect_to,
                "redirect_status": r.redirect_status,
                "static_body": r.static_body,
                "static_status": r.static_status,
                "strip_prefix": r.strip_prefix,
                "rewrite_to": r.rewrite_to,
                "request_headers": r.request_headers or {},
                "response_headers": r.response_headers or {},
                "remove_request_headers": r.remove_request_headers or [],
                "remove_response_headers": r.remove_response_headers or [],
                "timeout_seconds": r.timeout_seconds,
                "read_timeout_seconds": r.read_timeout_seconds,
                "retries": r.retries,
                "auth_policy": r.auth_policy,
                "auth_requirement": r.auth_requirement,
                "ip_rules": route_rules.get(r.pk, []),
            }
            for r in routes
        ],
        "ip_rules": [
            {
                "action": r.action,
                "cidr": r.cidr,
                "domain": r.domain.hostname if r.domain else None,
            }
            for r in live_rules
            if r.route_id is None
        ],
        "rate_limits": [
            {
                "id": limit.pk,
                "name": limit.name,
                "key": limit.key,
                "limit": limit.limit,
                "window_seconds": limit.window_seconds,
                "burst": limit.burst,
                "enabled": limit.enabled,
                "route_id": limit.route_id,
                "path": limit.route.path if limit.route else "/*",
            }
            for limit in limits
        ],
        "policies": [
            {
                "name": p.name,
                "subject": p.subject,
                "operator": p.operator,
                "value": p.value,
                "action": p.action,
                "priority": p.priority,
                "enabled": p.enabled,
                "domain": p.domain.hostname if p.domain else None,
                "rate_limit": (
                    {
                        "id": p.rate_limit.pk,
                        "key": p.rate_limit.key,
                        "limit": p.rate_limit.limit,
                        "window_seconds": p.rate_limit.window_seconds,
                        "path": "/*",
                    }
                    if p.rate_limit
                    else None
                ),
            }
            for p in policies
        ],
    }


async def generate(gateway: Gateway, manager: CaddyManager | None = None) -> tuple[dict, list[str]]:
    """Build the Caddy server object for a gateway's current desired state.

    Returns the payload and any warnings the generator raised — a rate limit it
    could not emit, a route with no upstream. Warnings do not stop a
    deployment; they are shown alongside it, because an operator deploying nine
    working routes and one broken one wants the nine deployed and to be told
    about the tenth.
    """
    manager = manager or manager_for(gateway)
    # Capabilities come from the *gateway's* build, not from whatever `caddy`
    # happens to be on this host. For a remote gateway those are different
    # binaries, and generating against the wrong one either emits a handler
    # that will be rejected or omits one that would have worked.
    from app.services.modules import build_for

    build = await build_for(gateway)
    generated = manager.generate(await snapshot(gateway), build.modules)
    return dict(generated), generated.warnings


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


async def deploy(
    gateway: Gateway,
    *,
    actor: Any = None,
    origin: str = "web",
    note: str | None = None,
    ip: str | None = None,
    force: bool = False,
) -> DeployResult:
    """Take the gateway's desired state all the way to running configuration.

    Args:
        force: Deploy even when the payload is identical to the active version.
            The default is not to: an unchanged deployment writes a version
            nobody can tell apart from its predecessor, and history is the
            thing versions exist for. `force` is what a drifted gateway needs,
            where the desired state is unchanged but the gateway is not running
            it.

    Returns:
        A :class:`DeployResult` naming the stage reached. Failure at any stage
        leaves the gateway running its previous configuration.
    """
    manager = manager_for(gateway)

    # ---- generate --------------------------------------------------------
    try:
        payload, warnings = await generate(gateway, manager)
    except Exception as error:  # noqa: BLE001
        await _mark(gateway, "FAILED", str(error))
        return DeployResult(ok=False, stage="generate", error=str(error))

    # The checksum covers everything a deployment *writes*, not just the
    # server object. Janus writes two subtrees — the server and the access
    # logger — and the logger's path is not inside the server payload, so a
    # checksum over the payload alone reported "no changes to deploy" after
    # the log path moved, and the gateway kept writing to the old file with
    # nothing to say why the collector had gone quiet.
    digest = checksum_of({"server": payload, "access_log": manager.access_log})
    active = await _active_version(gateway)
    if active is not None and active.checksum == digest and not force:
        return DeployResult(ok=True, stage="unchanged", version=active, unchanged=True, warnings=warnings)

    # The version row is created before validation, so a rejected
    # configuration is still recorded with the reason it was rejected. A
    # pipeline that only writes history on success cannot answer "what did we
    # try to deploy at 3am and why did it not go out".
    async with in_transaction():
        next_number = ((await ConfigVersion.filter(gateway_id=gateway.pk)
                        .order_by("-version").first()) or None)
        version = await ConfigVersion.create(
            gateway_id=gateway.pk,
            version=(next_number.version + 1) if next_number else 1,
            payload=payload,
            checksum=digest,
            status="draft",
            note=note,
            summary=_summarise(active.payload if active else None, payload),
            route_count=len(payload.get("routes") or []),
            upstream_count=len(await Upstream.filter(gateway_id=gateway.pk, enabled=True)),
            domain_count=await Domain.filter(gateway_id=gateway.pk, enabled=True).count(),
            author_id=getattr(actor, "pk", None),
            origin=origin,
        )

    deployment = await Deployment.create(
        gateway_id=gateway.pk,
        version_id=version.pk,
        status="validating",
        stage="validating",
        previous_version_id=active.pk if active else None,
        actor_id=getattr(actor, "pk", None),
        origin=origin,
    )
    await _mark(gateway, "SYNCING", None)

    # ---- validate --------------------------------------------------------
    # The local binary judges only when it is the gateway's own build. See
    # `CaddyManager.validate`.
    from app.services.modules import build_for as _build_for

    result = await manager.validate(
        payload, use_binary=(await _build_for(gateway)).source != "declared"
    )
    if not result.ok:
        version.status = "rejected"
        version.validation_error = result.error
        await version.save()
        await _finish(deployment, "failed", "validating", result.error)
        await _mark(gateway, "FAILED", result.error)
        await audit.record(
            action="config.rejected",
            resource_type="config_version",
            resource_id=version.pk,
            resource_label=f"v{version.version}",
            actor=actor,
            after={"error": result.error, "method": result.method},
            origin=origin,
            ip=ip,
        )
        # The gateway is untouched: nothing was sent.
        return DeployResult(
            ok=False, stage="validate", version=version, deployment=deployment,
            error=result.error, warnings=warnings,
        )

    version.status = "validated"
    await version.save()

    # ---- apply -----------------------------------------------------------
    deployment.stage = "applying"
    deployment.status = "applying"
    await deployment.save()
    try:
        await manager.apply(payload)
    except (CaddyApplyError, CaddyUnreachable) as error:
        version.status = "failed"
        version.validation_error = str(error)
        await version.save()
        await _finish(deployment, "failed", "applying", str(error))
        await _mark(gateway, "FAILED", str(error))
        await audit.record(
            action="config.apply_failed",
            resource_type="config_version",
            resource_id=version.pk,
            resource_label=f"v{version.version}",
            actor=actor,
            after={"error": str(error)},
            origin=origin,
            ip=ip,
        )
        return DeployResult(
            ok=False, stage="apply", version=version, deployment=deployment,
            error=str(error), warnings=warnings,
        )

    # ---- verify ----------------------------------------------------------
    deployment.stage = "verifying"
    deployment.status = "verifying"
    await deployment.save()
    if not await manager.verify(payload):
        message = "The gateway accepted the configuration but is not running it."
        await _finish(deployment, "failed", "verifying", message)
        await _mark(gateway, "DRIFTED", message)
        return DeployResult(
            ok=False, stage="verify", version=version, deployment=deployment,
            error=message, warnings=warnings,
        )

    # ---- mark active -----------------------------------------------------
    async with in_transaction():
        if active is not None:
            active.status = "superseded"
            await active.save()
        version.status = "active"
        version.activated_at = datetime.now(UTC)
        await version.save()
        gateway.active_version_id = version.pk
        gateway.sync_state = "SYNCED"
        gateway.sync_error = None
        gateway.last_synced_at = datetime.now(UTC)
        gateway.simulated = config.caddy_simulate
        await gateway.save()

    await _finish(deployment, "succeeded", "done", None)
    await audit.record(
        action="config.deployed",
        resource_type="config_version",
        resource_id=version.pk,
        resource_label=f"v{version.version}",
        actor=actor,
        before={"version": active.version} if active else None,
        after={"version": version.version, "routes": version.route_count},
        origin=origin,
        ip=ip,
    )
    return DeployResult(
        ok=True, stage="active", version=version, deployment=deployment, warnings=warnings
    )


async def rollback(
    gateway: Gateway,
    target_version: int,
    *,
    actor: Any = None,
    origin: str = "web",
    ip: str | None = None,
) -> DeployResult:
    """Redeploy an earlier version's stored payload.

    Not an undo: the target version's bytes are sent exactly as they were
    recorded, which is what makes a rollback predictable when the models have
    moved on since. A *new* version row is created carrying that payload, so
    the history reads forward — "v9, which is v4's configuration" — rather than
    a version silently becoming active for the second time with two activation
    dates.
    """
    target = await ConfigVersion.get_or_none(gateway_id=gateway.pk, version=target_version)
    if target is None:
        return DeployResult(ok=False, stage="generate", error=f"No version {target_version}.")
    if target.status == "rejected":
        return DeployResult(
            ok=False,
            stage="validate",
            error=f"Version {target_version} was rejected and never ran; it cannot be restored.",
        )

    manager = manager_for(gateway)
    payload = target.payload
    active = await _active_version(gateway)

    from app.services.modules import build_for as _build_for

    result = await manager.validate(
        payload, use_binary=(await _build_for(gateway)).source != "declared"
    )
    if not result.ok:
        return DeployResult(ok=False, stage="validate", error=result.error)

    latest = await ConfigVersion.filter(gateway_id=gateway.pk).order_by("-version").first()
    version = await ConfigVersion.create(
        gateway_id=gateway.pk,
        version=(latest.version + 1) if latest else 1,
        payload=payload,
        checksum=checksum_of({"server": payload, "access_log": manager.access_log}),
        status="validated",
        note=f"Rollback to v{target_version}.",
        summary=_summarise(active.payload if active else None, payload),
        route_count=target.route_count,
        upstream_count=target.upstream_count,
        domain_count=target.domain_count,
        author_id=getattr(actor, "pk", None),
        origin=origin,
        rolled_back_from_id=target.pk,
    )
    deployment = await Deployment.create(
        gateway_id=gateway.pk,
        version_id=version.pk,
        status="applying",
        stage="applying",
        previous_version_id=active.pk if active else None,
        actor_id=getattr(actor, "pk", None),
        origin=origin,
    )

    try:
        await manager.apply(payload)
    except (CaddyApplyError, CaddyUnreachable) as error:
        version.status = "failed"
        await version.save()
        await _finish(deployment, "failed", "applying", str(error))
        await _mark(gateway, "FAILED", str(error))
        return DeployResult(ok=False, stage="apply", version=version, error=str(error))

    async with in_transaction():
        if active is not None:
            active.status = "superseded"
            await active.save()
        version.status = "active"
        version.activated_at = datetime.now(UTC)
        await version.save()
        gateway.active_version_id = version.pk
        gateway.sync_state = "SYNCED"
        gateway.sync_error = None
        gateway.last_synced_at = datetime.now(UTC)
        await gateway.save()

    await _finish(deployment, "succeeded", "done", None)
    await audit.record(
        action="config.rolled_back",
        resource_type="config_version",
        resource_id=version.pk,
        resource_label=f"v{version.version}",
        actor=actor,
        before={"version": active.version} if active else None,
        after={"version": version.version, "restored_from": target_version},
        origin=origin,
        ip=ip,
    )
    return DeployResult(ok=True, stage="active", version=version, deployment=deployment)


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


async def detect_drift(gateway: Gateway) -> tuple[bool, str | None]:
    """Compare what is live against the active version, and record the verdict.

    Drift is not an error and does not deploy anything by itself. It sets the
    gateway's state to `DRIFTED` and says why; putting it right is an operator
    decision, because the reason for drift is sometimes a person fixing an
    outage by hand and overwriting them automatically is not obviously correct.
    """
    active = await _active_version(gateway)
    if active is None:
        return False, None

    manager = manager_for(gateway)
    try:
        drifted, description = await manager.detect_drift(active.payload)
    except CaddyUnreachable as error:
        await _mark(gateway, "FAILED", str(error))
        return False, None

    if drifted:
        await _mark(gateway, "DRIFTED", description)
    elif gateway.sync_state in {"DRIFTED", "FAILED"}:
        await _mark(gateway, "SYNCED", None)
    return drifted, description


# ---------------------------------------------------------------------------
# History and diffs
# ---------------------------------------------------------------------------


async def history(gateway: Gateway, limit: int = 50) -> list[ConfigVersion]:
    return (
        await ConfigVersion.filter(gateway_id=gateway.pk)
        .order_by("-version")
        .limit(limit)
        .prefetch_related("author")
    )


def diff_versions(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[str]:
    """A readable line-by-line difference between two payloads.

    Unified diff over pretty-printed JSON rather than a structural comparison.
    A structural differ produces a tidier object and a worse answer: what an
    operator reviewing a change wants is to see the lines, in context, the way
    they would read a code review.
    """
    import difflib

    left = jsonlib.dumps(before or {}, indent=2, sort_keys=True).splitlines()
    right = jsonlib.dumps(after or {}, indent=2, sort_keys=True).splitlines()
    return list(
        difflib.unified_diff(left, right, fromfile="active", tofile="proposed", lineterm="", n=3)
    )


def _summarise(before: dict[str, Any] | None, after: dict[str, Any]) -> str:
    """One line describing what changed, for the history list."""
    if before is None:
        return f"Initial configuration — {len(after.get('routes') or [])} routes."
    old_routes = len(before.get("routes") or [])
    new_routes = len(after.get("routes") or [])
    parts: list[str] = []
    if new_routes != old_routes:
        direction = "added" if new_routes > old_routes else "removed"
        parts.append(f"{abs(new_routes - old_routes)} route(s) {direction}")
    if before.get("listen") != after.get("listen"):
        parts.append(f"listen {before.get('listen')} → {after.get('listen')}")
    if not parts:
        changed = len([line for line in diff_versions(before, after) if line.startswith(("+", "-"))])
        parts.append(f"{max(0, changed - 2)} configuration line(s) changed")
    return ", ".join(parts) + "."


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


async def _active_version(gateway: Gateway) -> ConfigVersion | None:
    if gateway.active_version_id:
        found = await ConfigVersion.get_or_none(pk=gateway.active_version_id)
        if found is not None:
            return found
    return await ConfigVersion.filter(gateway_id=gateway.pk, status="active").first()


async def _mark(gateway: Gateway, state: str, error: str | None) -> None:
    gateway.sync_state = state
    gateway.sync_error = error
    if state == "SYNCED":
        gateway.last_synced_at = datetime.now(UTC)
    await gateway.save()


async def _finish(deployment: Deployment, status: str, stage: str, error: str | None) -> None:
    now = datetime.now(UTC)
    deployment.status = status
    deployment.stage = stage
    deployment.error = error
    deployment.finished_at = now
    started = deployment.started_at
    if started is not None:
        deployment.duration_ms = int((now - started).total_seconds() * 1000)
    await deployment.save()
