"""Recorded viewing (V4, CONTRACT §5.16): segment list and "play at" resolution via the MediaMTX playback server."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.core.errors import not_found, validation_error
from app.core.tz import iso_z, parse_iso, utcnow
from app.db.models import Camera
from app.schemas.common import parse_window
from app.services.audit import set_audit
from app.services.mediamtx_client import play_path, playback_list, public_playback_url
from app.services.scope import scoped_camera

router = APIRouter(tags=["recordings"], dependencies=[Depends(require_permission("cameras.read"))])

RETENTION_H = 12
# A start this far before a recorded timespan still counts as that timespan (CONTRACT Amendments 2026-09-05):
# MediaMTX lists starts with microseconds, the API renders (and clients echo) milliseconds.
BOUNDARY_TOLERANCE = timedelta(seconds=1)


def covering_segment(segs: list[dict[str, Any]], start: datetime) -> tuple[dict[str, Any] | None, datetime]:
    """`(timespan covering start, start to request)` over `_segments` rows (sorted by start). Pure.

    A start inside a timespan is returned unchanged; one up to `BOUNDARY_TOLERANCE` before a timespan (e.g. its
    millisecond rendering `.355` echoed back for a recorded `.355768`) is clamped to the exact recorded start.
    `(None, start)` when nothing covers it. The playback URL then rounds the start up to the millisecond
    (`mediamtx_client._rfc3339`), so the request never precedes the timespan (MediaMTX answers 404 if it does)."""
    for seg in segs:
        seg_start = seg["_start"]
        if seg_start - BOUNDARY_TOLERANCE <= start < seg_start + timedelta(seconds=seg["_dur"]):
            return seg, max(start, seg_start)
    return None, start


def _segments(raw: list[dict[str, Any]] | None, path: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for seg in raw or []:
        try:
            start = parse_iso(seg.get("start"))
            dur = float(seg.get("duration") or 0)
        except (ValueError, TypeError):
            continue
        if start is None or dur <= 0:
            continue
        out.append({"start": iso_z(start), "duration_s": round(dur, 3), "end": iso_z(start + timedelta(seconds=dur)), "url": public_playback_url(path, start, dur), "_start": start, "_dur": dur})
    out.sort(key=lambda s: s["_start"])
    return out


def _public(segs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in s.items() if not k.startswith("_")} for s in segs]


async def _camera(db, user, camera_id: int) -> Camera:
    cam = await scoped_camera(db, user_scope(user), camera_id, include_retired=False)
    if cam is None:
        raise not_found("Camera not found")
    return cam


@router.get("/recordings/{camera_id}")
async def list_recordings(camera_id: int, user: CurrentUser, db: DbDep, from_: str | None = Query(None, alias="from"), to: str | None = None):
    cam = await _camera(db, user, camera_id)
    t_from, t_to = parse_window(from_, to, RETENTION_H)
    path = play_path(cam.id, cam.codec, cam.meta) if cam.rtsp_url else None
    segs: list[dict[str, Any]] = []
    if path and cam.record_enabled:
        segs = _segments(await playback_list(path, t_from, t_to), path)
    return {"camera_id": cam.id, "playback_path": path, "record_enabled": cam.record_enabled, "retention_h": RETENTION_H, "window": {"from": iso_z(t_from), "to": iso_z(t_to)}, "segments": _public(segs)}


async def resolve_play(cam: Camera, at: datetime, before_s: int, duration_s: int) -> dict[str, Any]:
    """Find the segment containing `at - before_s`; returns the §5.16 play descriptor."""
    path = play_path(cam.id, cam.codec, cam.meta) if cam.rtsp_url else None
    start = at - timedelta(seconds=before_s)
    if not path or not cam.record_enabled:
        return {"url": None, "start": iso_z(start), "duration_s": duration_s, "available": False, "playback_path": path}
    segs = _segments(await playback_list(path, start - timedelta(minutes=5), start + timedelta(seconds=duration_s)), path)
    covering, start = covering_segment(segs, start)  # a boundary request is clamped to the recorded start
    if covering is None:
        later = [s for s in segs if s["_start"] >= start and s["_start"] <= start + timedelta(seconds=duration_s)]
        if not later:
            return {"url": None, "start": iso_z(start), "duration_s": duration_s, "available": False, "playback_path": path}
        start = later[0]["_start"]
    return {"url": public_playback_url(path, start, duration_s), "start": iso_z(start), "duration_s": duration_s, "available": True, "playback_path": path}


@router.get("/recordings/{camera_id}/play")
async def play(camera_id: int, user: CurrentUser, db: DbDep, request: Request, at: str = Query(...), before_s: int = Query(10, ge=0, le=600), duration_s: int = Query(30, ge=1, le=600)):
    cam = await _camera(db, user, camera_id)
    try:
        at_dt = parse_iso(at)
    except ValueError:
        at_dt = None
    if at_dt is None:
        raise validation_error("Invalid at", [{"field": "at", "message": "must be ISO-8601"}])
    if at_dt > utcnow() + timedelta(minutes=1):
        raise validation_error("Invalid at", [{"field": "at", "message": "is in the future"}])
    result = await resolve_play(cam, at_dt, before_s, duration_s)
    set_audit(request, action="recording.play", entity="camera", entity_id=cam.id, after={"at": iso_z(at_dt), "before_s": before_s, "duration_s": duration_s, "available": result["available"]})
    return result
