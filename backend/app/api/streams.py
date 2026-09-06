"""Stream descriptor for the player (CONTRACT §5.4)."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.core.config import settings
from app.core.errors import not_found
from app.core.hashing import abs_path, media_url
from app.core.tz import iso_z, utcnow
from app.services.audit import set_audit
from app.services.mediamtx_client import client as mtx
from app.services.mediamtx_client import play_path, relay_path, whep_supported
from app.services.scope import scoped_camera

router = APIRouter(tags=["streams"], dependencies=[Depends(require_permission("cameras.read"))])


@router.get("/streams/{camera_id}")
async def get_stream(camera_id: int, user: CurrentUser, db: DbDep, request: Request):
    cam = await scoped_camera(db, user_scope(user), camera_id, include_retired=False)
    if cam is None:
        raise not_found("Camera not found")
    pp = play_path(cam.id, cam.codec, cam.meta) if cam.rtsp_url else None
    snap_rel = f"snapshots/cam_{cam.id}.jpg"
    snap = abs_path(snap_rel)
    snapshot_url = snapshot_updated = None
    stale = True
    if snap.exists():
        mtime = datetime.fromtimestamp(os.path.getmtime(snap), tz=timezone.utc)
        snapshot_url = media_url(snap_rel)
        snapshot_updated = iso_z(mtime)
        stale = (utcnow() - mtime).total_seconds() > settings.SNAPSHOT_STALE_SECONDS
    ready = readers = None
    if pp:
        item = await mtx.get_path(pp)
        if item is not None:
            ready = bool(item.get("ready"))
            readers = len(item.get("readers") or [])
    whep_ok = whep_supported(cam.codec, cam.meta)
    set_audit(request, action="stream.view", entity="camera", entity_id=cam.id, after={"play_path": pp})
    return {
        # Browser start mode: WebRTC unless the relay cannot serve this stream over WebRTC (B-frame H.264), then HLS.
        "whep_supported": whep_ok,
        # True when the play path is the relay's libx264 re-encode (H.265 source, or H.264 with B-frames)
        "transcoded": bool(pp) and pp != relay_path(cam.id),
        "preferred_mode": "whep" if whep_ok else "hls",
        "camera_id": cam.id,
        "name": cam.name,
        "codec": cam.codec,
        "relay_path": relay_path(cam.id) if cam.rtsp_url else None,
        "play_path": pp,
        "whep_url": f"/mtx/{pp}/whep" if pp else None,
        "hls_url": f"/mtx/{pp}/index.m3u8" if pp else None,
        "snapshot_url": snapshot_url,
        "snapshot_updated_at": snapshot_updated,
        "snapshot_stale": stale,
        "ready": ready,
        "readers": readers,
        "record_enabled": cam.record_enabled,
        "playback_path": pp if cam.record_enabled else None,
        "status": cam.status,
    }
