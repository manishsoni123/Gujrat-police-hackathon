"""Reports (CONTRACT §5.15): output report CSV/PDF (O1), analytics quality (A8), report history."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.core.config import settings
from app.core.errors import forbidden, validation_error
from app.core.rbac import has_permission
from app.core.tz import fmt_ist, iso_z, utcnow
from app.db.models import Alert, Camera, ObjectCount, PlateRead, ReportFile, Sighting
from app.schemas.common import PageParams, parse_window
from app.services import lookups, quality, serializers
from app.services.audit import set_audit
from app.services.plates import normalise
from app.services.report_builder import DETECTIONS_CSV_COLUMNS, build_csv, detections_pdf, quality_pdf, rows_hash, store_report
from app.services.scope import in_scope_condition

router = APIRouter(tags=["reports"])

CSV_CAP = 200_000
PDF_CAP = 5_000


def _stamp_for_name(dt) -> str:
    from app.core.tz import IST, to_utc

    return to_utc(dt).astimezone(IST).strftime("%Y%m%d_%H%M")


@router.get("/reports/detections", dependencies=[Depends(require_permission("reports.export"))])
async def detections_report(
    user: CurrentUser, db: DbDep, request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None,
    camera_id: int | None = None, department_id: int | None = None, district: str | None = None, min_conf: float | None = Query(None, ge=0, le=1),
    valid_only: bool = False, plate: str | None = None, format: str = Query("csv"),
):
    if format not in ("csv", "pdf"):
        raise validation_error("Unsupported format", [{"field": "format", "message": "csv or pdf"}])
    t_from, t_to = parse_window(from_, to, None, max_days=7, from_required=True)
    scope = user_scope(user)
    await serializers.warm(db)
    stmt = select(PlateRead, Camera).join(Camera, Camera.id == PlateRead.camera_id).where(PlateRead.captured_at >= t_from, PlateRead.captured_at <= t_to)
    cond = in_scope_condition(scope, PlateRead.camera_id)
    if cond is not None:
        stmt = stmt.where(cond)
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
    if plate:
        stmt = stmt.where(PlateRead.plate_norm == normalise(plate).plate_norm)
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    cap = CSV_CAP if format == "csv" else PDF_CAP
    rows_db = (await db.execute(stmt.order_by(PlateRead.captured_at.asc(), PlateRead.id.asc()).limit(cap))).all()
    filters = {k: v for k, v in {"from": iso_z(t_from), "to": iso_z(t_to), "camera_id": camera_id, "department_id": department_id, "district": district, "min_conf": min_conf, "valid_only": valid_only, "plate": plate}.items() if v not in (None, False)}
    capped = total > len(rows_db)

    records: list[dict[str, Any]] = []
    for r, c in rows_db:
        d = lookups.dept_sync(c.department_id)
        records.append({
            "captured_at_ist": fmt_ist(r.captured_at), "captured_at_utc": iso_z(r.captured_at), "camera_id": c.id, "camera_external_id": c.external_id,
            "camera_name": c.name, "department": d.code if d else None, "district": c.district, "lat": c.lat, "lon": c.lon, "plate": r.plate_norm,
            "plate_raw": r.plate_raw, "is_valid_format": r.is_valid_format, "confidence": round(float(r.confidence), 3), "sighting_id": r.sighting_id,
            "read_id": r.id, "crop_path": r.crop_path, "crop_sha256": r.crop_sha256, "mode": r.mode,
        })
    fname_stamp = f"{_stamp_for_name(t_from)}_{_stamp_for_name(t_to)}_IST"
    if format == "csv":
        rows = [[rec[c] for c in DETECTIONS_CSV_COLUMNS] for rec in records]
        trailer = f"# {settings.PRODUCT_NAME} {settings.APP_VERSION} | rows={len(rows)} | generated {fmt_ist(utcnow())} by {user.username} | filters={json.dumps(filters)} | sha256(rows)={rows_hash(rows)}" + (f" | capped_at={cap} total={total}" if capped else "")
        content = build_csv(DETECTIONS_CSV_COLUMNS, rows, trailer)
        rf = await store_report(db, "detections_csv", content, "csv", user.username, user.id, filters, len(rows))
        media, fname = "text/csv; charset=utf-8", f"detections_{fname_stamp}.csv"
    else:
        summary = await _summary(db, scope, t_from, t_to, records, camera_id, department_id, district)
        q = await quality.compute(db, scope, t_from, t_to, camera_id)
        window = {"from_ist": fmt_ist(t_from), "to_ist": fmt_ist(t_to), "filters_text": ", ".join(f"{k}={v}" for k, v in filters.items() if k not in ("from", "to")) or "none"}
        content = detections_pdf(user.username, window, summary, records, q, capped)
        rf = await store_report(db, "detections_pdf", content, "pdf", user.username, user.id, filters, len(records))
        media, fname = "application/pdf", f"detections_{fname_stamp}.pdf"
    set_audit(request, action="report.detections", entity="report_file", entity_id=rf.id, after={"format": format, "rows": len(records), "total": total, "sha256": rf.sha256, "path": rf.path, "filters": filters})
    return Response(content=content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{fname}"', "X-Sentinel-Sha256": rf.sha256, "X-Sentinel-Report-Id": str(rf.id)})


async def _summary(db, scope, t_from, t_to, records: list[dict[str, Any]], camera_id, department_id, district) -> dict[str, Any]:
    cams = {r["camera_id"] for r in records}
    depts = {r["department"] for r in records if r["department"]}
    valid = sum(1 for r in records if r["is_valid_format"])
    s_q = select(func.count(), func.count(func.distinct(Sighting.plate_norm))).where(Sighting.first_seen >= t_from, Sighting.first_seen <= t_to)
    cond = in_scope_condition(scope, Sighting.camera_id)
    if cond is not None:
        s_q = s_q.where(cond)
    if camera_id:
        s_q = s_q.where(Sighting.camera_id == camera_id)
    if department_id or district:
        sub = select(Camera.id)
        if department_id:
            sub = sub.where(Camera.department_id == department_id)
        if district:
            sub = sub.where(Camera.district == district)
        s_q = s_q.where(Sighting.camera_id.in_(sub))
    sightings, unique_plates = (await db.execute(s_q)).one()
    oc_q = select(ObjectCount.class_, func.sum(ObjectCount.count)).where(ObjectCount.minute >= t_from, ObjectCount.minute <= t_to).group_by(ObjectCount.class_)
    ocond = in_scope_condition(scope, ObjectCount.camera_id)
    if ocond is not None:
        oc_q = oc_q.where(ocond)
    if camera_id:
        oc_q = oc_q.where(ObjectCount.camera_id == camera_id)
    oc = {r[0]: int(r[1] or 0) for r in (await db.execute(oc_q)).all()}
    a_q = select(func.count()).select_from(Alert).where(Alert.created_at >= t_from, Alert.created_at <= t_to)
    acond = in_scope_condition(scope, Alert.camera_id)
    if acond is not None:
        a_q = a_q.where(acond)
    if camera_id:
        a_q = a_q.where(Alert.camera_id == camera_id)
    alerts = int((await db.execute(a_q)).scalar() or 0)
    return {
        "cameras": len(cams), "departments": len(depts), "reads": len(records), "valid_pct": (100.0 * valid / len(records)) if records else 0.0,
        "sightings": int(sightings or 0), "unique_plates": int(unique_plates or 0), "object_counts": oc, "alerts": alerts,
    }


@router.get("/reports/quality")
async def quality_report(
    user: CurrentUser, db: DbDep, request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None,
    camera_id: int | None = None, format: str = Query("json"),
):
    if format not in ("json", "pdf"):
        raise validation_error("Unsupported format", [{"field": "format", "message": "json or pdf"}])
    needed = "reports.export" if format == "pdf" else "analytics.read"
    if not has_permission(user.role, needed):
        raise forbidden()
    t_from, t_to = parse_window(from_, to, 24, max_days=31)
    await serializers.warm(db)
    q = await quality.compute(db, user_scope(user), t_from, t_to, camera_id)
    if format == "json":
        return q
    q["window"].update({"from_ist": fmt_ist(t_from), "to_ist": fmt_ist(t_to)})
    content = quality_pdf(user.username, q)
    rf = await store_report(db, "quality_pdf", content, "pdf", user.username, user.id, {"from": iso_z(t_from), "to": iso_z(t_to), "camera_id": camera_id}, q["reads_total"])
    set_audit(request, action="report.quality", entity="report_file", entity_id=rf.id, after={"sha256": rf.sha256, "path": rf.path, "reads": q["reads_total"], "labelled": q["labelled"]})
    return Response(content=content, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="quality_{_stamp_for_name(t_from)}_{_stamp_for_name(t_to)}_IST.pdf"', "X-Sentinel-Sha256": rf.sha256, "X-Sentinel-Report-Id": str(rf.id)})


HISTORY_SORTS: dict[str, Any] = {"created_at": ReportFile.created_at, "type": ReportFile.type, "size_bytes": ReportFile.size_bytes}


@router.get("/reports/history", dependencies=[Depends(require_permission("reports.export"))])
async def history(user: CurrentUser, db: DbDep, page: PageParams = Depends(), type: str | None = None):
    sort, order = page.resolve(HISTORY_SORTS, "created_at", "desc")
    stmt = select(ReportFile)
    if user.role != "admin":
        stmt = stmt.where(ReportFile.created_by == user.id)
    if type:
        stmt = stmt.where(ReportFile.type == type)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    col = HISTORY_SORTS[sort]
    rows = (await db.execute(stmt.order_by(col.desc() if order == "desc" else col.asc(), ReportFile.id.desc()).offset(page.offset).limit(page.page_size))).scalars().all()
    await lookups.usernames(db)
    return {"items": [serializers.report_file_item(r) for r in rows], "total": int(total), "page": page.page, "page_size": page.page_size}
