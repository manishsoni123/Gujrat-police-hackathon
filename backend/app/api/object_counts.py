"""Object counts (A6, CONTRACT §5.17): per-minute or per-hour buckets in IST labels."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.core.errors import validation_error
from app.core.tz import IST, iso_z, label_ist, to_utc
from app.db.models import OBJECT_CLASSES, ObjectCount
from app.schemas.common import csv_list, parse_window
from app.services.scope import in_scope_condition

router = APIRouter(tags=["object-counts"], dependencies=[Depends(require_permission("cameras.read"))])

MAX_POINTS = 10_000


def _bucket_expr(bucket: str):
    if bucket == "minute":
        return ObjectCount.minute
    # hour buckets in IST, returned as UTC timestamptz
    return func.timezone("Asia/Kolkata", func.date_trunc("hour", func.timezone("Asia/Kolkata", ObjectCount.minute)))


@router.get("/object-counts")
async def object_counts(
    user: CurrentUser, db: DbDep, camera_id: int | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None,
    class_: str | None = Query(None, alias="class"), bucket: str = Query("minute", pattern="^(minute|hour)$"),
):
    t_from, t_to = parse_window(from_, to, 1)
    classes = csv_list(class_) if class_ else None
    if classes and any(c not in OBJECT_CLASSES for c in classes):
        raise validation_error("Unknown class", [{"field": "class", "message": f"must be one of {', '.join(OBJECT_CLASSES)}"}])

    async def run(b: str):
        bx = _bucket_expr(b)
        stmt = select(ObjectCount.camera_id, bx.label("bucket"), ObjectCount.class_, func.sum(ObjectCount.count)).where(ObjectCount.minute >= t_from, ObjectCount.minute <= t_to)
        cond = in_scope_condition(user_scope(user), ObjectCount.camera_id)
        if cond is not None:
            stmt = stmt.where(cond)
        if camera_id:
            stmt = stmt.where(ObjectCount.camera_id == camera_id)
        if classes:
            stmt = stmt.where(ObjectCount.class_.in_(classes))
        stmt = stmt.group_by(ObjectCount.camera_id, bx, ObjectCount.class_).order_by(bx.asc(), ObjectCount.camera_id, ObjectCount.class_).limit(MAX_POINTS + 1)
        return (await db.execute(stmt)).all()

    rows = await run(bucket)
    if len(rows) > MAX_POINTS and bucket == "minute":
        bucket = "hour"
        rows = await run(bucket)
    rows = rows[:MAX_POINTS]
    items = []
    totals: dict[str, int] = {}
    for cam, b, cls, n in rows:
        b = to_utc(b) if isinstance(b, datetime) else b
        items.append({"camera_id": cam, "bucket_start": iso_z(b), "label_ist": label_ist(b, "hour") if bucket == "hour" else b.astimezone(IST).strftime("%d %b %H:%M"), "class": cls, "count": int(n)})
        totals[cls] = totals.get(cls, 0) + int(n)
    return {"window": {"from": iso_z(t_from), "to": iso_z(t_to), "bucket": bucket}, "items": items, "totals": totals}
