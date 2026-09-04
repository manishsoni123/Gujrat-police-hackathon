"""Health summary (CONTRACT §5.5)."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.core.tz import iso_z, utcnow
from app.db.models import Camera
from app.services import lookups
from app.services.health_poller import camera_counts, disk_usage, poller, uptime_24h, worker_status
from app.services.scope import camera_conditions

router = APIRouter(tags=["health"], dependencies=[Depends(require_permission("cameras.read"))])


@router.get("/health/summary")
async def health_summary(user: CurrentUser, db: DbDep):
    scope = user_scope(user)
    conds = camera_conditions(scope)
    await lookups.departments(db)
    now = utcnow()
    counts = await camera_counts(db, conds)
    cams = (await db.execute(select(Camera).where(Camera.status != "retired", *conds))).scalars().all()
    down = []
    for c in cams:
        if c.status == "offline" and c.last_status_change_at and (now - c.last_status_change_at) > timedelta(minutes=5):
            d = lookups.dept_sync(c.department_id)
            down.append({"id": c.id, "name": c.name, "district": c.district, "department_name": d.name if d else None, "offline_since": iso_z(c.last_status_change_at), "minutes": int((now - c.last_status_change_at).total_seconds() // 60)})
    down.sort(key=lambda x: -x["minutes"])
    today = now.date()
    amc = []
    for c in cams:
        # "expiring within 30 days": already-expired contracts belong to the ageing report (amc_status=expired)
        if c.amc_expiry and today <= c.amc_expiry <= today + timedelta(days=30):
            amc.append({"id": c.id, "name": c.name, "amc_vendor": c.amc_vendor, "amc_expiry": c.amc_expiry.isoformat(), "days_left": (c.amc_expiry - today).days})
    amc.sort(key=lambda x: x["days_left"])
    maintenance = [
        {"id": c.id, "name": c.name, "maintenance_status": c.maintenance_status, "since": iso_z(c.last_maintenance_at or c.updated_at), "note": c.maintenance_note}
        for c in cams
        if c.maintenance_status != "ok"
    ]
    anpr_live = sum(1 for c in cams if c.anpr_enabled)
    return {
        "checked_at": iso_z(poller.last_run_at or now),
        "cameras": counts,
        "uptime_24h_pct": await uptime_24h(db, conds),
        "anpr_live_cameras": anpr_live,
        "down_over_5min": down,
        "amc_expiring_30d": amc,
        "maintenance": maintenance,
        "disk": disk_usage(),
        "mediamtx": {"ok": bool(poller.mediamtx_ok), "paths": poller.mediamtx_paths, "ready": poller.mediamtx_ready},
        "anpr_workers": await worker_status(db),
    }
