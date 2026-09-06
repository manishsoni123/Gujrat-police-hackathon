"""Evidence clips (V4/S7, CONTRACT §5.16): download a playback range to /data/clips with its SHA-256."""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.api.recordings import _segments
from app.core.errors import ApiError, conflict, not_found, validation_error
from app.core.hashing import abs_path, write_stream_hashed
from app.core.tz import parse_iso, utcnow
from app.db.models import Alert, Camera, Clip, Sighting
from app.schemas.analytics import ClipCreate
from app.schemas.common import PageParams
from app.services import serializers
from app.services.audit import set_audit
from app.services.mediamtx_client import play_path, playback_download, playback_list
from app.services.scope import in_scope_condition, scoped_camera

log = logging.getLogger("sentinel.clips")
router = APIRouter(tags=["clips"])

SORTS: dict[str, Any] = {"created_at": Clip.created_at, "start_at": Clip.start_at}


async def _collect(gen):
    async for chunk in gen:
        yield chunk


@router.post("/clips", status_code=201, dependencies=[Depends(require_permission("events.write"))])
async def create_clip(body: ClipCreate, user: CurrentUser, db: DbDep, request: Request):
    cam = await scoped_camera(db, user_scope(user), body.camera_id, include_retired=False)
    if cam is None:
        raise not_found("Camera not found")
    try:
        start = parse_iso(body.start_at)
    except ValueError:
        start = None
    if start is None:
        raise validation_error("Invalid start_at", [{"field": "start_at", "message": "must be ISO-8601"}])
    if body.alert_id is not None:
        a = await db.get(Alert, body.alert_id)
        if a is None or a.camera_id != cam.id:
            raise validation_error("Unknown alert", [{"field": "alert_id", "message": "alert not found on this camera"}])
    if body.sighting_id is not None:
        s = await db.get(Sighting, body.sighting_id)
        if s is None or s.camera_id != cam.id:
            raise validation_error("Unknown sighting", [{"field": "sighting_id", "message": "sighting not found on this camera"}])
    path = play_path(cam.id, cam.codec, cam.meta) if cam.rtsp_url else None
    if not path or not cam.record_enabled:
        raise conflict("No recording is available for this camera (recording is not enabled)")
    segs = _segments(await playback_list(path, start - timedelta(minutes=2), start + timedelta(seconds=body.duration_s)), path)
    # 1 s tolerance: MediaMTX reports segment starts with microseconds while the API renders (and clients echo)
    # millisecond precision, so a clip requested at the exact segment start must still be accepted.
    covering = next((s for s in segs if s["_start"] - timedelta(seconds=1) <= start < s["_start"] + timedelta(seconds=s["_dur"])), None)
    if covering is None:
        raise conflict("No recording covers the requested start time")
    if start < covering["_start"]:
        start = covering["_start"]  # requested at the (rounded) segment boundary: clamp to what is recorded

    # download the range through the playback server (no ffmpeg dependency on the API path)
    chunks: list[bytes] = []
    try:
        async for chunk in playback_download(path, start, body.duration_s):
            chunks.append(chunk)
    except RuntimeError as exc:
        raise ApiError(502, f"MediaMTX playback: {exc}")
    data = b"".join(chunks)
    if not data:
        raise conflict("Playback returned an empty file for this range")

    clip = Clip(camera_id=cam.id, alert_id=body.alert_id, sighting_id=body.sighting_id, start_at=start, duration_s=body.duration_s, path="pending", sha256="0" * 64, size_bytes=0, created_by=user.id, created_at=utcnow())
    db.add(clip)
    await db.flush()
    rel = f"clips/{cam.id}/{clip.id}.mp4"
    sha, size = write_stream_hashed(rel, iter([data]))
    clip.path, clip.sha256, clip.size_bytes = rel, sha, size
    await db.commit()
    await db.refresh(clip)
    await serializers.warm(db)
    item = serializers.clip_item(clip, cam)
    set_audit(request, entity="clip", entity_id=clip.id, after={"camera_id": cam.id, "start_at": item["start_at"], "duration_s": clip.duration_s, "sha256": sha, "size_bytes": size, "path": rel})
    return item


@router.get("/clips", dependencies=[Depends(require_permission("analytics.read"))])
async def list_clips(user: CurrentUser, db: DbDep, page: PageParams = Depends(), camera_id: int | None = None, alert_id: int | None = None, sighting_id: int | None = None):
    sort, order = page.resolve(SORTS, "created_at", "desc")
    await serializers.warm(db)
    stmt = select(Clip, Camera).join(Camera, Camera.id == Clip.camera_id)
    cond = in_scope_condition(user_scope(user), Clip.camera_id)
    if cond is not None:
        stmt = stmt.where(cond)
    if camera_id:
        stmt = stmt.where(Clip.camera_id == camera_id)
    if alert_id:
        stmt = stmt.where(Clip.alert_id == alert_id)
    if sighting_id:
        stmt = stmt.where(Clip.sighting_id == sighting_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    col = SORTS[sort]
    rows = (await db.execute(stmt.order_by(col.desc() if order == "desc" else col.asc(), Clip.id.desc()).offset(page.offset).limit(page.page_size))).all()
    return {"items": [serializers.clip_item(c, cam) for c, cam in rows], "total": int(total), "page": page.page, "page_size": page.page_size}


@router.get("/clips/{clip_id}", dependencies=[Depends(require_permission("analytics.read"))])
async def get_clip(clip_id: int, user: CurrentUser, db: DbDep):
    row = (await db.execute(select(Clip, Camera).join(Camera, Camera.id == Clip.camera_id).where(Clip.id == clip_id))).first()
    if row is None:
        raise not_found("Clip not found")
    clip, cam = row
    if not user_scope(user).allows(cam.department_id, cam.district):
        raise not_found("Clip not found")
    await serializers.warm(db)
    item = serializers.clip_item(clip, cam)
    item["exists"] = os.path.isfile(abs_path(clip.path))
    return item
