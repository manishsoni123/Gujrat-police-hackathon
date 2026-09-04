"""Events: list with filters and manual tagging (CONTRACT §5.13)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.core.errors import not_found, validation_error
from app.core.tz import parse_iso, utcnow
from app.db.models import Camera, Event, PlateRead, Sighting
from app.schemas.analytics import EventCreate
from app.schemas.common import PageParams, csv_list, parse_window
from app.services import serializers
from app.services.audit import set_audit
from app.services.notifications import dispatch_event_created
from app.services.scope import in_scope_condition, scoped_camera

router = APIRouter(tags=["events"])

SORTS: dict[str, Any] = {"occurred_at": Event.occurred_at, "created_at": Event.created_at, "type": Event.type}


@router.get("/events", dependencies=[Depends(require_permission("analytics.read"))])
async def list_events(
    user: CurrentUser, db: DbDep, page: PageParams = Depends(), camera_id: int | None = None, department_id: int | None = None,
    type: str | None = None, is_auto: bool | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None,
    sighting_id: int | None = None, alert_id: int | None = None,
):
    sort, order = page.resolve(SORTS, "occurred_at", "desc")
    t_from, t_to = parse_window(from_, to, 24)
    await serializers.warm(db)
    stmt = select(Event, Camera).join(Camera, Camera.id == Event.camera_id).where(Event.occurred_at >= t_from, Event.occurred_at <= t_to)
    cond = in_scope_condition(user_scope(user), Event.camera_id)
    if cond is not None:
        stmt = stmt.where(cond)
    if camera_id:
        stmt = stmt.where(Event.camera_id == camera_id)
    if department_id:
        stmt = stmt.where(Camera.department_id == department_id)
    if type:
        stmt = stmt.where(Event.type.in_(csv_list(type) or []))
    if is_auto is not None:
        stmt = stmt.where(Event.is_auto.is_(is_auto))
    if sighting_id:
        stmt = stmt.where(Event.sighting_id == sighting_id)
    if alert_id:
        stmt = stmt.where(Event.alert_id == alert_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    col = SORTS[sort]
    rows = (await db.execute(stmt.order_by(col.desc() if order == "desc" else col.asc(), Event.id.desc()).offset(page.offset).limit(page.page_size))).all()
    return {"items": [serializers.event_item(e, c) for e, c in rows], "total": int(total), "page": page.page, "page_size": page.page_size}


@router.post("/events", status_code=201, dependencies=[Depends(require_permission("events.write"))])
async def create_event(body: EventCreate, user: CurrentUser, db: DbDep, request: Request):
    cam = await scoped_camera(db, user_scope(user), body.camera_id, include_retired=False)
    if cam is None:
        raise not_found("Camera not found")
    try:
        occurred = parse_iso(body.occurred_at) if body.occurred_at else utcnow()
    except ValueError:
        raise validation_error("Invalid occurred_at", [{"field": "occurred_at", "message": "must be ISO-8601"}])
    if body.sighting_id is not None:
        s = await db.get(Sighting, body.sighting_id)
        if s is None or s.camera_id != cam.id:
            raise validation_error("Unknown sighting", [{"field": "sighting_id", "message": "sighting not found on this camera"}])
    if body.read_id is not None:
        r = await db.get(PlateRead, body.read_id)
        if r is None or r.camera_id != cam.id:
            raise validation_error("Unknown read", [{"field": "read_id", "message": "read not found on this camera"}])
    ev = Event(camera_id=cam.id, occurred_at=occurred, type=body.type, note=body.note, sighting_id=body.sighting_id, read_id=body.read_id, is_auto=False, created_by=user.id, created_at=utcnow())
    db.add(ev)
    await db.commit()
    await db.refresh(ev)
    await serializers.warm(db)
    item = serializers.event_item(ev, cam)
    dispatch_event_created(item)
    set_audit(request, entity="event", entity_id=ev.id, after={"camera_id": cam.id, "type": ev.type, "note": ev.note, "sighting_id": ev.sighting_id, "read_id": ev.read_id})
    return item
