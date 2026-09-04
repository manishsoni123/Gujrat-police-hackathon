"""Plate reads and sightings listings (CONTRACT §5.9)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.core.errors import not_found
from app.core.hashing import media_url
from app.db.models import Alert, Camera, Event, PlateRead, QaLabel, Sighting
from app.schemas.common import PageParams, parse_window
from app.services import serializers
from app.services.plates import normalise
from app.services.scope import in_scope_condition
from app.services.serializers import event_item

router = APIRouter(tags=["detections"], dependencies=[Depends(require_permission("analytics.read"))])

READ_SORTS: dict[str, Any] = {"captured_at": PlateRead.captured_at, "confidence": PlateRead.confidence, "plate_norm": PlateRead.plate_norm, "camera_name": Camera.name}
SIGHTING_SORTS: dict[str, Any] = {"first_seen": Sighting.first_seen, "last_seen": Sighting.last_seen, "read_count": Sighting.read_count, "best_conf": Sighting.best_conf, "plate_norm": Sighting.plate_norm}


async def _alert_map(db, read_ids: list[int]) -> dict[int, int]:
    if not read_ids:
        return {}
    rows = (await db.execute(select(Alert.read_id, Alert.id).where(Alert.read_id.in_(read_ids)))).all()
    return {r[0]: r[1] for r in rows}


async def _qa_map(db, read_ids: list[int]) -> dict[int, dict]:
    if not read_ids:
        return {}
    rows = (await db.execute(select(QaLabel).where(QaLabel.read_id.in_(read_ids)))).scalars().all()
    return {q.read_id: {"true_plate": q.true_plate, "is_match": q.is_match, "char_errors": q.char_errors} for q in rows}


@router.get("/detections")
async def list_detections(
    user: CurrentUser, db: DbDep, page: PageParams = Depends(),
    plate: str | None = None, fuzzy: bool = False, camera_id: int | None = None, department_id: int | None = None, district: str | None = None,
    from_: str | None = Query(None, alias="from"), to: str | None = None, min_conf: float | None = Query(None, ge=0, le=1),
    valid_only: bool = False, mode: str | None = None, sighting_id: int | None = None,
):
    sort, order = page.resolve(READ_SORTS, "captured_at", "desc", max_size=500)
    t_from, t_to = parse_window(from_, to, 24)
    await serializers.warm(db)
    stmt = select(PlateRead, Camera).join(Camera, Camera.id == PlateRead.camera_id).where(PlateRead.captured_at >= t_from, PlateRead.captured_at <= t_to)
    cond = in_scope_condition(user_scope(user), PlateRead.camera_id)
    if cond is not None:
        stmt = stmt.where(cond)
    if plate:
        q = normalise(plate).plate_norm
        if fuzzy and q:
            stmt = stmt.where((func.levenshtein(PlateRead.plate_norm, q) <= 2) | (func.similarity(PlateRead.plate_raw, q) > 0.6))
        elif q:
            stmt = stmt.where(PlateRead.plate_norm == q)
    if camera_id:
        stmt = stmt.where(PlateRead.camera_id == camera_id)
    if department_id:
        stmt = stmt.where(Camera.department_id == department_id)
    if district:
        stmt = stmt.where(Camera.district == district)
    if min_conf is not None:
        stmt = stmt.where(PlateRead.confidence >= min_conf)
    if valid_only:
        stmt = stmt.where(PlateRead.is_valid_format.is_(True))
    if mode in ("live", "preindex"):
        stmt = stmt.where(PlateRead.mode == mode)
    if sighting_id:
        stmt = stmt.where(PlateRead.sighting_id == sighting_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    col = READ_SORTS[sort]
    rows = (await db.execute(stmt.order_by(col.desc() if order == "desc" else col.asc(), PlateRead.id.desc()).offset(page.offset).limit(page.page_size))).all()
    ids = [r.id for r, _ in rows]
    alerts = await _alert_map(db, ids)
    qa = await _qa_map(db, ids)
    return {"items": [serializers.read_item(r, c, alerts.get(r.id), qa.get(r.id)) for r, c in rows], "total": int(total), "page": page.page, "page_size": page.page_size}


@router.get("/detections/{read_id}")
async def get_detection(read_id: int, user: CurrentUser, db: DbDep):
    row = (await db.execute(select(PlateRead, Camera).join(Camera, Camera.id == PlateRead.camera_id).where(PlateRead.id == read_id))).first()
    if row is None:
        raise not_found("Detection not found")
    read, cam = row
    if not user_scope(user).allows(cam.department_id, cam.district):
        raise not_found("Detection not found")
    await serializers.warm(db)
    alerts = await _alert_map(db, [read.id])
    qa = await _qa_map(db, [read.id])
    item = serializers.read_item(read, cam, alerts.get(read.id), qa.get(read.id))
    sighting = await db.get(Sighting, read.sighting_id) if read.sighting_id else None
    item["sighting"] = serializers.sighting_item(sighting, cam) if sighting else None
    item["frame_url"] = media_url(sighting.frame_path) if sighting else None
    item["frame_sha256"] = sighting.frame_sha256 if sighting else None
    events = (await db.execute(select(Event).where((Event.read_id == read.id) | ((Event.sighting_id == read.sighting_id) & (read.sighting_id is not None))).order_by(Event.occurred_at.desc()).limit(50))).scalars().all() if read.sighting_id else (await db.execute(select(Event).where(Event.read_id == read.id))).scalars().all()
    item["events"] = [event_item(e, cam) for e in events]
    return item


@router.get("/sightings")
async def list_sightings(
    user: CurrentUser, db: DbDep, page: PageParams = Depends(),
    plate: str | None = None, camera_id: int | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None,
    closed: bool | None = None, min_conf: float | None = Query(None, ge=0, le=1), valid_only: bool = False,
):
    sort, order = page.resolve(SIGHTING_SORTS, "first_seen", "desc", max_size=500)
    t_from, t_to = parse_window(from_, to, 24)
    await serializers.warm(db)
    stmt = select(Sighting, Camera).join(Camera, Camera.id == Sighting.camera_id).where(Sighting.first_seen >= t_from, Sighting.first_seen <= t_to)
    cond = in_scope_condition(user_scope(user), Sighting.camera_id)
    if cond is not None:
        stmt = stmt.where(cond)
    if plate:
        stmt = stmt.where(Sighting.plate_norm == normalise(plate).plate_norm)
    if camera_id:
        stmt = stmt.where(Sighting.camera_id == camera_id)
    if closed is not None:
        stmt = stmt.where(Sighting.closed.is_(closed))
    if min_conf is not None:
        stmt = stmt.where(Sighting.best_conf >= min_conf)
    if valid_only:
        stmt = stmt.where(Sighting.is_valid_format.is_(True))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    col = SIGHTING_SORTS[sort]
    rows = (await db.execute(stmt.order_by(col.desc() if order == "desc" else col.asc(), Sighting.id.desc()).offset(page.offset).limit(page.page_size))).all()
    return {"items": [serializers.sighting_item(s, c) for s, c in rows], "total": int(total), "page": page.page, "page_size": page.page_size}
