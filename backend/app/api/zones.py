"""Intrusion zones CRUD (A7, CONTRACT §5.17). The worker reads them via /internal/anpr-config."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.core.errors import not_found
from app.core.tz import utcnow
from app.db.models import Camera, Zone
from app.schemas.analytics import ZoneCreate, ZoneUpdate
from app.services import serializers
from app.services.audit import set_audit
from app.services.scope import in_scope_condition, scoped_camera

router = APIRouter(tags=["zones"])


async def _get_zone(db, user, zone_id: int) -> Zone:
    z = await db.get(Zone, zone_id)
    if z is None:
        raise not_found("Zone not found")
    cam = await db.get(Camera, z.camera_id)
    if cam is None or not user_scope(user).allows(cam.department_id, cam.district):
        raise not_found("Zone not found")
    return z


@router.get("/zones", dependencies=[Depends(require_permission("cameras.read"))])
async def list_zones(user: CurrentUser, db: DbDep, camera_id: int | None = None, is_active: bool | None = None):
    stmt = select(Zone)
    cond = in_scope_condition(user_scope(user), Zone.camera_id)
    if cond is not None:
        stmt = stmt.where(cond)
    if camera_id:
        stmt = stmt.where(Zone.camera_id == camera_id)
    if is_active is not None:
        stmt = stmt.where(Zone.is_active.is_(is_active))
    rows = (await db.execute(stmt.order_by(Zone.camera_id, Zone.id))).scalars().all()
    return {"items": [serializers.zone_item(z) for z in rows], "total": len(rows)}


@router.post("/zones", status_code=201, dependencies=[Depends(require_permission("zones.write"))])
async def create_zone(body: ZoneCreate, user: CurrentUser, db: DbDep, request: Request):
    cam = await scoped_camera(db, user_scope(user), body.camera_id, include_retired=False)
    if cam is None:
        raise not_found("Camera not found")
    z = Zone(
        camera_id=cam.id, name=body.name, polygon_json=body.polygon, active_from=body.active_from, active_to=body.active_to,
        classes=body.classes or [], dwell_s=body.dwell_s if body.dwell_s is not None else 2.0, priority=body.priority or "medium",
        is_active=body.is_active if body.is_active is not None else True, created_by=user.id, created_at=utcnow(), updated_at=utcnow(),
    )
    db.add(z)
    await db.commit()
    await db.refresh(z)
    item = serializers.zone_item(z)
    set_audit(request, entity="zone", entity_id=z.id, after=item)
    return item


@router.put("/zones/{zone_id}", dependencies=[Depends(require_permission("zones.write"))])
async def update_zone(zone_id: int, body: ZoneUpdate, user: CurrentUser, db: DbDep, request: Request):
    z = await _get_zone(db, user, zone_id)
    before = serializers.zone_item(z)
    data = body.model_dump(exclude_unset=True)
    if "polygon" in data and data["polygon"] is not None:
        z.polygon_json = data.pop("polygon")
    for k, v in data.items():
        if k == "polygon":
            continue
        if v is None and k in ("name", "classes", "dwell_s", "priority", "is_active"):
            continue
        setattr(z, k, v)
    z.updated_at = utcnow()
    await db.commit()
    await db.refresh(z)
    item = serializers.zone_item(z)
    set_audit(request, entity="zone", entity_id=z.id, before=before, after=item)
    return item


@router.delete("/zones/{zone_id}", status_code=204, dependencies=[Depends(require_permission("zones.write"))])
async def delete_zone(zone_id: int, user: CurrentUser, db: DbDep, request: Request):
    z = await _get_zone(db, user, zone_id)
    before = serializers.zone_item(z)
    await db.delete(z)
    await db.commit()
    set_audit(request, entity="zone", entity_id=zone_id, before=before)
    return Response(status_code=204)
