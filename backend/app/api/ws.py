"""WebSocket endpoints (CONTRACT §9): /ws/alerts, /ws/reads/{camera_id}, /ws/health."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import __version__
from app.api.deps import extract_token, user_scope, ws_user
from app.core.security import decode_token
from app.core.tz import iso_z, utcnow
from app.db.session import SessionLocal
from app.services.scope import scoped_camera
from app.services.ws_manager import Connection, manager

log = logging.getLogger("sentinel.ws")
router = APIRouter(tags=["websocket"])

IDLE_TIMEOUT_S = 90.0


def _token_exp(ws: WebSocket) -> datetime | None:
    claims = decode_token(extract_token(ws) or "")
    exp = claims.get("exp") if claims else None
    return datetime.fromtimestamp(int(exp), tz=timezone.utc) if exp else None


async def _serve(ws: WebSocket, channel: str, camera_id: int | None = None) -> None:
    user = await ws_user(ws)
    await ws.accept()
    if user is None:
        await ws.close(code=4401, reason="unauthorized")
        return
    scope = user_scope(user)
    if camera_id is not None:
        async with SessionLocal() as db:
            cam = await scoped_camera(db, scope, camera_id, include_retired=False)
        if cam is None:
            await ws.close(code=4404, reason="camera not found")
            return
    conn = Connection(ws=ws, channel=channel, username=user.username, role=user.role, scope=scope, camera_id=camera_id)
    exp = _token_exp(ws)
    await manager.register(conn)
    await ws.send_text(manager.envelope("hello", {"user": user.username, "role": user.role, "server_time": iso_z(utcnow()), "version": __version__, "scope": scope.as_dict(), "channel": channel, "camera_id": camera_id}))

    async def sender() -> None:
        while True:
            payload = await conn.queue.get()
            if exp is not None and utcnow() >= exp:
                await ws.close(code=4401, reason="token expired")
                return
            await ws.send_text(payload)

    send_task = asyncio.create_task(sender())
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=IDLE_TIMEOUT_S)
            except asyncio.TimeoutError:
                await ws.close(code=1000, reason="idle")
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("type") == "ping":
                manager.send(conn, "pong", {})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("ws %s closed: %s", channel, exc)
    finally:
        send_task.cancel()
        await manager.unregister(conn)


@router.websocket("/ws/alerts")
async def ws_alerts(ws: WebSocket) -> None:
    await _serve(ws, "alerts")


@router.websocket("/ws/health")
async def ws_health(ws: WebSocket) -> None:
    await _serve(ws, "health")


@router.websocket("/ws/reads/{camera_id}")
async def ws_reads(ws: WebSocket, camera_id: int) -> None:
    await _serve(ws, f"reads:{camera_id}", camera_id)
