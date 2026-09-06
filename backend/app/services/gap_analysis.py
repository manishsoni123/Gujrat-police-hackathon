"""Gap analysis (CONTRACT §5.8): PostGIS coverage, zero-coverage grid, POIs, ageing, recommendations."""

from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rbac import Scope
from app.core.tz import iso_z, utcnow
from app.db.models import Camera, CameraHealthLog, District, Poi
from app.services import lookups, serializers
from app.services.scope import camera_conditions

MAX_CELLS = 5000
log = logging.getLogger("sentinel.gap")

_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}


def _scope_sql(scope: Scope) -> tuple[str, dict[str, Any]]:
    """SQL fragment (prefixed with AND) + params for the camera scope, for raw queries."""
    if scope.unrestricted:
        return "", {}
    sql = " AND c.department_id = :scope_dept"
    params: dict[str, Any] = {"scope_dept": scope.department_id}
    if scope.district:
        sql += " AND c.district = :scope_district"
        params["scope_district"] = scope.district
    return sql, params


async def compute(db: AsyncSession, scope: Scope, params: dict[str, Any], district: str | None, refresh: bool) -> dict[str, Any]:
    key = (scope.department_id, scope.district, district, tuple(sorted(params.items())))
    now = time.monotonic()
    if not refresh and key in _cache and now - _cache[key][0] < settings.GAP_CACHE_SECONDS:
        data = dict(_cache[key][1])
        data["cached"] = True
        return data
    data = await _compute(db, scope, params, district)
    _cache[key] = (now, data)
    return data


