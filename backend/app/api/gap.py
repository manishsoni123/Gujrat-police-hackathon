"""Gap analysis endpoints (CONTRACT §5.8)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.core.config import settings
from app.core.errors import validation_error
from app.core.tz import fmt_ist, ist_stamp_short, utcnow
from app.db.models import Camera, District
from app.services import gap_analysis
from app.services import settings_service as cfg
from app.services.audit import set_audit
from app.services.report_builder import build_csv, gap_pdf, rows_hash, store_report
from app.services.scope import camera_conditions

router = APIRouter(tags=["gap-analysis"])


def _params(coverage_radius_m: int | None, poi_radius_m: int | None, grid_m: int | None, ageing_years: int | None) -> dict[str, Any]:
    return {
        "coverage_radius_m": coverage_radius_m or cfg.get_int("gap.coverage_radius_m"),
        "poi_radius_m": poi_radius_m or cfg.get_int("gap.poi_radius_m"),
        "grid_m": grid_m or cfg.get_int("gap.grid_m"),
        "ageing_years": ageing_years or cfg.get_int("gap.ageing_years"),
    }


@router.get("/gap-analysis", dependencies=[Depends(require_permission("cameras.read"))])
async def gap(
    user: CurrentUser, db: DbDep,
    coverage_radius_m: int | None = Query(None, ge=10, le=5000), poi_radius_m: int | None = Query(None, ge=10, le=10000),
    grid_m: int | None = Query(None, ge=100, le=5000), ageing_years: int | None = Query(None, ge=1, le=50),
    district: str | None = None, refresh: bool = False,
):
    return await gap_analysis.compute(db, user_scope(user), _params(coverage_radius_m, poi_radius_m, grid_m, ageing_years), district, refresh)


@router.get("/gap-analysis/export", dependencies=[Depends(require_permission("cameras.export"))])
async def gap_export(
    user: CurrentUser, db: DbDep, request: Request, format: str = Query("csv"),
    coverage_radius_m: int | None = Query(None, ge=10, le=5000), poi_radius_m: int | None = Query(None, ge=10, le=10000),
    grid_m: int | None = Query(None, ge=100, le=5000), ageing_years: int | None = Query(None, ge=1, le=50), district: str | None = None,
):
    if format not in ("csv", "pdf"):
        raise validation_error("Unsupported format", [{"field": "format", "message": "csv or pdf"}])
    params = _params(coverage_radius_m, poi_radius_m, grid_m, ageing_years)
    data = await gap_analysis.compute(db, user_scope(user), params, district, False)
    stamp = ist_stamp_short()
    if format == "csv":
        header, rows = gap_analysis.flatten_for_csv(data)
        trailer = f"# {settings.PRODUCT_NAME} {settings.APP_VERSION} | rows={len(rows)} | generated {fmt_ist(utcnow())} by {user.username} | params={json.dumps(params)} | sha256(rows)={rows_hash(rows)}"
        content = build_csv(header, rows, trailer)
        rf = await store_report(db, "gap_csv", content, "csv", user.username, user.id, params, len(rows))
        media, fname = "text/csv; charset=utf-8", f"gap_analysis_{stamp}IST.csv"
    else:
        cams = (await db.execute(select(Camera).where(Camera.status != "retired", Camera.lat.isnot(None), *camera_conditions(user_scope(user)), *([Camera.district == district] if district else [])))).scalars().all()
        points = [(c.lat, c.lon, "") for c in cams]
        outline = await _outlines(db, district or None, [d["district"] for d in data["by_district"]])
        cells = [[(pt[1], pt[0]) for pt in f["geometry"]["coordinates"][0]] for f in data["zero_coverage"]["features"]]
        content = gap_pdf(user.username, data, points, outline, cells)
        rf = await store_report(db, "gap_pdf", content, "pdf", user.username, user.id, params, None)
        media, fname = "application/pdf", f"gap_analysis_{stamp}IST.pdf"
    set_audit(request, action="report.gap", entity="report_file", entity_id=rf.id, after={"format": format, "sha256": rf.sha256, "path": rf.path, "params": params})
    return Response(content=content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{fname}"', "X-Sentinel-Sha256": rf.sha256, "X-Sentinel-Report-Id": str(rf.id)})


async def _outlines(db, district: str | None, names: list[str]) -> list[list[tuple[float, float]]]:
    wanted = [district] if district else [n for n in names if n and n != "Unknown"]
    if not wanted:
        return []
    rows = (await db.execute(select(func.ST_AsGeoJSON(func.ST_SimplifyPreserveTopology(District.geom, 0.01), 4)).where(District.name.in_(wanted)))).all()
    rings: list[list[tuple[float, float]]] = []
    for (gj,) in rows:
        geom = json.loads(gj)
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            rings.append([(pt[1], pt[0]) for pt in poly[0]])
    return rings
