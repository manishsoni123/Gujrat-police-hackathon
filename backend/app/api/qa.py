"""ANPR spot-check labelling (A8, CONTRACT §5.15): random unlabelled sample + label upsert."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.core.errors import validation_error
from app.core.tz import utcnow
from app.db.models import Camera, PlateRead, QaLabel
from app.schemas.analytics import QaLabelsRequest
from app.schemas.common import parse_window
from app.services import serializers
from app.services.audit import set_audit
from app.services.plates import levenshtein, normalise
from app.services.scope import in_scope_condition

router = APIRouter(tags=["qa"])


@router.get("/qa/sample", dependencies=[Depends(require_permission("analytics.read"))])
async def sample(user: CurrentUser, db: DbDep, camera_id: int | None = None, n: int = Query(30, ge=1, le=200), from_: str | None = Query(None, alias="from"), to: str | None = None):
    t_from, t_to = parse_window(from_, to, 24 * 7)
    await serializers.warm(db)
    labelled = select(QaLabel.read_id)
    stmt = select(PlateRead, Camera).join(Camera, Camera.id == PlateRead.camera_id).where(PlateRead.captured_at >= t_from, PlateRead.captured_at <= t_to, PlateRead.id.notin_(labelled), PlateRead.crop_path.isnot(None))
    cond = in_scope_condition(user_scope(user), PlateRead.camera_id)
    if cond is not None:
        stmt = stmt.where(cond)
    if camera_id:
        stmt = stmt.where(PlateRead.camera_id == camera_id)
    rows = (await db.execute(stmt.order_by(func.random()).limit(n))).all()
    return {"items": [serializers.read_item(r, c) for r, c in rows], "total": len(rows), "window": {"from": t_from, "to": t_to, "camera_id": camera_id}}


@router.post("/qa/labels", dependencies=[Depends(require_permission("events.write"))])
async def label(body: QaLabelsRequest, user: CurrentUser, db: DbDep, request: Request):
    if not body.labels:
        raise validation_error("No labels", [{"field": "labels", "message": "at least one label required"}])
    ids = [item.read_id for item in body.labels]
    q = select(PlateRead).where(PlateRead.id.in_(ids))
    cond = in_scope_condition(user_scope(user), PlateRead.camera_id)
    if cond is not None:
        q = q.where(cond)
    reads = {r.id: r for r in (await db.execute(q)).scalars().all()}
    missing = [i for i in ids if i not in reads]
    if missing:
        raise validation_error("Unknown reads", [{"field": "labels", "message": f"read ids not found: {missing[:10]}"}])
    saved = exact = 0
    total_chars = total_errors = 0
    now = utcnow()
    for item in body.labels:
        read = reads[item.read_id]
        true_plate = normalise(item.true_plate).plate_norm if item.true_plate.strip() else ""
        is_match = bool(true_plate) and true_plate == read.plate_norm
        errors = levenshtein(true_plate, read.plate_norm)
        stmt = pg_insert(QaLabel).values(read_id=read.id, true_plate=true_plate[:16], is_match=is_match, char_errors=errors, labelled_by=user.id, created_at=now)
        stmt = stmt.on_conflict_do_update(index_elements=[QaLabel.read_id], set_={"true_plate": true_plate[:16], "is_match": is_match, "char_errors": errors, "labelled_by": user.id, "created_at": now})
        await db.execute(stmt)
        saved += 1
        exact += 1 if is_match else 0
        total_chars += max(len(true_plate), len(read.plate_norm))
        total_errors += errors
    await db.commit()
    result = {"saved": saved, "exact": exact, "char_accuracy_pct": round(100.0 * (total_chars - total_errors) / total_chars, 1) if total_chars else None}
    set_audit(request, action="qa.label", entity="qa_labels", entity_id=f"{saved} reads", after={**result, "read_ids": ids[:50]})
    return result