async def _compute(db: AsyncSession, scope: Scope, p: dict[str, Any], district: str | None) -> dict[str, Any]:
    await lookups.departments(db)
    radius = int(p["coverage_radius_m"])
    poi_radius = int(p["poi_radius_m"])
    grid = int(p["grid_m"])
    ageing_years = int(p["ageing_years"])
    scope_sql, scope_params = _scope_sql(scope)
    dist_sql = " AND c.district = :district" if district else ""
    dparams = {"district": district} if district else {}

    cams = (await db.execute(select(Camera).where(Camera.status != "retired", *camera_conditions(scope), *( [Camera.district == district] if district else [] )))).scalars().all()
    cam_by_id = {c.id: c for c in cams}
    offline_pct = await _offline_pct_24h(db, [c.id for c in cams])

    # 1. coverage by area
    by_area: dict[tuple, dict[str, Any]] = {}
    by_district: dict[str, dict[str, Any]] = {}
    for c in cams:
        dcode = lookups.dept_sync(c.department_id).code if lookups.dept_sync(c.department_id) else "UNASSIGNED"
        k = (c.district, c.police_station, c.ward)
        a = by_area.setdefault(k, {"district": c.district, "police_station": c.police_station, "ward": c.ward, "total": 0, "online": 0, "degraded": 0, "offline": 0, "not_streaming": 0, "unknown": 0, "by_department": {}})
        a["total"] += 1
        a[c.status if c.status in ("online", "degraded", "offline", "not_streaming", "unknown") else "unknown"] += 1
        a["by_department"][dcode] = a["by_department"].get(dcode, 0) + 1
        d = by_district.setdefault(c.district or "Unknown", {"district": c.district or "Unknown", "total": 0, "online": 0, "degraded": 0, "offline": 0, "not_streaming": 0, "unknown": 0, "zero_coverage_cells": 0, "uncovered_pois": 0, "ageing": 0, "metadata_gaps": 0})
        d["total"] += 1
        d[c.status if c.status in ("online", "degraded", "offline", "not_streaming", "unknown") else "unknown"] += 1
    for a in by_area.values():
        a["online_pct"] = round(100.0 * a["online"] / a["total"], 1) if a["total"] else 0.0
    area_list = sorted(by_area.values(), key=lambda x: (x["district"] or "", x["police_station"] or "", x["ward"] or ""))

    # 5. department gaps (departments × districts-with-cameras with zero cameras)
    districts_present = sorted(d for d in by_district if d != "Unknown")
    core_depts = ("POLICE", "HEALTH", "GSRTC", "PANCHAYAT", "MUNICIPAL")
    present_pairs = {(lookups.dept_sync(c.department_id).code if lookups.dept_sync(c.department_id) else "UNASSIGNED", c.district) for c in cams}
    department_gaps = []
    for code in core_depts:
        info = next((d for d in lookups._Cache.depts_by_id.values() if d.code == code), None)
        if info is None or (not scope.unrestricted and info.id != scope.department_id):
            continue
        for dname in districts_present:
            if (code, dname) not in present_pairs:
                department_gaps.append({"department_code": code, "department_name": info.name, "district": dname})

    # 4. uncovered POIs
    poi_rows = (
        await db.execute(
            text(
                f"""
                SELECT p.id, p.name, p.type, p.district, p.lat, p.lon,
                       n.id AS nearest_id, n.dist AS nearest_m
                FROM pois p
                LEFT JOIN LATERAL (
                    SELECT c.id, ST_Distance(c.geog, p.geog) AS dist
                    FROM cameras c
                    WHERE c.geog IS NOT NULL AND c.status <> 'retired' {scope_sql}
                    ORDER BY c.geog <-> p.geog LIMIT 1
                ) n ON true
                WHERE (n.dist IS NULL OR n.dist > :poi_radius) {"AND p.district = :district" if district else ""}
                ORDER BY p.district, p.name
                """
            ),
            {"poi_radius": poi_radius, **scope_params, **dparams},
        )
    ).all()
    uncovered_pois = [
        {"id": r.id, "name": r.name, "type": r.type, "district": r.district, "lat": r.lat, "lon": r.lon, "nearest_camera_id": r.nearest_id, "nearest_camera_m": round(float(r.nearest_m), 1) if r.nearest_m is not None else None}
        for r in poi_rows
    ]
    for u in uncovered_pois:
        if u["district"] in by_district:
            by_district[u["district"]]["uncovered_pois"] += 1

    # 3. zero-coverage grid (districts that have cameras only; capped)
    zero_features, zero_counts, truncated = await _zero_coverage(db, scope_sql, scope_params, districts_present if not district else [district], radius, grid)
    for dname, n in zero_counts.items():
        if dname in by_district:
            by_district[dname]["zero_coverage_cells"] = n

    # 6. offline hotspots (offline > 24 h grouped by police station)
    hotspots: dict[tuple, dict[str, Any]] = {}
    cutoff = utcnow() - timedelta(hours=24)
    for c in cams:
        if c.status == "offline" and c.last_status_change_at and c.last_status_change_at <= cutoff:
            h = hotspots.setdefault((c.district, c.police_station), {"district": c.district, "police_station": c.police_station, "count": 0, "camera_ids": []})
            h["count"] += 1
            h["camera_ids"].append(c.id)
    offline_hotspots = sorted(hotspots.values(), key=lambda h: -h["count"])

    # 7. metadata gaps
    metadata_gaps = []
    for c in cams:
        missing = []
        if c.lat is None or c.lon is None:
            missing.append("lat/lon")
        if not c.rtsp_url:
            missing.append("rtsp_url")
        if c.retention_days is None:
            missing.append("retention_days")
        if lookups.dept_sync(c.department_id) is None or lookups.dept_sync(c.department_id).code == "UNASSIGNED":
            missing.append("department")
        if c.install_date is None:
            missing.append("install_date")
        if not c.district:
            missing.append("district")
        if missing:
            metadata_gaps.append({"camera_id": c.id, "name": c.name, "district": c.district, "missing": missing})
            if c.district in by_district:
                by_district[c.district]["metadata_gaps"] += 1

    # 7b. ageing infrastructure
    near_poi_ids = await _cameras_near_poi(db, scope_sql, scope_params, poi_radius)
    today = utcnow().date()
    ageing = []
    for c in cams:
        reasons = []
        age = serializers.age_years(c.install_date)
        amc = serializers.amc_status(c.amc_expiry, today)
        over = max(0.0, (age or 0) - ageing_years) if age is not None else 0.0
        if age is not None and age > ageing_years:
            reasons.append(f"older than {ageing_years} years")
        if c.type == "analog":
            reasons.append("analog")
        if amc == "expired":
            reasons.append("AMC expired")
        elif amc == "expiring":
            reasons.append("AMC expiring within 30 days")
        if c.maintenance_status in ("faulty", "under_maintenance") and c.last_maintenance_at and (utcnow() - c.last_maintenance_at) > timedelta(days=7):
            reasons.append(f"{c.maintenance_status.replace('_', ' ')} for more than 7 days")
        elif c.maintenance_status == "faulty":
            reasons.append("faulty")
        off = offline_pct.get(c.id, 0.0)
        if off >= 50:
            reasons.append(f"offline {off:.0f}% of 24 h")
        if not reasons:
            continue
        score = min(100.0, 10 * over + 30 * (c.type == "analog") + 25 * (amc == "expired") + 15 * (amc == "expiring") + 0.2 * off + 10 * (c.id in near_poi_ids))
        ageing.append(
            {
                "camera_id": c.id,
                "name": c.name,
                "department_code": lookups.dept_sync(c.department_id).code if lookups.dept_sync(c.department_id) else None,
                "district": c.district,
                "type": c.type,
                "install_date": c.install_date.isoformat() if c.install_date else None,
                "age_years": age,
                "amc_expiry": c.amc_expiry.isoformat() if c.amc_expiry else None,
                "amc_status": amc,
                "maintenance_status": c.maintenance_status,
                "offline_pct_24h": round(off, 1),
                "near_poi": c.id in near_poi_ids,
                "priority_score": round(score, 1),
                "reasons": reasons,
            }
        )
        if c.district in by_district:
            by_district[c.district]["ageing"] += 1
    ageing.sort(key=lambda a: -a["priority_score"])

    # 8. recommendations
    recommendations = []
    for dname in sorted(by_district):
        d = by_district[dname]
        parts = []
        pois_here = [u for u in uncovered_pois if u["district"] == dname]
        if pois_here:
            names = ", ".join(u["name"] for u in pois_here[:3])
            parts.append(f"Add {len(pois_here)} camera{'s' if len(pois_here) != 1 else ''} at uncovered POIs ({names}{'…' if len(pois_here) > 3 else ''})")
        old_analog = [a for a in ageing if a["district"] == dname and ("analog" in a["reasons"] or any(r.startswith("older") for r in a["reasons"]))]
        if old_analog:
            names = ", ".join(a["name"] for a in old_analog[:2])
            parts.append(f"replace {len(old_analog)} ageing/analog camera{'s' if len(old_analog) != 1 else ''} ({names})")
        amc_bad = [a for a in ageing if a["district"] == dname and a["amc_status"] in ("expired", "expiring")]
        if amc_bad:
            parts.append(f"renew AMC on {len(amc_bad)} camera{'s' if len(amc_bad) != 1 else ''}")
        if d["zero_coverage_cells"]:
            parts.append(f"{d['zero_coverage_cells']} zero-coverage {grid} m cells remain")
        gaps_here = [g for g in department_gaps if g["district"] == dname]
        if gaps_here:
            parts.append(f"no cameras from {', '.join(g['department_code'] for g in gaps_here)}")
        if d["total"] and d["online"] / d["total"] < 0.5:
            parts.append(f"restore connectivity: only {d['online']}/{d['total']} cameras online")
        if parts:
            recommendations.append({"district": dname, "text": "; ".join(parts) + "."})

    district_list = []
    for dname in sorted(by_district):
        d = by_district[dname]
        d["online_pct"] = round(100.0 * d["online"] / d["total"], 1) if d["total"] else 0.0
        district_list.append(d)

    total_districts = int((await db.execute(select(func.count()).select_from(District))).scalar() or 0)
    online = sum(1 for c in cams if c.status == "online")
    summary = {
        "cameras_total": len(cams),
        "with_location": sum(1 for c in cams if c.lat is not None),
        "online_pct": round(100.0 * online / len(cams), 1) if cams else 0.0,
        "districts_with_cameras": len(districts_present),
        "districts_total": total_districts,
        "zero_coverage_cells": sum(zero_counts.values()),
        "uncovered_pois": len(uncovered_pois),
        "ageing_cameras": len(ageing),
        "metadata_gaps": len(metadata_gaps),
        "offline_hotspots": len(offline_hotspots),
        "department_gaps": len(department_gaps),
    }
    return {
        "generated_at": iso_z(utcnow()),
        "cached": False,
        "params": {"coverage_radius_m": radius, "poi_radius_m": poi_radius, "grid_m": grid, "ageing_years": ageing_years, "district": district},
        "summary": summary,
        "by_area": area_list,
        "by_district": district_list,
        "department_gaps": department_gaps,
        "uncovered_pois": uncovered_pois,
        "zero_coverage": {"type": "FeatureCollection", "features": zero_features},
        "zero_coverage_truncated": truncated,
        "offline_hotspots": offline_hotspots,
        "metadata_gaps": metadata_gaps,
        "ageing": ageing,
        "recommendations": recommendations,
    }


