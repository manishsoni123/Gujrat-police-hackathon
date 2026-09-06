"""GeoJSON endpoints (CONTRACT §5.3)."""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.core.config import settings
from app.core.tz import iso_z
from app.db.models import Camera, District, Poi
from app.schemas.common import csv_list
from app.services import gap_analysis, lookups
from app.services import settings_service as cfg
from app.services.scope import camera_conditions

router = APIRouter(tags=["geo"], dependencies=[Depends(require_permission("cameras.read"))])

_coverage_cache: dict[tuple, tuple[float, dict]] = {}


@router.get("/geo/cameras")
async def geo_cameras(user: CurrentUser, db: DbDep, department_id: int | None = None, district: str | None = None, status: str | None = None, type: str | None = None):
    await lookups.departments(db)
    stmt = select(Camera).where(Camera.status != "retired", Camera.lat.isnot(None), Camera.lon.isnot(None), *camera_conditions(user_scope(user)))
    if department_id is not None:
        stmt = stmt.where(Camera.department_id == department_id)
    if district:
        stmt = stmt.where(Camera.district == district)
    if status:
        stmt = stmt.where(Camera.status.in_(csv_list(status) or []))
    if type:
        stmt = stmt.where(Camera.type.in_(csv_list(type) or []))
    cams = (await db.execute(stmt.order_by(Camera.id).limit(5000))).scalars().all()
    feats = []
    for c in cams:
        d = lookups.dept_sync(c.department_id)
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [c.lon, c.lat]},
            "properties": {
                "id": c.id, "external_id": c.external_id, "name": c.name, "department_code": d.code if d else None, "department_name": d.name if d else None,
                "district": c.district, "police_station": c.police_station, "type": c.type, "ownership": c.ownership, "status": c.status,
                "maintenance_status": c.maintenance_status, "anpr_enabled": c.anpr_enabled, "live": c.live, "codec": c.codec,
                "heading_deg": c.heading_deg, "fov_deg": c.fov_deg, "last_seen_at": iso_z(c.last_seen_at),
                "location_confidence": c.location_confidence,
            },
        })
    return {"type": "FeatureCollection", "features": feats}


@router.get("/geo/districts")
async def geo_districts(user: CurrentUser, db: DbDep):
    scope = user_scope(user)
    conds = camera_conditions(scope)
    counts_q = select(Camera.district, func.count(), func.sum(func.cast(Camera.status == "online", __import__("sqlalchemy").Integer))).where(Camera.status != "retired", *conds).group_by(Camera.district)
    counts = {r[0]: (int(r[1]), int(r[2] or 0)) for r in (await db.execute(counts_q)).all()}
    rows = (await db.execute(select(District.id, District.name, District.code, func.ST_AsGeoJSON(func.ST_SimplifyPreserveTopology(District.geom, 0.002), 5)).order_by(District.name))).all()
    feats = []
    for did, name, code, gj in rows:
        total, online = counts.get(name, (0, 0))
        feats.append({"type": "Feature", "geometry": json.loads(gj), "properties": {"id": did, "name": name, "code": code, "camera_count": total, "online_count": online}})
    return {"type": "FeatureCollection", "features": feats}


@router.get("/geo/pois")
async def geo_pois(user: CurrentUser, db: DbDep, district: str | None = None, type: str | None = None):
    scope_sql, scope_params = gap_analysis._scope_sql(user_scope(user))
    where = []
    params: dict[str, Any] = dict(scope_params)
    if district:
        where.append("p.district = :district")
        params["district"] = district
    if type:
        where.append("p.type = ANY(:types)")
        params["types"] = csv_list(type)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = (
        await db.execute(
            text(
                f"""
                SELECT p.id, p.name, p.type, p.district, p.lat, p.lon, n.dist
                FROM pois p
                LEFT JOIN LATERAL (
                    SELECT ST_Distance(c.geog, p.geog) AS dist FROM cameras c
                    WHERE c.geog IS NOT NULL AND c.status <> 'retired' {scope_sql}
                    ORDER BY c.geog <-> p.geog LIMIT 1
                ) n ON true
                {where_sql}
                ORDER BY p.district, p.name
                """
            ),
            params,
        )
    ).all()
    feats = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]}, "properties": {"id": r.id, "name": r.name, "type": r.type, "district": r.district, "nearest_camera_m": round(float(r.dist), 1) if r.dist is not None else None}}
        for r in rows
    ]
    return {"type": "FeatureCollection", "features": feats}


@router.get("/geo/coverage")
async def geo_coverage(user: CurrentUser, db: DbDep, radius: int | None = Query(None, ge=10, le=5000), district: str | None = None, include_offline: bool = False):
    scope = user_scope(user)
    r = radius or cfg.get_int("gap.coverage_radius_m")
    key = (scope.department_id, scope.district, r, district, include_offline)
    now = time.monotonic()
    hit = _coverage_cache.get(key)
    if hit and now - hit[0] < settings.GAP_CACHE_SECONDS:
        return hit[1]
    data = await gap_analysis.coverage_geojson(db, scope, r, district, include_offline)
    _coverage_cache[key] = (now, data)
    return data
