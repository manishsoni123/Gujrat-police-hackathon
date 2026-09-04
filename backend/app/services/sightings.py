"""ANPR batch ingestion (CONTRACT §7.2–§7.6): sightings upsert, reads, crops, counts, events, alerts."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, not_found
from app.core.hashing import media_url, write_bytes_hashed
from app.core.tz import epoch_ms, iso_z, parse_iso, utc_date_dir, utcnow
from app.db.models import Alert, Camera, Event, ObjectCount, PlateRead, Sighting, Zone
from app.schemas.internal import DetectionBatch, EventIn, ObjectCountIn
from app.services import serializers
from app.services.matcher import matcher
from app.services.plates import normalise
from app.services.ws_manager import manager

log = logging.getLogger("sentinel.ingest")

MAX_CROP_BYTES = 200 * 1024
MAX_FRAME_BYTES = 400 * 1024
INTRUSION_SUPPRESSION_S = 120
ALL_CLASSES = ("person", "bicycle", "car", "motorcycle", "bus", "truck")


def _reads_channel(camera_id: int) -> str:
    return f"reads:{camera_id}"


def _validate_jpeg(data: bytes | None, max_bytes: int) -> str | None:
    if data is None:
        return "missing"
    if len(data) == 0:
        return "empty"
    if len(data) > max_bytes:
        return f"larger than {max_bytes // 1024} KB"
    if not (data[:3] == b"\xff\xd8\xff"):
        return "not a JPEG"
    return None


async def get_camera_for_ingest(db: AsyncSession, camera_id: int) -> Camera:
    cam = await db.get(Camera, camera_id)
    if cam is None or cam.status == "retired":
        raise not_found(f"camera {camera_id} unknown or retired")
    return cam


async def ingest_batch(db: AsyncSession, batch: DetectionBatch, files: dict[str, bytes]) -> dict[str, Any]:
    cam = await get_camera_for_ingest(db, batch.camera_id)
    await serializers.warm(db)
    await matcher.maybe_reload(db)
    rejected: list[dict[str, Any]] = []
    counters = {"accepted_reads": 0, "accepted_sightings": 0, "alerts_created": 0, "alerts_updated": 0, "object_counts_upserted": 0, "events_created": 0}
    now = utcnow()
    date_dir = utc_date_dir(now)

    # 1. sightings (before reads, keyed by worker_key)
    sightings_by_key: dict[str, Sighting] = {}
    changed_sightings: set[str] = set()
    keys = [s.key for s in batch.sightings] + [r.sighting_key for r in batch.reads]
    if keys:
        rows = (await db.execute(select(Sighting).where(Sighting.worker_key.in_(list(set(keys)))))).scalars().all()
        sightings_by_key = {s.worker_key: s for s in rows}

    for idx, sin in enumerate(batch.sightings):
        try:
            first_seen, last_seen = parse_iso(sin.first_seen), parse_iso(sin.last_seen)
        except ValueError:
            rejected.append({"index": idx, "kind": "sighting", "reason": "invalid timestamps"})
            continue
        if first_seen is None or last_seen is None:
            rejected.append({"index": idx, "kind": "sighting", "reason": "invalid timestamps"})
            continue
        norm = normalise(sin.plate_norm)
        plate_norm = norm.plate_norm or sin.plate_norm.upper()
        is_valid = norm.is_valid_format
        s = sightings_by_key.get(sin.key)
        if s is None:
            s = Sighting(
                worker_key=sin.key[:80],
                camera_id=cam.id,
                plate_norm=plate_norm[:16],
                is_valid_format=is_valid,
                first_seen=first_seen,
                last_seen=last_seen,
                read_count=sin.read_count,
                best_conf=sin.best_conf,
                closed=sin.closed,
                mode=batch.mode,
                created_at=now,
                updated_at=now,
            )
            db.add(s)
            sightings_by_key[sin.key] = s
            changed_sightings.add(sin.key)
            counters["accepted_sightings"] += 1
        else:
            if last_seen >= s.last_seen:
                s.last_seen = last_seen
                s.read_count = max(s.read_count, sin.read_count)
                s.best_conf = max(float(s.best_conf), float(sin.best_conf))
                s.closed = sin.closed
                s.updated_at = now
                changed_sightings.add(sin.key)
                counters["accepted_sightings"] += 1
        frame_name = sin.frame_file
        if frame_name:
            err = _validate_jpeg(files.get(frame_name), MAX_FRAME_BYTES)
            if err:
                rejected.append({"index": idx, "kind": "frame", "reason": f"frame_file '{frame_name}' {err}"})
            else:
                rel = f"frames/{cam.id}/{date_dir}/{cam.id}-{plate_norm}-{epoch_ms(first_seen)}.jpg"
                sha, _ = write_bytes_hashed(rel, files[frame_name])
                s.frame_path = rel
                s.frame_sha256 = sha
                changed_sightings.add(sin.key)
    await db.flush()

    # 2. reads — idempotent on (camera_id, captured_at, plate_norm): the worker re-sends the same
    #    multipart batch after a timeout/5xx (CONTRACT §7.7), so a read that is already stored is
    #    reported as `rejected` with reason `duplicate` and neither its crop nor an alert update
    #    is produced. The unique index `uq_reads_camera_captured_plate` makes this race-safe.
    new_reads: list[tuple[PlateRead, Sighting | None, str | None]] = []
    for idx, rin in enumerate(batch.reads):
        captured = None
        try:
            captured = parse_iso(rin.captured_at)
        except ValueError:
            pass
        if captured is None:
            rejected.append({"index": idx, "kind": "read", "reason": "invalid captured_at"})
            continue
        crop = files.get(rin.crop_file or "")
        err = _validate_jpeg(crop, MAX_CROP_BYTES)
        if err or crop is None:
            rejected.append({"index": idx, "kind": "read", "reason": f"crop_file '{rin.crop_file}' {err}"})
            continue
        norm = normalise(rin.plate_raw)
        if rin.plate_norm and (rin.plate_norm != norm.plate_norm or (rin.is_valid_format is not None and rin.is_valid_format != norm.is_valid_format)):
            log.warning("worker normalisation differs for %r: worker=%s api=%s", rin.plate_raw, rin.plate_norm, norm.plate_norm)
        plate_norm = (norm.plate_norm or rin.plate_raw.upper())[:16]
        sighting = sightings_by_key.get(rin.sighting_key)
        rel = f"crops/{cam.id}/{date_dir}/{uuid.uuid4().hex}.jpg"
        values = {
            "camera_id": cam.id,
            "sighting_id": sighting.id if sighting else None,
            "captured_at": captured,
            "stream_pts": rin.stream_pts,
            "frame_index": rin.frame_index,
            "plate_raw": rin.plate_raw.replace("\n", " ").strip().upper()[:32],
            "plate_norm": plate_norm,
            "is_valid_format": norm.is_valid_format,
            "confidence": float(rin.confidence),
            "bbox": rin.bbox,
            "crop_path": rel,
            "crop_sha256": hashlib.sha256(crop).hexdigest(),
            "mode": batch.mode,
            "created_at": now,
        }
        read_id = await _insert_read_once(db, values)
        if read_id is None:
            rejected.append({"index": idx, "kind": "read", "reason": "duplicate"})
            continue
        write_bytes_hashed(rel, crop)  # only after the row is ours: no orphan crops on replays
        read = await db.get(PlateRead, read_id)
        assert read is not None
        new_reads.append((read, sighting, rin.crop_file))
        counters["accepted_reads"] += 1
    await db.flush()

    # best crop linkage for sightings
    for sin in batch.sightings:
        s = sightings_by_key.get(sin.key)
        if s is None or not sin.best_crop_file:
            continue
        for read, sighting, crop_field in new_reads:
            if sighting is s and crop_field == sin.best_crop_file:
                s.best_read_id = read.id
                s.best_crop_path = read.crop_path
                changed_sightings.add(sin.key)
                break
    for read, sighting, _ in new_reads:
        if sighting is not None and sighting.best_read_id is None:
            sighting.best_read_id = read.id
            sighting.best_crop_path = read.crop_path
            changed_sightings.add(sighting.worker_key)
    await db.flush()

    # 3. watchlist matching
    alert_ids: dict[int, int] = {}
    for read, sighting, _ in new_reads:
        alert, created = await matcher.process_read(db, read, sighting, cam)
        if alert is not None:
            alert_ids[read.id] = alert.id
            counters["alerts_created" if created else "alerts_updated"] += 1

    # 4. object counts
    counters["object_counts_upserted"] += await upsert_object_counts(db, cam.id, batch.object_counts, rejected)

    # 5. events
    counters["events_created"] += await create_worker_events(db, cam, batch.events, files, rejected)

    await db.commit()

    # 6. broadcasts (after commit so ids are final)
    channel = _reads_channel(cam.id)
    for read, sighting, _ in new_reads:
        manager.broadcast(channel, "read", serializers.read_item(read, cam, alert_ids.get(read.id)), cam.department_id, cam.district)
    for key in changed_sightings:
        s = sightings_by_key.get(key)
        if s is not None:
            manager.broadcast(channel, "sighting", serializers.sighting_item(s, cam), cam.department_id, cam.district)
    if batch.live_counts is not None:
        counts = {c: int(batch.live_counts.counts.get(c, 0)) for c in ALL_CLASSES}
        manager.broadcast(channel, "object_counts", {"camera_id": cam.id, "minute": batch.live_counts.minute, "counts": counts, "final": False}, cam.department_id, cam.district)
    if batch.object_counts:
        by_minute: dict[str, dict[str, int]] = {}
        for oc in batch.object_counts:
            by_minute.setdefault(oc.minute, {c: 0 for c in ALL_CLASSES})[oc.class_] = oc.count
        for minute, counts in by_minute.items():
            manager.broadcast(channel, "object_counts", {"camera_id": cam.id, "minute": minute, "counts": counts, "final": True}, cam.department_id, cam.district)

    return {**counters, "rejected": rejected, "server_time": iso_z(utcnow())}


async def _insert_read_once(db: AsyncSession, values: dict[str, Any]) -> int | None:
    """INSERT … ON CONFLICT (camera_id, captured_at, plate_norm) DO NOTHING; return the new id or None."""
    stmt = (
        pg_insert(PlateRead)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["camera_id", "captured_at", "plate_norm"])
        .returning(PlateRead.id)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def upsert_object_counts(db: AsyncSession, camera_id: int, counts: list[ObjectCountIn], rejected: list[dict[str, Any]]) -> int:
    n = 0
    for idx, oc in enumerate(counts):
        try:
            minute = parse_iso(oc.minute)
        except ValueError:
            minute = None
        if minute is None:
            rejected.append({"index": idx, "kind": "object_count", "reason": "invalid minute"})
            continue
        minute = minute.replace(second=0, microsecond=0)
        stmt = pg_insert(ObjectCount).values(camera_id=camera_id, minute=minute, **{"class": oc.class_}, count=oc.count)
        stmt = stmt.on_conflict_do_update(constraint="uq_object_counts", set_={"count": oc.count})
        await db.execute(stmt)
        n += 1
    return n


async def create_worker_events(db: AsyncSession, cam: Camera, events: list[EventIn], files: dict[str, bytes], rejected: list[dict[str, Any]]) -> int:
    n = 0
    now = utcnow()
    for idx, ev in enumerate(events):
        try:
            occurred = parse_iso(ev.occurred_at)
        except ValueError:
            occurred = None
        if occurred is None:
            rejected.append({"index": idx, "kind": "event", "reason": "invalid occurred_at"})
            continue
        frame_path = frame_sha = None
        if ev.frame_file:
            err = _validate_jpeg(files.get(ev.frame_file), MAX_FRAME_BYTES)
            if err:
                rejected.append({"index": idx, "kind": "event_frame", "reason": f"frame_file '{ev.frame_file}' {err}"})
            else:
                frame_path = f"frames/{cam.id}/{utc_date_dir(now)}/{cam.id}-intrusion-{epoch_ms(occurred)}.jpg"
                frame_sha, _ = write_bytes_hashed(frame_path, files[ev.frame_file])
        note = ev.note
        if ev.type == "loop_reset" and not note and ev.stream_pts_before is not None:
            note = f"pts {ev.stream_pts_before} -> {ev.stream_pts_after} (discontinuity)"
        event = Event(camera_id=cam.id, occurred_at=occurred, type=ev.type, note=note, frame_path=frame_path, frame_sha256=frame_sha, is_auto=True, created_at=now)
        db.add(event)
        await db.flush()
        n += 1
        if ev.type == "intrusion":
            alert = await _intrusion_alert(db, cam, ev, event, now)
            if alert is not None:
                event.alert_id = alert.id
        manager.broadcast(_reads_channel(cam.id), "event", serializers.event_item(event, cam), cam.department_id, cam.district)
    return n


async def _intrusion_alert(db: AsyncSession, cam: Camera, ev: EventIn, event: Event, now: datetime) -> Alert | None:
    zone = await db.get(Zone, ev.zone_id) if ev.zone_id else None
    priority = zone.priority if zone else "medium"
    recent = (
        await db.execute(
            select(Alert)
            .where(Alert.type == "intrusion", Alert.camera_id == cam.id, Alert.zone_id == ev.zone_id, Alert.created_at >= now - timedelta(seconds=INTRUSION_SUPPRESSION_S))
            .order_by(Alert.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if recent is not None:
        recent.read_count += 1
        recent.last_read_at = event.occurred_at
        recent.updated_at = now
        manager.broadcast("alerts", "alert_update", serializers.alert_update_payload(recent), cam.department_id, cam.district)
        return recent
    alert = Alert(
        type="intrusion",
        status="new",
        priority=priority,
        camera_id=cam.id,
        zone_id=ev.zone_id,
        snapshot_path=event.frame_path,
        snapshot_sha256=event.frame_sha256,
        read_count=1,
        last_read_at=event.occurred_at,
        latency_ms=max(0, int((now - event.occurred_at).total_seconds() * 1000)),
        note=ev.note,
        created_at=now,
        updated_at=now,
    )
    db.add(alert)
    await db.flush()
    matcher.broadcast_alert(alert, cam, None, None)
    return alert


async def store_snapshot(cam: Camera, data: bytes) -> str:
    err = _validate_jpeg(data, 150 * 1024)
    if err:
        raise ApiError(400, f"snapshot file {err}")
    rel = f"snapshots/cam_{cam.id}.jpg"
    write_bytes_hashed(rel, data)
    ts = int(utcnow().timestamp())
    manager.broadcast(_reads_channel(cam.id), "snapshot", {"camera_id": cam.id, "url": f"{media_url(rel)}?t={ts}", "updated_at": iso_z(utcnow())}, cam.department_id, cam.district)
    return rel