async def _offline_pct_24h(db: AsyncSession, camera_ids: list[int]) -> dict[int, float]:
    if not camera_ids:
        return {}
    since = utcnow() - timedelta(hours=24)
    rows = (
        await db.execute(
            select(CameraHealthLog.camera_id, func.count(), func.sum(func.cast(CameraHealthLog.is_ready, __import__("sqlalchemy").Integer)))
            .where(CameraHealthLog.checked_at >= since, CameraHealthLog.camera_id.in_(camera_ids))
            .group_by(CameraHealthLog.camera_id)
        )
    ).all()
    return {r[0]: round(100.0 * (1 - float(r[2] or 0) / float(r[1])), 1) for r in rows if r[1]}


async def _cameras_near_poi(db: AsyncSession, scope_sql: str, scope_params: dict, radius: int) -> set[int]:
    rows = (
        await db.execute(
            text(f"SELECT DISTINCT c.id FROM cameras c JOIN pois p ON ST_DWithin(c.geog, p.geog, :r) WHERE c.geog IS NOT NULL {scope_sql}"),
            {"r": radius, **scope_params},
        )
    ).all()
    return {r[0] for r in rows}


ZERO_COVERAGE_TIMEOUT_MS = 20_000

_ZERO_COVERAGE_CTE = """
    WITH d AS MATERIALIZED (
        SELECT name, ST_Transform(ST_SimplifyPreserveTopology(geom, 0.002), 3857) AS g
        FROM districts WHERE name = ANY(:districts)
    ),
    cov AS MATERIALIZED (
        SELECT ST_Union(ST_Transform(ST_Buffer(c.geog, :radius)::geometry, 3857)) AS g
        FROM cameras c WHERE c.geog IS NOT NULL AND c.status <> 'retired' {scope_sql}
    ),
    cells AS MATERIALIZED (
        SELECT d.name AS district, g.geom, g.i, g.j
        FROM d CROSS JOIN LATERAL ST_SquareGrid(:grid, d.g) AS g
        WHERE ST_Intersects(d.g, ST_Centroid(g.geom))
    ),
    zero AS (
        SELECT cells.district, cells.i, cells.j, cells.geom
        FROM cells, cov
        WHERE cov.g IS NULL OR NOT ST_Intersects(cells.geom, cov.g)
    )
"""


