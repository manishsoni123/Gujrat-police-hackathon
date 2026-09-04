"""Dashboard stats and charts (CONTRACT §5.14). Chart buckets are computed in IST."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, cast, func, select

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.core.tz import iso_z, label_ist, to_utc, utcnow
from app.db.models import Alert, Camera, Event, ObjectCount, PlateRead, Sighting, Watchlist
from app.schemas.common import parse_window
from app.services.health_poller import camera_counts, disk_usage, worker_status
from app.services.plates import format_plate
from app.services.scope import camera_conditions, in_scope_condition

router = APIRouter(tags=["dashboard"], dependencies=[Depends(require_permission("analytics.read"))])

MAX_POINTS = 500


def _ist_bucket(col, bucket: str):
    """date_trunc on the IST wall clock, returned as a UTC timestamptz."""
    return func.timezone("Asia/Kolkata", func.date_trunc(bucket, func.timezone("Asia/Kolkata", col)))


def _scoped(stmt, scope, col):
    cond = in_scope_condition(scope, col)
    return stmt.where(cond) if cond is not None else stmt


@router.get("/dashboard/stats")
async def stats(user: CurrentUser, db: DbDep):
    scope = user_scope(user)
    conds = camera_conditions(scope)
    now = utcnow()
    h1, h24 = now - timedelta(hours=1), now - timedelta(hours=24)

    cams = await camera_counts(db, conds)
    anpr_live = (await db.execute(select(func.count()).select_from(Camera).where(Camera.status != "retired", Camera.anpr_enabled.is_(True), *conds))).scalar() or 0
    recording = (await db.execute(select(func.count()).select_from(Camera).where(Camera.status != "retired", Camera.record_enabled.is_(True), *conds))).scalar() or 0

    reads_q = _scoped(select(func.count(), func.sum(cast(PlateRead.captured_at >= h1, Integer)), func.sum(cast(PlateRead.captured_at >= h24, Integer)), func.max(PlateRead.captured_at)), scope, PlateRead.camera_id)
    r_total, r_1h, r_24h, last_read = (await db.execute(reads_q)).one()

    s_q = _scoped(select(func.count(), func.sum(cast(Sighting.first_seen >= h24, Integer)), func.sum(cast((Sighting.first_seen >= h24) & Sighting.is_valid_format.is_(True), Integer))), scope, Sighting.camera_id)
    s_total, s_24h, s_valid_24h = (await db.execute(s_q)).one()

    a_q = _scoped(
        select(
            func.sum(cast(Alert.status == "new", Integer)),
            func.sum(cast(Alert.status == "acknowledged", Integer)),
            func.sum(cast(Alert.created_at >= h24, Integer)),
            func.sum(cast((Alert.priority == "critical") & Alert.status.in_(("new", "acknowledged")), Integer)),
            func.avg(func.nullif(Alert.latency_ms, None)).filter(Alert.created_at >= h24, Alert.type == "watchlist_hit"),
        ),
        scope,
        Alert.camera_id,
    )
    a_new, a_ack, a_24h, a_crit, a_lat = (await db.execute(a_q)).one()

    w_rows = (await db.execute(select(Watchlist.entity_type, func.count()).where(Watchlist.is_active.is_(True), (Watchlist.expires_at.is_(None)) | (Watchlist.expires_at > now)).group_by(Watchlist.entity_type))).all()
    w_counts = {r[0]: int(r[1]) for r in w_rows}

    oc_q = _scoped(select(ObjectCount.class_, func.sum(ObjectCount.count)).where(ObjectCount.minute >= h24).group_by(ObjectCount.class_), scope, ObjectCount.camera_id)
    oc = {r[0]: int(r[1] or 0) for r in (await db.execute(oc_q)).all()}
    for cls in ("person", "car", "motorcycle", "bus", "truck", "bicycle"):
        oc.setdefault(cls, 0)

    ev_24h = (await db.execute(_scoped(select(func.count()).select_from(Event).where(Event.occurred_at >= h24), scope, Event.camera_id))).scalar() or 0

    return {
        "generated_at": iso_z(now),
        "cameras": {**{k: cams[k] for k in ("total", "online", "degraded", "offline", "unknown")}, "anpr_live": int(anpr_live), "recording": int(recording)},
        "reads": {"last_1h": int(r_1h or 0), "last_24h": int(r_24h or 0), "total": int(r_total or 0), "last_read_at": iso_z(last_read)},
        "sightings": {"last_24h": int(s_24h or 0), "total": int(s_total or 0), "valid_format_pct_24h": round(100.0 * float(s_valid_24h or 0) / float(s_24h), 1) if s_24h else None},
        "alerts": {"new": int(a_new or 0), "acknowledged": int(a_ack or 0), "last_24h": int(a_24h or 0), "critical_open": int(a_crit or 0), "avg_latency_ms_24h": int(a_lat) if a_lat is not None else None},
        "watchlist": {"active": sum(w_counts.values()), "vehicles": w_counts.get("vehicle", 0), "persons": w_counts.get("person", 0)},
        "object_counts_24h": oc,
        "events_24h": int(ev_24h),
        "disk": disk_usage(),
        "anpr_workers": await worker_status(db),
    }


@router.get("/dashboard/charts")
async def charts(
    user: CurrentUser, db: DbDep, from_: str | None = Query(None, alias="from"), to: str | None = None,
    camera_id: int | None = None, department_id: int | None = None, bucket: str = Query("hour", pattern="^(hour|day)$"),
):
    scope = user_scope(user)
    t_from, t_to = parse_window(from_, to, 24)
    cam_filter = []
    if camera_id:
        cam_filter.append(Camera.id == camera_id)
    if department_id:
        cam_filter.append(Camera.department_id == department_id)

    async def vehicles_per(b: str) -> list[dict[str, Any]]:
        bx = _ist_bucket(Sighting.first_seen, b)
        q = select(Sighting.camera_id, Camera.name, bx.label("b"), func.count()).join(Camera, Camera.id == Sighting.camera_id).where(Sighting.first_seen >= t_from, Sighting.first_seen <= t_to, *cam_filter)
        q = _scoped(q, scope, Sighting.camera_id).group_by(Sighting.camera_id, Camera.name, bx).order_by(bx.asc(), Sighting.camera_id).limit(MAX_POINTS + 1)
        return [{"camera_id": r[0], "camera_name": r[1], "bucket_start": iso_z(to_utc(r[2])), "label_ist": label_ist(to_utc(r[2]), b), "sightings": int(r[3])} for r in (await db.execute(q)).all()]

    series = await vehicles_per(bucket)
    if len(series) > MAX_POINTS and bucket == "hour":
        bucket = "day"
        series = await vehicles_per(bucket)
    series = series[:MAX_POINTS]

    tp_q = select(Sighting.plate_norm, func.count(), func.count(func.distinct(Sighting.camera_id)), func.max(Sighting.last_seen)).join(Camera, Camera.id == Sighting.camera_id).where(Sighting.first_seen >= t_from, Sighting.first_seen <= t_to, Sighting.is_valid_format.is_(True), *cam_filter)
    tp_q = _scoped(tp_q, scope, Sighting.camera_id).group_by(Sighting.plate_norm).order_by(func.count().desc(), func.max(Sighting.last_seen).desc()).limit(20)
    top_plates = [{"plate_norm": r[0], "plate_display": format_plate(r[0]), "sightings": int(r[1]), "cameras": int(r[2]), "last_seen": iso_z(r[3])} for r in (await db.execute(tp_q)).all()]

    day = _ist_bucket(Alert.created_at, "day")
    al_q = select(Alert.camera_id, Camera.name, day.label("b"), func.count()).join(Camera, Camera.id == Alert.camera_id).where(Alert.created_at >= t_from, Alert.created_at <= t_to, *cam_filter)
    al_q = _scoped(al_q, scope, Alert.camera_id).group_by(Alert.camera_id, Camera.name, day).order_by(day.asc(), Alert.camera_id).limit(MAX_POINTS)
    alerts_per = [{"camera_id": r[0], "camera_name": r[1], "bucket_start": iso_z(to_utc(r[2])), "label_ist": label_ist(to_utc(r[2]), "day"), "alerts": int(r[3])} for r in (await db.execute(al_q)).all()]

    ob = _ist_bucket(ObjectCount.minute, bucket)
    oc_q = select(ObjectCount.camera_id, Camera.name, ob.label("b"), ObjectCount.class_, func.sum(ObjectCount.count)).join(Camera, Camera.id == ObjectCount.camera_id).where(ObjectCount.minute >= t_from, ObjectCount.minute <= t_to, *cam_filter)
    oc_q = _scoped(oc_q, scope, ObjectCount.camera_id).group_by(ObjectCount.camera_id, Camera.name, ob, ObjectCount.class_).order_by(ob.asc(), ObjectCount.camera_id, ObjectCount.class_).limit(MAX_POINTS)
    object_counts = [{"camera_id": r[0], "camera_name": r[1], "bucket_start": iso_z(to_utc(r[2])), "label_ist": label_ist(to_utc(r[2]), bucket), "class": r[3], "count": int(r[4] or 0)} for r in (await db.execute(oc_q)).all()]

    bin_expr = func.least(func.floor(PlateRead.confidence * 10), 9)
    rc_q = select(bin_expr.label("bin"), func.count()).join(Camera, Camera.id == PlateRead.camera_id).where(PlateRead.captured_at >= t_from, PlateRead.captured_at <= t_to, *cam_filter)
    rc_q = _scoped(rc_q, scope, PlateRead.camera_id).group_by(bin_expr).order_by(bin_expr.desc())
    reads_by_conf = [{"bin": f"{int(r[0]) / 10:.1f}-{(int(r[0]) + 1) / 10:.1f}", "reads": int(r[1])} for r in (await db.execute(rc_q)).all() if r[0] is not None]

    return {
        "window": {"from": iso_z(t_from), "to": iso_z(t_to), "bucket": bucket},
        "vehicles_per_hour": series,
        "top_plates": top_plates,
        "alerts_per_camera_day": alerts_per,
        "object_counts": object_counts,
        "reads_by_confidence": reads_by_conf,
    }
