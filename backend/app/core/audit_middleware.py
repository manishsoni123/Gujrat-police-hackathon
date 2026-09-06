"""Pure-ASGI audit middleware (CONTRACT §4.4 rules).

Writes one audit row after the response for every POST/PUT/PATCH/DELETE under /api
(except /api/internal/*, /api/auth/verify and /api/mock-sandbox/*), and for GETs only
when the handler set `request.state.audit` (stream.view, vehicle.search, report.*, …).
Handlers enrich the row through `services.audit.set_audit`.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Iterable
from typing import Any

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services.audit import action_for_route, client_ip, write_audit

log = logging.getLogger("sentinel.http")

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# No trailing slashes: `/api/internal` (no slash, 404) must be skipped like `/api/internal/...`.
_SKIP_PREFIXES = ("/api/internal", "/api/auth/verify", "/api/mock-sandbox", "/api/docs", "/api/openapi.json", "/api/redoc", "/api/healthz")


def should_audit(method: str, path: str, has_audit: bool, route_matched: bool, status: int) -> bool:
    """Pure decision (tested): mutations under /api on a **matched** route, or GETs the handler marked."""
    if not path.startswith("/api/"):
        return False
    if path.startswith(_SKIP_PREFIXES):
        return False
    if method in ("OPTIONS", "HEAD"):
        return False
    if method not in _MUTATING and not has_audit:
        return False
    if not route_matched and status == 404:
        return False  # unmatched route: nothing happened, no synthetic 'post.api.x' action (CONTRACT §4.4)
    return True


def _plain(obj: Any, attr: str) -> Any:
    """Read a column value without triggering a lazy refresh.

    The user row lives in the request session; if a handler rolled that session back or the
    session is already closed, normal attribute access on an expired instance raises
    `DetachedInstanceError`/`MissingGreenlet` and the audit row would be lost. `__dict__` holds
    the last loaded value (or nothing when expired), which is all the audit row needs.
    """
    if obj is None:
        return None
    return getattr(obj, "__dict__", {}).get(attr)


class AuditMiddleware:
    """`routes` is a zero-arg callable returning the FastAPI route list; it is used to resolve
    the matched route template (`/api/cameras/{camera_id}`) when Starlette did not put one in scope."""

    def __init__(self, app: ASGIApp, routes: Callable[[], Iterable[Any]] | None = None) -> None:
        self.app = app
        self._routes = routes

    def _resolve_route(self, scope: Scope) -> Any | None:
        route = scope.get("route")
        if route is not None or self._routes is None:
            return route
        try:
            for r in self._routes():
                matcher = getattr(r, "matches", None)
                if matcher is None:
                    continue
                match, _child = matcher(scope)
                if match == Match.FULL:
                    return r
        except Exception:  # noqa: BLE001
            return None
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4()
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id
        scope["state"]["audit"] = None
        started = time.perf_counter()
        status_holder = {"status": 0}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", str(request_id).encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            try:
                await self._after(scope, status_holder["status"], duration_ms, request_id)
            except Exception:  # noqa: BLE001
                log.exception("audit middleware failure")

    async def _after(self, scope: Scope, status: int, duration_ms: int, request_id: uuid.UUID) -> None:
        method = scope.get("method", "GET")
        path = scope.get("path", "")
        state = scope.get("state", {})
        audit = state.get("audit")
        user = state.get("user")
        api_key = state.get("api_key")

        log.info(
            "%s %s -> %s",
            method,
            path,
            status,
            extra={
                "request_id": str(request_id),
                "path": path,
                "status": status,
                "duration_ms": duration_ms,
                "user": _plain(user, "username") or (f"apikey:{api_key.name}" if api_key else None),
            },
        )

        if audit and audit.get("skip"):
            return
        route = self._resolve_route(scope)
        if not should_audit(method, path, bool(audit), route is not None, status):
            return
        route_path = getattr(route, "path", None)
        action = (audit or {}).get("action") or action_for_route(method, route_path, path)
        if action is None:
            # unmapped mutation – still record something meaningful
            action = f"{method.lower()}.{(route_path or path).strip('/').replace('/', '.')}"[:48]

        actor_info = state.get("actor") or {}
        if actor_info.get("username"):
            actor, role, uid = actor_info["username"], actor_info.get("role") or "unknown", actor_info.get("id")
        elif user is not None and _plain(user, "username"):
            actor, role, uid = _plain(user, "username"), _plain(user, "role") or "unknown", _plain(user, "id")
        elif api_key is not None:
            actor, role, uid = f"apikey:{api_key.name}", "apikey", None
        else:
            actor, role, uid = (audit or {}).get("actor") or "anonymous", "anonymous", None

        headers = Headers(scope=scope)
        req = Request(scope)
        after = (audit or {}).get("after")
        if status >= 400:
            after = dict(after or {})
            after["http_status"] = status
        await write_audit(
            action,
            actor=actor,
            role=role,
            user_id=uid,
            entity=(audit or {}).get("entity"),
            entity_id=(audit or {}).get("entity_id"),
            before=(audit or {}).get("before"),
            after=after,
            ip=client_ip(req),
            user_agent=headers.get("user-agent"),
            request_id=request_id,
        )
