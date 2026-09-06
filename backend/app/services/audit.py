"""Audit-log writer (CONTRACT §4.4). Rows are append-only (DB trigger)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import Request

from app.core.tz import utcnow
from app.db.models import AuditLog
from app.db.session import SessionLocal

log = logging.getLogger("sentinel.audit")

# Route → action map used by the middleware when the handler did not set an explicit action.
ROUTE_ACTIONS: list[tuple[str, str, str]] = [
    ("POST", "/api/auth/login", "auth.login"),
    ("POST", "/api/auth/logout", "auth.logout"),
    ("POST", "/api/auth/change-password", "user.change_password"),
    ("POST", "/api/users/{id}/reset-password", "user.reset_password"),
    ("POST", "/api/users", "user.create"),
    ("PUT", "/api/users/{id}", "user.update"),
    ("DELETE", "/api/users/{id}", "user.deactivate"),
    ("POST", "/api/api-keys", "apikey.create"),
    ("DELETE", "/api/api-keys/{id}", "apikey.delete"),
    ("POST", "/api/cameras/import/sandbox", "camera.import_sandbox"),
    ("POST", "/api/cameras/import/sandbox/file", "camera.import_sandbox"),
    ("POST", "/api/settings/catalogue/enrichment", "settings.update"),
    ("POST", "/api/cameras/import/csv", "camera.import_csv"),
    ("POST", "/api/v1/cameras/bulk", "camera.import_bulk"),
    ("POST", "/api/cameras", "camera.create"),
    ("PUT", "/api/cameras/{id}/maintenance", "camera.maintenance"),
    ("PUT", "/api/cameras/{id}", "camera.update"),
    ("DELETE", "/api/cameras/{id}", "camera.retire"),
    ("POST", "/api/watchlist/import/csv", "watchlist.import"),
    ("POST", "/api/watchlist", "watchlist.create"),
    ("PUT", "/api/watchlist/{id}", "watchlist.update"),
    ("DELETE", "/api/watchlist/{id}", "watchlist.delete"),
    ("POST", "/api/alerts/{id}/ack", "alert.ack"),
    ("POST", "/api/alerts/{id}/close", "alert.close"),
    ("POST", "/api/events", "event.create"),
    ("POST", "/api/vehicles/{plate}/confirm", "vehicle.confirm"),
    ("PUT", "/api/settings", "settings.update"),
    ("POST", "/api/webhooks/{id}/test", "webhook.test"),
    ("POST", "/api/webhooks", "webhook.create"),
    ("PUT", "/api/webhooks/{id}", "webhook.update"),
    ("DELETE", "/api/webhooks/{id}", "webhook.delete"),
    ("POST", "/api/zones", "zone.create"),
    ("PUT", "/api/zones/{id}", "zone.update"),
    ("DELETE", "/api/zones/{id}", "zone.delete"),
    ("POST", "/api/qa/labels", "qa.label"),
    ("POST", "/api/clips", "clip.create"),
    ("POST", "/api/settings/catalogue/test", "settings.catalogue_test"),
    ("PUT", "/api/me/wall-layout", "user.wall_layout"),
]


def action_for_route(method: str, route_path: str | None, raw_path: str) -> str | None:
    """Match against the FastAPI route template (`/api/cameras/{id}`) with tolerant param names."""
    if not route_path:
        route_path = raw_path
    norm = _normalise_template(route_path)
    for m, tpl, action in ROUTE_ACTIONS:
        if m == method and _normalise_template(tpl) == norm:
            return action
    return None


def _normalise_template(path: str) -> str:
    parts = []
    for seg in path.rstrip("/").split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            parts.append("{}")
        else:
            parts.append(seg)
    return "/".join(parts)


def set_audit(
    request: Request,
    action: str | None = None,
    entity: str | None = None,
    entity_id: Any = None,
    before: dict | None = None,
    after: dict | None = None,
    actor: str | None = None,
    skip: bool = False,
) -> None:
    """Handlers call this to enrich (or suppress) the row the middleware will write."""
    cur = getattr(request.state, "audit", None) or {}
    if action:
        cur["action"] = action
    if entity:
        cur["entity"] = entity
    if entity_id is not None:
        cur["entity_id"] = str(entity_id)
    if before is not None:
        cur["before"] = before
    if after is not None:
        cur["after"] = after
    if actor is not None:
        cur["actor"] = actor
    if skip:
        cur["skip"] = True
    request.state.audit = cur


def client_ip(request: Request) -> str | None:
    """Best-effort client address for the rate limiter and the audit row.

    Precedence: `X-Real-IP` (set by Caddy from the TCP peer, never client-controlled), then the
    **last** `X-Forwarded-For` hop (the one the proxy appended; earlier hops can be forged by a
    private-range client because of Caddy `trusted_proxies`), then the socket peer.
    """
    real = (request.headers.get("x-real-ip") or "").strip()
    if real:
        return real
    xff = request.headers.get("x-forwarded-for")
    if xff:
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        if hops:
            return hops[-1]
    if request.client:
        return request.client.host
    return None


def _valid_inet(ip: str | None) -> str | None:
    if not ip:
        return None
    import ipaddress

    try:
        ipaddress.ip_address(ip)
        return ip
    except ValueError:
        return None


async def write_audit(
    action: str,
    actor: str = "system",
    role: str = "system",
    user_id: int | None = None,
    entity: str | None = None,
    entity_id: Any = None,
    before: dict | None = None,
    after: dict | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    request_id: uuid.UUID | None = None,
) -> None:
    try:
        async with SessionLocal() as db:
            db.add(
                AuditLog(
                    ts=utcnow(),
                    user_id=user_id,
                    actor=actor[:80],
                    role=role[:16],
                    action=action[:48],
                    entity=entity[:32] if entity else None,
                    entity_id=str(entity_id)[:64] if entity_id is not None else None,
                    before=before,
                    after=after,
                    ip=_valid_inet(ip),
                    user_agent=(user_agent or "")[:255] or None,
                    request_id=request_id,
                )
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        log.exception("audit write failed for %s", action)


async def audit_from_request(request: Request, action: str, **kw: Any) -> None:
    """Write an audit row for the current request context immediately (handlers that need it before returning)."""
    user = getattr(request.state, "user", None)
    api_key = getattr(request.state, "api_key", None)
    if user is not None:
        actor, role, uid = user.username, user.role, user.id
    elif api_key is not None:
        actor, role, uid = f"apikey:{api_key.name}", "apikey", None
    else:
        actor, role, uid = kw.pop("actor", "anonymous"), kw.pop("role", "anonymous"), None
    await write_audit(
        action,
        actor=actor,
        role=role,
        user_id=uid,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        request_id=getattr(request.state, "request_id", None),
        **kw,
    )
