"""Outbound notifications: webhooks (S9, CONTRACT §5.20) and Telegram (W4, P2). Never block the request path."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from typing import Any

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.tz import iso_z, utcnow
from app.db.models import Webhook
from app.db.session import SessionLocal
from app.services import settings_service as cfg

log = logging.getLogger("sentinel.notify")

PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
RETRY_DELAYS = (2, 4, 8)
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def deliver(webhook_id: int, event: str, data: dict[str, Any], attempts: int = 4) -> tuple[int | None, int, str | None]:
    """POST to one webhook with retries; updates last_status/last_error. Returns (status, duration_ms, error)."""
    async with SessionLocal() as db:
        wh = await db.get(Webhook, webhook_id)
        if wh is None:
            return None, 0, "webhook not found"
        url, secret = wh.url, wh.secret
    delivery_id = str(uuid.uuid4())
    body = json.dumps({"event": event, "ts": iso_z(utcnow()), "delivery_id": delivery_id, "data": data}, default=str).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Sentinel-Event": event,
        "X-Sentinel-Delivery": delivery_id,
        "User-Agent": f"SentinelGujarat/{settings.APP_VERSION}",
    }
    if secret:
        headers["X-Sentinel-Signature"] = signature(secret, body)
    status: int | None = None
    error: str | None = None
    duration_ms = 0
    for attempt in range(attempts):
        started = asyncio.get_event_loop().time()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.post(url, content=body, headers=headers)
            duration_ms = int((asyncio.get_event_loop().time() - started) * 1000)
            status = r.status_code
            error = None if r.status_code < 400 else f"HTTP {r.status_code}"
            if r.status_code < 500:
                break
        except httpx.HTTPError as exc:
            duration_ms = int((asyncio.get_event_loop().time() - started) * 1000)
            status, error = None, f"{exc.__class__.__name__}: {exc}"[:255]
        if attempt < len(RETRY_DELAYS):
            await asyncio.sleep(RETRY_DELAYS[attempt])
    async with SessionLocal() as db:
        wh = await db.get(Webhook, webhook_id)
        if wh is not None:
            wh.last_status = status
            wh.last_error = error
            wh.last_delivered_at = utcnow()
            await db.commit()
    return status, duration_ms, error


async def fanout(event: str, data: dict[str, Any]) -> None:
    async with SessionLocal() as db:
        rows = (await db.execute(select(Webhook).where(Webhook.is_active.is_(True)))).scalars().all()
        ids = [w.id for w in rows if event in (w.event_types or [])]
    for wid in ids:
        _spawn(deliver(wid, event, data))


def emit(event: str, data: dict[str, Any]) -> None:
    """Fire-and-forget webhook fan-out for an event type."""
    _spawn(fanout(event, data))


async def telegram_send(text: str, photo_url: str | None = None) -> None:
    token = cfg.get("notify.telegram_bot_token")
    chat_id = cfg.get("notify.telegram_chat_id")
    if not token or not chat_id:
        return
    api = f"https://api.telegram.org/bot{token}"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            if photo_url:
                await client.post(f"{api}/sendPhoto", data={"chat_id": chat_id, "photo": photo_url, "caption": text})
            else:
                await client.post(f"{api}/sendMessage", data={"chat_id": chat_id, "text": text})
    except httpx.HTTPError as exc:
        log.warning("telegram send failed: %s", exc)


def dispatch_alert_created(alert_data: dict[str, Any], priority: str) -> None:
    emit("alert.created", alert_data)
    min_prio = cfg.get("notify.telegram_min_priority") or "high"
    if PRIORITY_RANK.get(priority, 9) <= PRIORITY_RANK.get(min_prio, 1):
        title = alert_data.get("notify_title") or "Sentinel Gujarat alert"
        body = alert_data.get("notify_body") or ""
        photo = alert_data.get("snapshot_url")
        photo_abs = f"{settings.PUBLIC_BASE_URL.rstrip('/')}{photo}" if photo else None
        _spawn(telegram_send(f"{title}\n{body}", photo_abs))


def dispatch_alert_updated(alert_data: dict[str, Any]) -> None:
    emit("alert.updated", alert_data)


def dispatch_camera_status(event: str, data: dict[str, Any]) -> None:
    emit(event, data)


def dispatch_event_created(data: dict[str, Any]) -> None:
    emit("event.created", data)
