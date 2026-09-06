"""Periodic `stats` envelopes for `/ws/alerts` and `/ws/health` (CONTRACT §9), computed per scope."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import Scope
from app.db.models import Alert, Camera
from app.db.session import SessionLocal
from app.services.health_poller import camera_counts, disk_usage, reads_last_minute, uptime_24h
from app.services.scope import camera_conditions
from app.services.ws_manager import manager

log = logging.getLogger("sentinel.ws.stats")


async def alerts_stats(db: AsyncSession, scope: Scope) -> dict[str, Any]:
    conds = camera_conditions(scope)
    q = select(Alert.status, Alert.priority, func.count()).where(Alert.status.in_(("new", "acknowledged"))).group_by(Alert.status, Alert.priority)
    if conds:
        q = q.where(Alert.camera_id.in_(select(Camera.id).where(*conds)))
    rows = (await db.execute(q)).all()
    new = ack = critical_open = 0
    for status, priority, n in rows:
        if status == "new":
            new += int(n)
        else:
            ack += int(n)
        if priority == "critical":
            critical_open += int(n)
    counts = await camera_counts(db, conds)
    return {
        "alerts_new": new,
        "alerts_acknowledged": ack,
        "critical_open": critical_open,
        "reads_last_min": await reads_last_minute(db, conds),
        "cameras_online": counts["online"],
    }


async def health_stats(db: AsyncSession, scope: Scope) -> dict[str, Any]:
    conds = camera_conditions(scope)
    counts = await camera_counts(db, conds)
    anpr = (await db.execute(select(func.count()).select_from(Camera).where(Camera.status != "retired", Camera.anpr_enabled.is_(True), *conds))).scalar() or 0
    return {
        "cameras": {k: counts[k] for k in ("total", "online", "degraded", "offline", "not_streaming", "unknown")},
        "uptime_24h_pct": await uptime_24h(db, conds),
        "anpr_live_cameras": int(anpr),
        "reads_last_min": await reads_last_minute(db, conds),
        "disk_free_bytes": disk_usage()["data_free_bytes"],
    }


async def broadcast_stats() -> None:
    """One DB round-trip per distinct scope per channel; nothing when nobody listens."""
    conns = manager.connections("alerts") + manager.connections("health")
    if not conns:
        return
    cache: dict[tuple, dict[str, Any]] = {}
    async with SessionLocal() as db:
        for conn in conns:
            key = (conn.channel, conn.scope.department_id, conn.scope.district)
            if key in cache:
                continue
            try:
                cache[key] = await (alerts_stats if conn.channel == "alerts" else health_stats)(db, conn.scope)
            except Exception:  # noqa: BLE001
                log.exception("stats computation failed for %s", key)
    for channel in ("alerts", "health"):
        manager.broadcast_scoped_fn(channel, "stats", lambda c, ch=channel: cache.get((ch, c.scope.department_id, c.scope.district)))
