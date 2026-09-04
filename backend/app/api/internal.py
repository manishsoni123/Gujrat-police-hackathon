"""ANPR worker → API endpoints (CONTRACT §7). All require `X-API-Key` with scope `internal`; never audited."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from starlette.datastructures import UploadFile

from app.api.deps import DbDep, require_api_key
from app.core.config import settings
from app.core.errors import ApiError, bad_request, validation_error
from app.core.tz import iso_z, parse_iso, utcnow
from app.db.models import AnprWorker, Camera, Zone
from app.schemas.internal import DetectionBatch, EventsBatch, Heartbeat, ObjectCountsBody
from app.services import serializers
from app.services.mediamtx_client import relay_path, relay_rtsp_url
from app.services.sightings import create_worker_events, get_camera_for_ingest, ingest_batch, store_snapshot, upsert_object_counts
from app.services.ws_manager import manager

log = logging.getLogger("sentinel.internal")
router = APIRouter(tags=["internal"], dependencies=[Depends(require_api_key("internal"))])

MAX_BATCH_BYTES = 50 * 1024 * 1024


def _check_size(request: Request, limit: int = MAX_BATCH_BYTES) -> None:
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > limit:
        raise ApiError(413, f"request larger than {limit // (1024 * 1024)} MB")


async def _multipart(request: Request) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Return (payload JSON, {field name → bytes}) from a multipart body."""
    try:
        form = await request.form()
    except Exception as exc:  # noqa: BLE001
        raise bad_request(f"malformed multipart body: {exc.__class__.__name__}")
    raw = form.get("payload")
    if raw is None:
        raise bad_request("multipart field 'payload' is required")
    if isinstance(raw, UploadFile):
        raw = (await raw.read()).decode("utf-8", "ignore")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        raise bad_request("payload is not valid JSON")
    if not isinstance(payload, dict):
        raise bad_request("payload must be a JSON object")
    files: dict[str, bytes] = {}
    for key, val in form.multi_items():
        if isinstance(val, UploadFile):
            files[key] = await val.read()
    return payload, files


def _issues(exc: ValidationError) -> list[dict[str, Any]]:
    out = []
    for e in exc.errors():
        loc = ".".join(str(p) for p in e.get("loc", ()))
        msg = e.get("msg", "invalid")
        out.append({"field": loc, "message": msg[len("Value error, "):] if msg.startswith("Value error, ") else msg})
    return out


def _zone_out(z: Zone) -> dict[str, Any]:
    return {"id": z.id, "name": z.name, "polygon": z.polygon_json, "active_from": z.active_from.strftime("%H:%M") if z.active_from else None, "active_to": z.active_to.strftime("%H:%M") if z.active_to else None, "classes": list(z.classes or []), "dwell_s": z.dwell_s, "priority": z.priority}


@router.get("/internal/anpr-config")
async def anpr_config(db: DbDep, mode: str = Query("live", pattern="^(live|preindex)$")):
    stmt = select(Camera).where(Camera.status != "retired", Camera.rtsp_url.isnot(None))
    if mode == "live":
        stmt = stmt.where(Camera.anpr_enabled.is_(True))
    else:
        stmt = stmt.where((Camera.live.is_(None)) | (Camera.live.is_(True)))
    cams = (await db.execute(stmt.order_by(Camera.id))).scalars().all()
    zones = (await db.execute(select(Zone).where(Zone.is_active.is_(True), Zone.camera_id.in_([c.id for c in cams])))).scalars().all() if cams else []
    by_cam: dict[int, list[dict[str, Any]]] = {}
    for z in zones:
        by_cam.setdefault(z.camera_id, []).append(_zone_out(z))
    out = []
    for c in cams:
        cam_mode = "live" if mode == "live" else ("both" if c.anpr_enabled else "preindex")
        out.append({
            "id": c.id, "external_id": c.external_id, "name": c.name, "relay_path": relay_path(c.id), "rtsp_url": relay_rtsp_url(c.id),
            "codec": c.codec, "lat": c.lat, "lon": c.lon, "anpr_enabled": c.anpr_enabled, "record_enabled": c.record_enabled,
            "mode": cam_mode, "status": c.status, "zones": by_cam.get(c.id, []),
        })
    return {
        "generated_at": iso_z(utcnow()),
        "mode": mode,
        "settings": {
            "live_fps": settings.ANPR_FPS, "preindex_fps": settings.PREINDEX_FPS, "det_conf": settings.ANPR_DET_CONF, "min_plate_w": settings.ANPR_MIN_PLATE_W,
            "vote_window_s": settings.ANPR_VOTE_WINDOW_S, "sighting_close_s": settings.SIGHTING_CLOSE_S, "object_detect": settings.OBJECT_DETECT,
            "object_every_n": settings.OBJECT_EVERY_N, "snapshot_interval_s": settings.SNAPSHOT_INTERVAL_S,
        },
        "cameras": out,
    }


