"""Alerts (CONTRACT §5.12)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import case, func, select

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.core.errors import conflict, not_found
from app.core.tz import utcnow
from app.db.models import Alert, Camera, Event, PlateRead, Watchlist
from app.schemas.analytics import AlertAck, AlertClose
from app.schemas.common import PageParams, csv_list, parse_window
from app.services import serializers
from app.services import settings_service as cfg
from app.services.audit import set_audit
from app.services.notifications import dispatch_alert_updated
from app.services.plates import normalise
from app.services.scope import in_scope_condition
from app.services.ws_manager import manager

router = APIRouter(tags=["alerts"])

PRIORITY_CASE = case({"critical": 0, "high": 1, "medium": 2, "low": 3}, value=Alert.priority, else_=9)
SORTS: dict[str, Any] = {"priority": PRIORITY_CASE, "created_at": Alert.created_at, "updated_at": Alert.updated_at}


async def _hydrate(db, alerts: list[Alert]) -> list[dict[str, Any]]:
    await serializers.warm(db)
    cam_ids = {a.camera_id for a in alerts}
    wl_ids = {a.watchlist_id for a in alerts if a.watchlist_id}
    read_ids = {a.read_id for a in alerts if a.read_id}
    cams = {c.id: c for c in (await db.execute(select(Camera).where(Camera.id.in_(cam_ids)))).scalars().all()} if cam_ids else {}
    wls = {w.id: w for w in (await db.execute(select(Watchlist).where(Watchlist.id.in_(wl_ids)))).scalars().all()} if wl_ids else {}
    reads = {r.id: r for r in (await db.execute(select(PlateRead).where(PlateRead.id.in_(read_ids)))).scalars().all()} if read_ids else {}
    return [serializers.alert_item(a, cams[a.camera_id], wls.get(a.watchlist_id), reads.get(a.read_id)) for a in alerts if a.camera_id in cams]


@router.get("/alerts", dependencies=[Depends(require_permission("analytics.read"))])
async def list_alerts(
    user: CurrentUser, db: DbDep, page: PageParams = Depends(), status: str | None = None, type: str | None = None, priority: str | None = None,
    camera_id: int | None = None, department_id: int | None = None, plate: str | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None,
    escalated: bool | None = None,
):
    sort, order = page.resolve(SORTS, "priority", "desc")
    t_from, t_to = parse_window(from_, to, 24)
    stmt = select(Alert).where(Alert.created_at >= t_from, Alert.created_at <= t_to)
    cond = in_scope_condition(user_scope(user), Alert.camera_id)
    if cond is not None:
        stmt = stmt.where(cond)
    statuses = csv_list(status) if status else ["new", "acknowledged"]
    if statuses and "all" not in statuses:
        stmt = stmt.where(Alert.status.in_(statuses))
    if type:
        stmt = stmt.where(Alert.type.in_(csv_list(type) or []))
    if priority:
        stmt = stmt.where(Alert.priority.in_(csv_list(priority) or []))
    if camera_id:
        stmt = stmt.where(Alert.camera_id == camera_id)
    if department_id:
        stmt = stmt.where(Alert.camera_id.in_(select(Camera.id).where(Camera.department_id == department_id)))
    if plate:
        stmt = stmt.where(Alert.plate_norm == normalise(plate).plate_norm)
    if escalated is not None:
        from datetime import timedelta

        cutoff = utcnow() - timedelta(minutes=cfg.get_int("alerts.escalate_minutes"))
        cond_esc = (Alert.status == "new") & (Alert.created_at < cutoff)
        stmt = stmt.where(cond_esc if escalated else ~cond_esc)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    if sort == "priority":
        stmt = stmt.order_by(PRIORITY_CASE.asc(), Alert.created_at.desc())
    else:
        col = SORTS[sort]
        stmt = stmt.order_by(col.desc() if order == "desc" else col.asc(), Alert.id.desc())
    rows = (await db.execute(stmt.offset(page.offset).limit(page.page_size))).scalars().all()
    return {"items": await _hydrate(db, rows), "total": int(total), "page": page.page, "page_size": page.page_size}


async def _get_alert(db, user, alert_id: int) -> tuple[Alert, Camera]:
    a = await db.get(Alert, alert_id)
    if a is None:
        raise not_found("Alert not found")
    cam = await db.get(Camera, a.camera_id)
    if cam is None or not user_scope(user).allows(cam.department_id, cam.district):
        raise not_found("Alert not found")
    return a, cam


@router.get("/alerts/{alert_id}", dependencies=[Depends(require_permission("analytics.read"))])
async def get_alert(alert_id: int, user: CurrentUser, db: DbDep):
    a, cam = await _get_alert(db, user, alert_id)
    item = (await _hydrate(db, [a]))[0]
    reads = []
    if a.type == "watchlist_hit" and a.watchlist_id and a.plate_norm:
        from datetime import timedelta

        first_read = await db.get(PlateRead, a.read_id) if a.read_id else None
        lower = min(a.created_at, first_read.captured_at if first_read else a.created_at) - timedelta(seconds=30)
        upper = max(a.last_read_at or a.created_at, a.created_at) + timedelta(seconds=1)
        rows = (
            await db.execute(
                select(PlateRead).where(PlateRead.camera_id == a.camera_id, PlateRead.captured_at >= lower, PlateRead.captured_at <= upper).order_by(PlateRead.captured_at.asc()).limit(200)
            )
        ).scalars().all()
        q = a.plate_norm
        from app.services.plates import levenshtein

        reads = [serializers.read_brief(r) for r in rows if levenshtein(r.plate_norm, q) <= 1]
    events = (await db.execute(select(Event).where(Event.alert_id == a.id).order_by(Event.occurred_at.desc()))).scalars().all()
    item["reads"] = reads
    item["events"] = [serializers.event_item(e, cam) for e in events]
    return item


def _broadcast_update(a: Alert, cam: Camera) -> None:
    payload = serializers.alert_update_payload(a)
    manager.broadcast("alerts", "alert_update", payload, cam.department_id, cam.district)
    dispatch_alert_updated(payload)


@router.post("/alerts/{alert_id}/ack", dependencies=[Depends(require_permission("alerts.ack"))])
async def ack(alert_id: int, body: AlertAck, user: CurrentUser, db: DbDep, request: Request):
    a, cam = await _get_alert(db, user, alert_id)
    if a.status != "new":
        raise conflict(f"Alert is already {a.status}")
    now = utcnow()
    a.status = "acknowledged"
    a.acknowledged_by = user.id
    a.acknowledged_at = now
    if body.note:
        a.note = body.note
    a.updated_at = now
    await db.commit()
    await db.refresh(a)
    _broadcast_update(a, cam)
    set_audit(request, entity="alert", entity_id=a.id, before={"status": "new"}, after={"status": "acknowledged", "note": body.note})
    return (await _hydrate(db, [a]))[0]


@router.post("/alerts/{alert_id}/close", dependencies=[Depends(require_permission("alerts.ack"))])
async def close(alert_id: int, body: AlertClose, user: CurrentUser, db: DbDep, request: Request):
    a, cam = await _get_alert(db, user, alert_id)
    if a.status == "closed":
        raise conflict("Alert is already closed")
    now = utcnow()
    before = {"status": a.status}
    if a.status == "new":
        a.acknowledged_by = user.id
        a.acknowledged_at = now
    a.status = "closed"
    a.closed_by = user.id
    a.closed_at = now
    a.outcome = body.outcome
    if body.note:
        a.note = body.note
    a.updated_at = now
    await db.commit()
    await db.refresh(a)
    _broadcast_update(a, cam)
    set_audit(request, entity="alert", entity_id=a.id, before=before, after={"status": "closed", "outcome": body.outcome, "note": body.note})
    return (await _hydrate(db, [a]))[0]
