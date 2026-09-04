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


async def exact_sightings(
    db: AsyncSession, plate_norm: str, t_from: datetime, t_to: datetime, scope: Scope,
    camera_id: int | None = None, department_id: int | None = None, limit: int = 200,
) -> list[tuple[Sighting, Camera]]:
    q = (
        select(Sighting, Camera)
        .join(Camera, Camera.id == Sighting.camera_id)
        .where(Sighting.plate_norm == plate_norm, Sighting.first_seen >= t_from, Sighting.first_seen <= t_to)
        .order_by(Sighting.first_seen.asc())
        .limit(limit)
    )
    cond = in_scope_condition(scope, Sighting.camera_id)
    if cond is not None:
        q = q.where(cond)
    if camera_id:
        q = q.where(Sighting.camera_id == camera_id)
    if department_id:
        q = q.where(Camera.department_id == department_id)
    return [(s, c) for s, c in (await db.execute(q)).all()]


async def fuzzy_sightings(
    db: AsyncSession, plate_norm: str, t_from: datetime, t_to: datetime, scope: Scope,
    exclude_ids: set[int], camera_id: int | None = None, department_id: int | None = None, limit: int = 200,
) -> list[dict[str, Any]]:
    """Reads with levenshtein ≤ 2 or trigram similarity > 0.6, grouped by sighting, scored."""
    dist = func.levenshtein(PlateRead.plate_norm, plate_norm)
    sim = func.similarity(PlateRead.plate_raw, plate_norm)
    q = (
        select(
            PlateRead.sighting_id.label("sid"),
            func.min(dist).label("distance"),
            func.max(sim).label("similarity"),
            func.max(PlateRead.confidence).label("best_conf"),
        )
        .where(
            PlateRead.captured_at >= t_from,
            PlateRead.captured_at <= t_to,
            PlateRead.sighting_id.isnot(None),
            PlateRead.plate_norm != plate_norm,
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
    exact = await exact_sightings(db, plate_norm, t_from, t_to, scope, camera_id, department_id, limit)
    exact_items = []
    for s, cam in exact:
        item = serializers.sighting_item(s, cam)
        item.update({"match": "exact", "score": 1.0, "confirmation": None})
        exact_items.append(item)
    fuzzy_items = await fuzzy_sightings(db, plate_norm, t_from, t_to, scope, {s.id for s, _ in exact}, camera_id, department_id, limit)
    conf = await confirmations_for(db, plate_norm, [f["id"] for f in fuzzy_items] + [e["id"] for e in exact_items])
    for it in exact_items + fuzzy_items:
        it["confirmation"] = conf.get(it["id"])
    cams = {it["camera"]["id"] for it in exact_items} | {it["camera"]["id"] for it in fuzzy_items if it.get("confirmation") == "confirmed"}
    return {"exact": exact_items, "fuzzy": fuzzy_items, "cameras_seen": len(cams)}


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