async def _zero_coverage(db: AsyncSession, scope_sql: str, scope_params: dict, districts: list[str], radius: int, grid: int) -> tuple[list[dict], dict[str, int], bool]:
    """Zero-coverage grid cells per district (§5.8). MATERIALIZED CTEs matter: an inlined CTE makes Postgres
    re-evaluate ST_Transform per cell and defeats the prepared-geometry cache (19 s vs 90 ms per district).
    One LATERAL grid pass, a count query and a capped
    feature query, bounded by a statement timeout so the endpoint can never hang on large districts."""
    if not districts:
        return [], {}, False
    params = {"districts": districts, "radius": radius, "grid": grid, **scope_params}
    cte = _ZERO_COVERAGE_CTE.format(scope_sql=scope_sql)
    count_sql = text(cte + " SELECT district, count(*) AS n FROM zero GROUP BY district")
    feat_sql = text(cte + " SELECT district, i, j, ST_AsGeoJSON(ST_Transform(geom, 4326), 5) AS gj FROM zero ORDER BY district, j, i LIMIT :cap")
    try:
        await db.execute(text(f"SET LOCAL statement_timeout = {ZERO_COVERAGE_TIMEOUT_MS}"))
        counts = {r.district: int(r.n) for r in (await db.execute(count_sql, params)).all()}
        rows = (await db.execute(feat_sql, {**params, "cap": MAX_CELLS})).all()
    except Exception as exc:  # noqa: BLE001 – timeout or PostGIS error: report empty rather than fail the whole report
        log.warning("zero-coverage grid skipped: %s", str(exc).splitlines()[0][:200])
        await db.rollback()
        return [], {}, False
    finally:
        try:
            await db.execute(text("SET LOCAL statement_timeout = 0"))
        except Exception:  # noqa: BLE001
            pass
    features = [
        {"type": "Feature", "geometry": json.loads(r.gj), "properties": {"district": r.district, "cell": f"r{r.j}c{r.i}"}}
        for r in rows
    ]
    return features, counts, sum(counts.values()) > len(features)