@router.post("/internal/detections")
async def detections(request: Request, db: DbDep):
    _check_size(request)
    payload, files = await _multipart(request)
    try:
        batch = DetectionBatch.model_validate(payload)
    except ValidationError as exc:
        raise validation_error("Invalid detection batch", _issues(exc))
    return await ingest_batch(db, batch, files)


@router.post("/internal/snapshots", status_code=204)
async def snapshots(request: Request, db: DbDep):
    _check_size(request, 2 * 1024 * 1024)
    try:
        form = await request.form()
    except Exception as exc:  # noqa: BLE001
        raise bad_request(f"malformed multipart body: {exc.__class__.__name__}")
    cam_raw = form.get("camera_id")
    file = form.get("file")
    if cam_raw is None or not str(cam_raw).isdigit():
        raise validation_error("camera_id required", [{"field": "camera_id", "message": "integer required"}])
    if not isinstance(file, UploadFile):
        raise validation_error("file required", [{"field": "file", "message": "JPEG file required"}])
    cam = await get_camera_for_ingest(db, int(cam_raw))
    await store_snapshot(cam, await file.read())
    return Response(status_code=204)


@router.post("/internal/object-counts")
async def object_counts(body: ObjectCountsBody, db: DbDep):
    cam = await get_camera_for_ingest(db, body.camera_id)
    rejected: list[dict[str, Any]] = []
    n = await upsert_object_counts(db, cam.id, body.object_counts, rejected)
    await db.commit()
    return {"object_counts_upserted": n, "rejected": rejected}


@router.post("/internal/heartbeat", status_code=204)
async def heartbeat(body: Heartbeat, db: DbDep):
    now = utcnow()
    cameras = [c.model_dump() for c in body.cameras]
    started = parse_iso(body.started_at) if body.started_at else None
    stmt = pg_insert(AnprWorker).values(id=body.worker_id[:64], mode=body.mode, version=body.version[:32], gpu=body.gpu, cameras=cameras, detector=(body.detector or None), started_at=started, last_heartbeat_at=now)
    stmt = stmt.on_conflict_do_update(index_elements=[AnprWorker.id], set_={"mode": body.mode, "version": body.version[:32], "gpu": body.gpu, "cameras": cameras, "detector": body.detector or None, "started_at": started, "last_heartbeat_at": now})
    await db.execute(stmt)
    await db.commit()
    manager.broadcast(
        "health", "anpr_status",
        {"worker_id": body.worker_id, "mode": body.mode, "gpu": body.gpu, "detector": body.detector, "cameras": [{"id": c["id"], "state": c["state"], "fps_actual": c.get("fps_actual")} for c in cameras], "last_heartbeat_at": iso_z(now), "stale": False},
        scoped=False,
    )
    return Response(status_code=204)


@router.post("/internal/events")
async def events(request: Request, db: DbDep):
    _check_size(request)
    payload, files = await _multipart(request)
    try:
        batch = EventsBatch.model_validate(payload)
    except ValidationError as exc:
        raise validation_error("Invalid events batch", _issues(exc))
    cam = await get_camera_for_ingest(db, batch.camera_id)
    await serializers.warm(db)
    rejected: list[dict[str, Any]] = []
    n = await create_worker_events(db, cam, batch.events, files, rejected)
    await db.commit()
    return {"events_created": n, "rejected": rejected}
