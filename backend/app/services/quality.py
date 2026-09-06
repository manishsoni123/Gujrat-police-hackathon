"""Analytics-quality figures (A8, CONTRACT §5.15 quality JSON) over reads, sightings and QA labels."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.hashing import media_url
from app.core.rbac import Scope
from app.core.tz import iso_z
from app.db.models import Camera, PlateRead, QaLabel, Sighting
from app.services.scope import in_scope_condition


def _pct(num: float, den: float) -> float | None:
    return round(100.0 * num / den, 1) if den else None


async def compute(db: AsyncSession, scope: Scope, t_from: datetime, t_to: datetime, camera_id: int | None, source: str | None = None) -> dict[str, Any]:
    """`source` (a `cameras.source` value, e.g. `sandbox`) restricts every figure to cameras of that registry
    source, so the government-feed quality can be reported without the own gate or mock rows."""
    cond = in_scope_condition(scope, PlateRead.camera_id)
    base = select(PlateRead).where(PlateRead.captured_at >= t_from, PlateRead.captured_at <= t_to)
    if cond is not None:
        base = base.where(cond)
    if camera_id:
        base = base.where(PlateRead.camera_id == camera_id)
    src_sub = select(Camera.id).where(Camera.source == source) if source else None
    if src_sub is not None:
        base = base.where(PlateRead.camera_id.in_(src_sub))
    sub = base.subquery()
    totals = (
        await db.execute(
            select(func.count(), func.sum(cast(sub.c.is_valid_format, Integer)), func.avg(sub.c.confidence)).select_from(sub)
        )
    ).one()
    reads_total = int(totals[0] or 0)
    reads_valid = int(totals[1] or 0)
    mean_conf = round(float(totals[2]), 3) if totals[2] is not None else None

    s_q = select(func.count(), func.count(func.distinct(Sighting.plate_norm))).where(Sighting.first_seen >= t_from, Sighting.first_seen <= t_to)
    s_cond = in_scope_condition(scope, Sighting.camera_id)
    if s_cond is not None:
        s_q = s_q.where(s_cond)
    if camera_id:
        s_q = s_q.where(Sighting.camera_id == camera_id)
    if src_sub is not None:
        s_q = s_q.where(Sighting.camera_id.in_(src_sub))
    s_total, unique_plates = (await db.execute(s_q)).one()

    per_cam_rows = (
        await db.execute(
            select(sub.c.camera_id, Camera.name, func.count(), func.sum(cast(sub.c.is_valid_format, Integer)), func.avg(sub.c.confidence))
            .join(Camera, Camera.id == sub.c.camera_id)
            .group_by(sub.c.camera_id, Camera.name)
            .order_by(func.count().desc())
        )
    ).all()
    reads_per_camera = [
        {"camera_id": r[0], "camera_name": r[1], "reads": int(r[2]), "valid_pct": _pct(float(r[3] or 0), float(r[2])), "mean_conf": round(float(r[4]), 3) if r[4] is not None else None}
        for r in per_cam_rows
    ]

    labels = (
        await db.execute(
            select(QaLabel, PlateRead, Camera.name)
            .join(PlateRead, PlateRead.id == QaLabel.read_id)
            .join(Camera, Camera.id == PlateRead.camera_id)
            .where(PlateRead.id.in_(select(sub.c.id)))
            .order_by(QaLabel.created_at.desc())
        )
    ).all()
    labelled = len(labels)
    exact = sum(1 for q, _r, _n in labels if q.is_match)
    total_chars = sum(max(len(q.true_plate), len(r.plate_norm)) for q, r, _n in labels)
    total_errors = sum(int(q.char_errors) for q, _r, _n in labels)
    per_cam: dict[int, dict[str, Any]] = {}
    confusions: Counter[tuple[str, str]] = Counter()
    for q, r, name in labels:
        pc = per_cam.setdefault(r.camera_id, {"camera_id": r.camera_id, "camera_name": name, "labelled": 0, "exact": 0, "chars": 0, "errors": 0})
        pc["labelled"] += 1
        pc["exact"] += 1 if q.is_match else 0
        pc["chars"] += max(len(q.true_plate), len(r.plate_norm))
        pc["errors"] += int(q.char_errors)
        if q.true_plate and len(q.true_plate) == len(r.plate_norm):
            for expected, got in zip(q.true_plate, r.plate_norm):
                if expected != got:
                    confusions[(expected, got)] += 1
    per_camera_accuracy = [
        {
            "camera_id": pc["camera_id"],
            "camera_name": pc["camera_name"],
            "labelled": pc["labelled"],
            "exact_pct": _pct(pc["exact"], pc["labelled"]),
            "char_accuracy_pct": _pct(pc["chars"] - pc["errors"], pc["chars"]),
        }
        for pc in sorted(per_cam.values(), key=lambda x: -x["labelled"])
    ]
    sample = [
        {"read_id": r.id, "camera_id": r.camera_id, "plate_norm": r.plate_norm, "true_plate": q.true_plate, "is_match": q.is_match, "char_errors": q.char_errors, "crop_url": media_url(r.crop_path)}
        for q, r, _n in labels[:60]
    ]
    return {
        "window": {"from": iso_z(t_from), "to": iso_z(t_to), "camera_id": camera_id, "source": source},
        "reads_total": reads_total,
        "reads_valid_format": reads_valid,
        "valid_format_pct": _pct(reads_valid, reads_total),
        "sightings_total": int(s_total or 0),
        "unique_plates": int(unique_plates or 0),
        "mean_confidence": mean_conf,
        "reads_per_camera": reads_per_camera,
        "labelled": labelled,
        "exact_match_pct": _pct(exact, labelled),
        "char_accuracy_pct": _pct(total_chars - total_errors, total_chars),
        "per_camera_accuracy": per_camera_accuracy,
        "confusions": [{"expected": e, "got": g, "count": n} for (e, g), n in confusions.most_common(20)],
        "sample": sample,
    }