async def coverage_geojson(db: AsyncSession, scope: Scope, radius: int, district: str | None, include_offline: bool) -> dict[str, Any]:
    scope_sql, scope_params = _scope_sql(scope)
    status_sql = "c.status <> 'retired'" if include_offline else "c.status IN ('online','degraded','unknown')"
    dist_sql = " AND c.district = :district" if district else ""
    rows = (
        await db.execute(
            text(
                f"""
                SELECT c.district, count(*) AS n,
                       ST_AsGeoJSON(ST_Multi(ST_Union(ST_Buffer(c.geog, :radius)::geometry)), 6) AS gj
                FROM cameras c
                WHERE c.geog IS NOT NULL AND {status_sql} {scope_sql} {dist_sql}
                GROUP BY c.district ORDER BY c.district
                """
            ),
            {"radius": radius, **scope_params, **({"district": district} if district else {})},
        )
    ).all()
    feats = [
        {"type": "Feature", "geometry": json.loads(r.gj), "properties": {"district": r.district, "radius_m": radius, "camera_count": int(r.n)}}
        for r in rows
        if r.gj
    ]
    return {"type": "FeatureCollection", "features": feats}


def flatten_for_csv(data: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    """One CSV with a `section` column and the union of section columns (§5.8 export)."""
    sections = ("by_area", "by_district", "department_gaps", "uncovered_pois", "offline_hotspots", "metadata_gaps", "ageing", "recommendations")
    cols: list[str] = ["section"]
    rows_raw: list[tuple[str, dict[str, Any]]] = []
    for sec in sections:
        for item in data.get(sec, []):
            flat = {}
            for k, v in item.items():
                if isinstance(v, dict):
                    flat[k] = json.dumps(v, ensure_ascii=False)
                elif isinstance(v, list):
                    flat[k] = "; ".join(str(x) for x in v)
                else:
                    flat[k] = v
            for k in flat:
                if k not in cols:
                    cols.append(k)
            rows_raw.append((sec, flat))
    rows = [[sec] + [flat.get(c) for c in cols[1:]] for sec, flat in rows_raw]
    return cols, rows
