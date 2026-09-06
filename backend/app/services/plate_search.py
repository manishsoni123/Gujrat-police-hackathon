"""Exact + fuzzy plate search over sightings/plate_reads (CONTRACT §5.10) and route assembly."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.hashing import media_url
from app.core.rbac import Scope
from app.db.models import Camera, Event, PlateRead, RouteConfirmation, Sighting
from app.services import serializers, settings_service as cfg
from app.services.route_builder import RoutePoint, build_route
from app.services.scope import in_scope_condition


def _exact_filters(plate_norm: str, t_from: datetime, t_to: datetime, scope: Scope, camera_id: int | None, department_id: int | None) -> list:
    conds = [Sighting.plate_norm == plate_norm, Sighting.first_seen >= t_from, Sighting.first_seen <= t_to]
    cond = in_scope_condition(scope, Sighting.camera_id)
    if cond is not None:
        conds.append(cond)
    if camera_id:
        conds.append(Sighting.camera_id == camera_id)
    if department_id:
        conds.append(Camera.department_id == department_id)
    return conds


async def exact_sightings(
    db: AsyncSession, plate_norm: str, t_from: datetime, t_to: datetime, scope: Scope,
    camera_id: int | None = None, department_id: int | None = None, limit: int = 200, order: str = "asc",
) -> list[tuple[Sighting, Camera]]:
    """Exact sightings in the window. `order='desc'` (search) shows the most recent first; the route keeps `asc`."""
    q = (
        select(Sighting, Camera)
        .join(Camera, Camera.id == Sighting.camera_id)
        .where(*_exact_filters(plate_norm, t_from, t_to, scope, camera_id, department_id))
        .order_by(Sighting.first_seen.desc() if order == "desc" else Sighting.first_seen.asc(), Sighting.id.asc())
        .limit(limit)
    )
    return [(s, c) for s, c in (await db.execute(q)).all()]


async def exact_totals(
    db: AsyncSession, plate_norm: str, t_from: datetime, t_to: datetime, scope: Scope,
    camera_id: int | None = None, department_id: int | None = None,
) -> tuple[int, set[int]]:
    """(total exact sightings, distinct camera ids) over the **uncapped** window."""
    q = (
        select(Sighting.camera_id, func.count())
        .select_from(Sighting)
        .join(Camera, Camera.id == Sighting.camera_id)
        .where(*_exact_filters(plate_norm, t_from, t_to, scope, camera_id, department_id))
        .group_by(Sighting.camera_id)
    )
    rows = (await db.execute(q)).all()
    return sum(int(n) for _cid, n in rows), {int(cid) for cid, _n in rows}


async def fuzzy_sightings(
    db: AsyncSession, plate_norm: str, t_from: datetime, t_to: datetime, scope: Scope,
    exclude_ids: set[int], camera_id: int | None = None, department_id: int | None = None, limit: int = 200,
    total_out: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Reads with levenshtein ≤ 2 or trigram similarity > 0.6, grouped by sighting, scored.

    Sightings whose own `plate_norm` equals the query are never fuzzy candidates (they are the
    exact set, capped or not); `total_out['fuzzy_total']` receives the uncapped candidate count.
    """
    dist = func.levenshtein(PlateRead.plate_norm, plate_norm)
    sim = func.similarity(PlateRead.plate_raw, plate_norm)
    q = (
        select(
            PlateRead.sighting_id.label("sid"),
            func.min(dist).label("distance"),
            func.max(sim).label("similarity"),
            func.max(PlateRead.confidence).label("best_conf"),
        )
        .join(Sighting, Sighting.id == PlateRead.sighting_id)
        .where(
            PlateRead.captured_at >= t_from,
            PlateRead.captured_at <= t_to,
            PlateRead.plate_norm != plate_norm,
            Sighting.plate_norm != plate_norm,
            (dist <= 2) | (sim > 0.6),
        )
        .group_by(PlateRead.sighting_id)
    )
    cond = in_scope_condition(scope, PlateRead.camera_id)
    if cond is not None:
        q = q.where(cond)
    if camera_id:
        q = q.where(PlateRead.camera_id == camera_id)
    if department_id:
        q = q.where(PlateRead.camera_id.in_(select(Camera.id).where(Camera.department_id == department_id)))
    rows = (await db.execute(q)).all()
    cands = []
    for r in rows:
        if r.sid in exclude_ids:
            continue
        distance = int(r.distance) if r.distance is not None else 3
        score = (1 - min(distance, 3) / 3) * 0.7 + float(r.best_conf or 0) * 0.3
        cands.append({"sighting_id": r.sid, "distance": distance, "similarity": round(float(r.similarity or 0), 3), "score": round(score, 3)})
    cands.sort(key=lambda c: (-c["score"], c["sighting_id"]))
    if total_out is not None:
        total_out["fuzzy_total"] = len(cands)
    cands = cands[:limit]
    if not cands:
        return []
    ids = [c["sighting_id"] for c in cands]
    srows = (await db.execute(select(Sighting, Camera).join(Camera, Camera.id == Sighting.camera_id).where(Sighting.id.in_(ids)))).all()
    by_id = {s.id: (s, c) for s, c in srows}
    # sample reads: the 3 highest-confidence reads of *each* candidate sighting (window function, so
    # one sighting with many strong reads cannot starve the others of their crops)
    rank = func.row_number().over(partition_by=PlateRead.sighting_id, order_by=(PlateRead.confidence.desc(), PlateRead.id.asc())).label("rn")
    ranked = select(PlateRead.id.label("rid"), rank).where(PlateRead.sighting_id.in_(ids)).subquery()
    reads = (
        await db.execute(
            select(PlateRead).join(ranked, ranked.c.rid == PlateRead.id).where(ranked.c.rn <= 3).order_by(PlateRead.sighting_id, PlateRead.confidence.desc())
        )
    ).scalars().all()
    samples: dict[int, list[dict[str, Any]]] = {}
    for rd in reads:
        samples.setdefault(rd.sighting_id, []).append(
            {"id": rd.id, "plate_raw": rd.plate_raw, "confidence": round(float(rd.confidence), 3), "crop_url": media_url(rd.crop_path)}
        )
    out = []
    for c in cands:
        pair = by_id.get(c["sighting_id"])
        if not pair:
            continue
        s, cam = pair
        item = serializers.sighting_item(s, cam)
        item.update({"match": "fuzzy", "distance": c["distance"], "similarity": c["similarity"], "score": c["score"], "sample_reads": samples.get(s.id, [])})
        out.append(item)
    return out


