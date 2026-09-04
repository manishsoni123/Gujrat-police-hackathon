"""Vehicle search, route, confirm, route PDF (CONTRACT §5.10)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.core.errors import validation_error
from app.core.tz import fmt_ist, ist_stamp_short, iso_z, utcnow
from app.db.models import District, RouteConfirmation, Sighting
from app.schemas.analytics import RouteConfirmRequest
from app.schemas.common import parse_window
from app.services import plate_search, serializers
from app.services import settings_service as cfg
from app.services.audit import set_audit
from app.services.plates import format_plate, normalise
from app.services.report_builder import route_pdf, store_report

router = APIRouter(tags=["vehicles"])


def _norm_or_422(plate: str) -> str:
    n = normalise(plate)
    if not n.plate_norm:
        raise validation_error("Plate is empty", [{"field": "plate", "message": "enter a registration number"}])
    return n.plate_norm


@router.get("/vehicles/search", dependencies=[Depends(require_permission("analytics.read"))])
async def search(
    user: CurrentUser, db: DbDep, request: Request, q: str = Query(..., min_length=1), from_: str | None = Query(None, alias="from"), to: str | None = None,
    camera_id: int | None = None, department_id: int | None = None, limit: int = Query(50, ge=1, le=200),
):
    norm = normalise(q)
    if not norm.plate_norm:
        raise validation_error("Plate is empty", [{"field": "q", "message": "enter a registration number"}])
    t_from, t_to = parse_window(from_, to, cfg.get_int("route.default_window_h"))
    await serializers.warm(db)
    res = await plate_search.search(db, norm.plate_norm, t_from, t_to, user_scope(user), camera_id, department_id, limit)
    set_audit(request, action="vehicle.search", entity="plate", entity_id=norm.plate_norm, after={"q": q, "normalised": norm.plate_norm, "exact": len(res["exact"]), "fuzzy": len(res["fuzzy"])})
    return {"query": {"raw": q, "normalised": norm.plate_norm, "is_valid_format": norm.is_valid_format, "from": iso_z(t_from), "to": iso_z(t_to)}, **res}


@router.post("/vehicles/{plate}/confirm", dependencies=[Depends(require_permission("route.confirm"))])
async def confirm(plate: str, body: RouteConfirmRequest, user: CurrentUser, db: DbDep, request: Request):
    plate_norm = _norm_or_422(plate)
    scope = user_scope(user)
    saved = 0
    for d in body.decisions:
        s = await db.get(Sighting, d.sighting_id)
        if s is None:
            raise validation_error("Unknown sighting", [{"field": "decisions", "message": f"sighting {d.sighting_id} not found"}])
        from app.services.scope import scoped_camera

        if await scoped_camera(db, scope, s.camera_id) is None:
            raise validation_error("Unknown sighting", [{"field": "decisions", "message": f"sighting {d.sighting_id} not found"}])
        stmt = pg_insert(RouteConfirmation).values(query_plate=plate_norm, sighting_id=d.sighting_id, decision=d.decision, user_id=user.id, created_at=utcnow())
        stmt = stmt.on_conflict_do_update(constraint="uq_route_conf", set_={"decision": d.decision, "user_id": user.id, "created_at": utcnow()})
        await db.execute(stmt)
        saved += 1
    await db.commit()
    set_audit(request, action="vehicle.confirm", entity="plate", entity_id=plate_norm, after={"decisions": [d.model_dump() for d in body.decisions]})
    return {"saved": saved}


async def _route(db, user, plate: str, from_: str | None, to: str | None, include: str, max_gap_h: float):
    plate_norm = _norm_or_422(plate)
    if include not in ("confirmed", "all"):
        raise validation_error("Invalid include", [{"field": "include", "message": "confirmed or all"}])
    t_from, t_to = parse_window(from_, to, cfg.get_int("route.default_window_h"))
    await serializers.warm(db)
    built = await plate_search.route(db, plate_norm, t_from, t_to, user_scope(user), include, max_gap_h)
    return plate_norm, t_from, t_to, built


@router.get("/vehicles/{plate}/route", dependencies=[Depends(require_permission("analytics.read"))])
async def route(plate: str, user: CurrentUser, db: DbDep, request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None, include: str = "confirmed", max_gap_h: float = Query(6.0, gt=0, le=720)):
    plate_norm, t_from, t_to, built = await _route(db, user, plate, from_, to, include, max_gap_h)
    for s in built["sightings"]:
        s["first_seen"] = iso_z(s["first_seen"])
        s["last_seen"] = iso_z(s["last_seen"])
    set_audit(request, action="vehicle.route", entity="plate", entity_id=plate_norm, after={"sightings": len(built["sightings"]), "cameras": built["cameras_count"], "flags": len(built["flags"]), "include": include})
    return {"plate": plate_norm, "plate_display": format_plate(plate_norm), "window": {"from": iso_z(t_from), "to": iso_z(t_to), "include": include}, **built}


@router.get("/vehicles/{plate}/route.pdf", dependencies=[Depends(require_permission("reports.export"))])
async def route_pdf_endpoint(plate: str, user: CurrentUser, db: DbDep, request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None, include: str = "confirmed", max_gap_h: float = Query(6.0, gt=0, le=720)):
    plate_norm, t_from, t_to, built = await _route(db, user, plate, from_, to, include, max_gap_h)
    # crop sha + path for the table/thumbnails
    ids = [s["sighting_id"] for s in built["sightings"]]
    if ids:
        rows = (await db.execute(select(Sighting.id, Sighting.best_crop_path, Sighting.best_read_id).where(Sighting.id.in_(ids)))).all()
        from app.db.models import PlateRead

        crop_by_sid = {r[0]: r[1] for r in rows}
        read_ids = [r[2] for r in rows if r[2]]
        sha_by_read = {}
        if read_ids:
            sha_by_read = {r[0]: r[1] for r in (await db.execute(select(PlateRead.id, PlateRead.crop_sha256).where(PlateRead.id.in_(read_ids)))).all()}
        best_read_by_sid = {r[0]: r[2] for r in rows}
        for s in built["sightings"]:
            s["crop_path"] = crop_by_sid.get(s["sighting_id"])
            s["crop_sha256"] = sha_by_read.get(best_read_by_sid.get(s["sighting_id"]))
    districts = {s["camera"].get("district") for s in built["sightings"] if s["camera"].get("district")}
    outline = []
    if districts:
        for (gj,) in (await db.execute(select(func.ST_AsGeoJSON(func.ST_SimplifyPreserveTopology(District.geom, 0.01), 4)).where(District.name.in_(list(districts))))).all():
            geom = json.loads(gj)
            polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
            for poly in polys:
                outline.append([(pt[1], pt[0]) for pt in poly[0]])
    window = {"from_ist": fmt_ist(t_from), "to_ist": fmt_ist(t_to), "include": include}
    pdf = route_pdf(user.username, plate_norm, window, built, outline)
    rf = await store_report(db, "route_pdf", pdf, "pdf", user.username, user.id, {"plate": plate_norm, "from": iso_z(t_from), "to": iso_z(t_to), "include": include}, len(built["sightings"]))
    set_audit(request, action="report.route", entity="report_file", entity_id=rf.id, after={"plate": plate_norm, "sha256": rf.sha256, "path": rf.path, "sightings": len(built["sightings"])})
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="route_{plate_norm}_{ist_stamp_short()}IST.pdf"', "X-Sentinel-Sha256": rf.sha256, "X-Sentinel-Report-Id": str(rf.id)})
