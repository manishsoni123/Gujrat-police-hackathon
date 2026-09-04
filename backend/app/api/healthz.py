"""Liveness (CONTRACT §5.22): `GET /healthz` and `GET /api/healthz`, no auth."""

from __future__ import annotations

import time
from datetime import timedelta

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app import __version__
from app.core.config import settings
from app.core.tz import iso_z, utcnow
from app.db.models import AnprWorker
from app.db.session import SessionLocal
from app.services.mediamtx_client import client as mtx

router = APIRouter(tags=["health"])
_STARTED = time.monotonic()


@router.get("/healthz", include_in_schema=True)
async def healthz():
    db_ok = True
    workers = 0
    try:
        async with SessionLocal() as db:
            stale_before = utcnow() - timedelta(seconds=3 * settings.HEARTBEAT_S)
            workers = int((await db.execute(select(func.count()).select_from(AnprWorker).where(AnprWorker.last_heartbeat_at >= stale_before))).scalar() or 0)
    except Exception:  # noqa: BLE001
        db_ok = False
    mtx_ok = await mtx.ping()
    body = {
        "status": "ok" if db_ok else "degraded",
        "version": __version__,
        "time": iso_z(utcnow()),
        "db": "ok" if db_ok else "down",
        "mediamtx": "ok" if mtx_ok else "down",
        "anpr_workers": workers,
        "uptime_s": int(time.monotonic() - _STARTED),
    }
    return JSONResponse(status_code=200 if db_ok else 503, content=body)