async def confirmations_for(db: AsyncSession, plate_norm: str, sighting_ids: list[int]) -> dict[int, str]:
    if not sighting_ids:
        return {}
    rows = (
        await db.execute(
            select(RouteConfirmation.sighting_id, RouteConfirmation.decision).where(
                RouteConfirmation.query_plate == plate_norm, RouteConfirmation.sighting_id.in_(sighting_ids)
            )
        )
    ).all()
    return {r[0]: r[1] for r in rows}


async def search(
    db: AsyncSession, plate_norm: str, t_from: datetime, t_to: datetime, scope: Scope,
    camera_id: int | None, department_id: int | None, limit: int,
) -> dict[str, Any]:
    # most recent exact sightings first (the operator wants the latest, not the 50 oldest of 1 100)
    exact = await exact_sightings(db, plate_norm, t_from, t_to, scope, camera_id, department_id, limit, order="desc")
    exact_total, exact_cams = await exact_totals(db, plate_norm, t_from, t_to, scope, camera_id, department_id)
    exact_items = []
    for s, cam in exact:
        item = serializers.sighting_item(s, cam)
        item.update({"match": "exact", "score": 1.0, "confirmation": None})
        exact_items.append(item)
    totals: dict[str, int] = {}
    fuzzy_items = await fuzzy_sightings(db, plate_norm, t_from, t_to, scope, {s.id for s, _ in exact}, camera_id, department_id, limit, total_out=totals)
    conf = await confirmations_for(db, plate_norm, [f["id"] for f in fuzzy_items] + [e["id"] for e in exact_items])
    for it in exact_items + fuzzy_items:
        it["confirmation"] = conf.get(it["id"])
    # cameras_seen: every camera of the uncapped exact set plus cameras of confirmed fuzzy candidates
    cams = exact_cams | {it["camera"]["id"] for it in fuzzy_items if it.get("confirmation") == "confirmed"}
    return {"exact": exact_items, "fuzzy": fuzzy_items, "cameras_seen": len(cams), "exact_total": exact_total, "fuzzy_total": totals.get("fuzzy_total", len(fuzzy_items)), "limit": limit}


async def route(
    db: AsyncSession, plate_norm: str, t_from: datetime, t_to: datetime, scope: Scope, include: str, max_gap_h: float,
) -> dict[str, Any]:
    exact = await exact_sightings(db, plate_norm, t_from, t_to, scope, limit=1000)
    exact_ids = {s.id for s, _ in exact}
    fuzzy = await fuzzy_sightings(db, plate_norm, t_from, t_to, scope, exact_ids, limit=500)
    conf = await confirmations_for(db, plate_norm, list(exact_ids) + [f["id"] for f in fuzzy])
    points: list[RoutePoint] = []
    for s, cam in exact:
        if conf.get(s.id) == "rejected":
            continue
        points.append(_point(s, cam, "exact", conf.get(s.id)))
    fuzzy_by_id = {f["id"]: f for f in fuzzy}
    if fuzzy_by_id:
        srows = (await db.execute(select(Sighting, Camera).join(Camera, Camera.id == Sighting.camera_id).where(Sighting.id.in_(list(fuzzy_by_id))))).all()
        for s, cam in srows:
            decision = conf.get(s.id)
            if decision == "rejected":
                continue
            if include == "all" or decision == "confirmed":
                points.append(_point(s, cam, "fuzzy", decision))
    built = build_route(points, cfg.get_float("route.speed_flag_kmh"), max_gap_h)
    loops = (
        await db.execute(
            select(func.count()).select_from(Event).where(Event.type == "loop_reset", Event.occurred_at >= t_from, Event.occurred_at <= t_to)
        )
    ).scalar() or 0
    built["loop_resets_in_window"] = int(loops)
    return built


def _point(s: Sighting, cam: Camera, match: str, decision: str | None) -> RoutePoint:
    return RoutePoint(
        sighting_id=s.id,
        camera_id=cam.id,
        camera_name=cam.name,
        first_seen=s.first_seen,
        last_seen=s.last_seen,
        read_count=s.read_count,
        best_conf=float(s.best_conf),
        lat=cam.lat,
        lon=cam.lon,
        match=match,
        confirmation=decision,
        crop_url=media_url(s.best_crop_path),
        frame_url=media_url(s.frame_path),
        camera=serializers.camera_summary(cam),
        recording_available=serializers.recording_available(cam, s.first_seen),
    )
