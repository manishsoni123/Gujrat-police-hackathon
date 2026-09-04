"""Health poller (CONTRACT §5.6): MediaMTX state + ffprobe probes → state machine → alerts/events/WS."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.tz import iso_z, utcnow
from app.db.models import Alert, AnprWorker, Camera, CameraHealthLog, Event, PlateRead
from app.db.session import SessionLocal
from app.services import lookups, serializers
from app.services.audit import write_audit
from app.services.mediamtx_client import client as mtx
from app.services.mediamtx_client import ffprobe_ok, h264_path, relay_path, relay_rtsp_url
from app.services.notifications import dispatch_camera_status
from app.services.ws_manager import manager

log = logging.getLogger("sentinel.health")

VIDEO_PREFIXES = ("H264", "H265", "MJPEG", "AV1", "VP")


class HealthPoller:
    def __init__(self) -> None:
        self._prev_bytes: dict[str, int] = {}
        self._last_probe: dict[int, datetime] = {}
        self._lock = asyncio.Lock()
        self.last_run_at: datetime | None = None
        self.mediamtx_ok: bool | None = None
        self.mediamtx_paths = 0
        self.mediamtx_ready = 0

    async def run_once(self) -> None:
        if self._lock.locked():
            log.warning("health poll skipped: previous run still active")
            return
        async with self._lock:
            try:
                await self._run()
            except Exception:  # noqa: BLE001
                log.exception("health poll failed")

    async def _run(self) -> None:
        now = utcnow()
        paths = await mtx.list_paths()
        config_paths = await mtx.list_config_paths()
        self.mediamtx_ok = paths is not None
        if paths is not None:
            self.mediamtx_paths = len(paths)
            self.mediamtx_ready = sum(1 for p in paths.values() if p.get("ready"))
        async with SessionLocal() as db:
            await lookups.departments(db)
            cams = (await db.execute(select(Camera).where(Camera.status != "retired"))).scalars().all()
            checks: dict[int, dict[str, Any]] = {}
            probe_candidates: list[Camera] = []
            for cam in cams:
                if not cam.rtsp_url:
                    continue
                rp = relay_path(cam.id)
                if config_paths is not None and rp not in config_paths:
                    # lost runtime config (MediaMTX restart) → self-heal
                    await mtx.ensure_camera_paths(cam)
                item = (paths or {}).get(rp)
                is_ready = bool(item and item.get("ready"))
                tracks = (item or {}).get("tracks") or []
                has_video = any(str(t).upper().startswith(VIDEO_PREFIXES) for t in tracks)
                total = int((item or {}).get("bytesReceived") or 0)
                prev = self._prev_bytes.get(rp)
                # First observation after an API start (or a counter reset) counts as "previous = 0" (CONTRACT §5.6),
                # otherwise every online camera flips to degraded for one tick after each restart.
                delta = total if prev is None or total < prev else max(0, total - prev)
                self._prev_bytes[rp] = total
                readers = len((item or {}).get("readers") or [])
                checks[cam.id] = {"is_ready": is_ready, "has_video": has_video, "bytes_delta": delta, "readers": readers, "source_flag": "mediamtx"}
                if not is_ready:
                    if cam.live is False:
                        checks[cam.id]["source_flag"] = "catalogue"
                    else:
                        probe_candidates.append(cam)
            probe_candidates.sort(key=lambda c: self._last_probe.get(c.id, datetime.min.replace(tzinfo=now.tzinfo)))
            for cam in probe_candidates[: settings.HEALTH_PROBE_MAX]:
                self._last_probe[cam.id] = now
            probe_results = await self._probe(probe_candidates[: settings.HEALTH_PROBE_MAX])
            for cam_id, ok in probe_results.items():
                checks[cam_id]["source_flag"] = "probe"
                if ok:
                    checks[cam_id].update({"is_ready": True, "has_video": True, "bytes_delta": 0})
            for cam in cams:
                chk = checks.get(cam.id)
                if chk is None:
                    continue
                await self._apply(db, cam, chk, now)
            await db.commit()
        self.last_run_at = now

    async def _probe(self, cams: list[Camera]) -> dict[int, bool]:
        results: dict[int, bool] = {}
        sem = asyncio.Semaphore(4)

        async def one(cam: Camera) -> None:
            async with sem:
                results[cam.id] = await ffprobe_ok(relay_rtsp_url(cam.id))

        await asyncio.gather(*(one(c) for c in cams))
        return results

    async def _apply(self, db: AsyncSession, cam: Camera, chk: dict[str, Any], now: datetime) -> None:
        prev_status = cam.status
        is_ready, has_video, delta, flag = chk["is_ready"], chk["has_video"], chk["bytes_delta"], chk["source_flag"]
        if is_ready and has_video and (delta > 0 or flag == "probe"):
            new_status = "online"
        elif is_ready:
            new_status = "degraded"
        else:
            cam.health_fail_count = (cam.health_fail_count or 0) + 1
            new_status = "offline" if cam.health_fail_count >= settings.HEALTH_OFFLINE_AFTER else (prev_status if prev_status in ("online", "degraded", "offline") else "unknown")
        if is_ready:
            cam.health_fail_count = 0
            cam.last_seen_at = now
        db.add(CameraHealthLog(camera_id=cam.id, checked_at=now, is_ready=is_ready, has_video=has_video, bytes_delta=delta, readers=chk["readers"], source_flag=flag, status_after=new_status))
        if new_status != prev_status:
            cam.status = new_status
            cam.last_status_change_at = now
            await self._transition(db, cam, prev_status, new_status, chk, now)

    async def _transition(self, db: AsyncSession, cam: Camera, prev: str, new: str, chk: dict[str, Any], now: datetime) -> None:
        if new == "offline":
            db.add(Event(camera_id=cam.id, occurred_at=now, type="camera_offline", note=f"{cam.name} offline after {settings.HEALTH_OFFLINE_AFTER} failed checks", is_auto=True, created_at=now))
            if cam.maintenance_status not in ("under_maintenance", "decommissioned"):
                open_alert = (
                    await db.execute(select(Alert).where(Alert.type == "camera_offline", Alert.camera_id == cam.id, Alert.status.in_(("new", "acknowledged"))).limit(1))
                ).scalar_one_or_none()
                if open_alert is None:
                    snap = f"snapshots/cam_{cam.id}.jpg"
                    from app.core.hashing import abs_path

                    alert = Alert(type="camera_offline", status="new", priority="low", camera_id=cam.id, snapshot_path=snap if abs_path(snap).exists() else None, read_count=1, latency_ms=0, created_at=now, updated_at=now)
                    db.add(alert)
                    await db.flush()
                    from app.services.matcher import matcher

                    matcher.broadcast_alert(alert, cam, None, None)
            dispatch_camera_status("camera.offline", {"camera": serializers.camera_summary(cam), "at": iso_z(now)})
        elif prev == "offline" and new in ("online", "degraded"):
            db.add(Event(camera_id=cam.id, occurred_at=now, type="camera_online", note=f"{cam.name} back {new}", is_auto=True, created_at=now))
            open_alerts = (await db.execute(select(Alert).where(Alert.type == "camera_offline", Alert.camera_id == cam.id, Alert.status.in_(("new", "acknowledged"))))).scalars().all()
            for a in open_alerts:
                a.status = "closed"
                a.outcome = "resolved"
                a.note = "Camera back online"
                a.closed_at = now
                a.closed_by = None
                a.updated_at = now
                if a.acknowledged_at is None:
                    a.acknowledged_at = now
                manager.broadcast("alerts", "alert_update", serializers.alert_update_payload(a), cam.department_id, cam.district)
                await write_audit("alert.auto_close", entity="alert", entity_id=a.id, after={"note": "Camera back online", "camera_id": cam.id})
            dispatch_camera_status("camera.online", {"camera": serializers.camera_summary(cam), "at": iso_z(now)})
        manager.broadcast(
            "health",
            "health",
            {
                "camera_id": cam.id,
                "name": cam.name,
                "status": new,
                "previous_status": prev,
                "last_seen_at": iso_z(cam.last_seen_at),
                "checked_at": iso_z(now),
                "is_ready": chk["is_ready"],
                "has_video": chk["has_video"],
                "bytes_delta": chk["bytes_delta"],
                "readers": chk["readers"],
                "source_flag": chk["source_flag"],
                "maintenance_status": cam.maintenance_status,
                "district": cam.district,
                "department_code": (lookups.dept_sync(cam.department_id).code if lookups.dept_sync(cam.department_id) else None),
            },
            cam.department_id,
            cam.district,
        )


poller = HealthPoller()


async def ensure_all_relay_paths() -> int:
    """Startup: (re)create MediaMTX paths for every non-retired camera with an rtsp_url."""
    n = 0
    async with SessionLocal() as db:
        cams = (await db.execute(select(Camera).where(Camera.status != "retired", Camera.rtsp_url.isnot(None)))).scalars().all()
    for cam in cams:
        res = await mtx.ensure_camera_paths(cam)
        n += sum(1 for r in res if r.ok)
    return n


def disk_usage() -> dict[str, int]:
    out = {"data_used_bytes": 0, "data_free_bytes": 0, "recordings_used_bytes": 0}
    try:
        u = shutil.disk_usage(settings.DATA_DIR)
        out["data_free_bytes"] = int(u.free)
        out["data_used_bytes"] = _dir_size(settings.DATA_DIR)
    except OSError:
        pass
    try:
        out["recordings_used_bytes"] = _dir_size(settings.RECORDINGS_DIR)
    except OSError:
        pass
    return out


_dir_cache: dict[str, tuple[float, int]] = {}


def _dir_size(path: str) -> int:
    import os
    import time

    now = time.monotonic()
    cached = _dir_cache.get(path)
    if cached and now - cached[0] < 60:
        return cached[1]
    total = 0
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    _dir_cache[path] = (now, total)
    return total


async def worker_status(db: AsyncSession) -> list[dict[str, Any]]:
    rows = (await db.execute(select(AnprWorker))).scalars().all()
    now = utcnow()
    out = []
    for w in rows:
        cams = w.cameras or []
        fps_total = round(sum(float(c.get("fps_actual") or 0) for c in cams), 1)
        stale = (now - w.last_heartbeat_at) > timedelta(seconds=3 * settings.HEARTBEAT_S)
        out.append({"id": w.id, "mode": w.mode, "gpu": w.gpu, "version": w.version, "detector": w.detector, "cameras": len(cams), "camera_states": cams, "fps_total": fps_total, "last_heartbeat_at": iso_z(w.last_heartbeat_at), "stale": stale})
    return out


async def camera_counts(db: AsyncSession, scope_conds: list) -> dict[str, int]:
    q = select(Camera.status, func.count()).group_by(Camera.status)
    for c in scope_conds:
        q = q.where(c)
    rows = (await db.execute(q)).all()
    counts = {"total": 0, "online": 0, "degraded": 0, "offline": 0, "unknown": 0, "retired": 0}
    for status, n in rows:
        counts[status] = int(n)
        if status != "retired":
            counts["total"] += int(n)
    return counts


async def uptime_24h(db: AsyncSession, scope_conds: list) -> float | None:
    since = utcnow() - timedelta(hours=24)
    q = select(func.count(), func.sum(func.cast(CameraHealthLog.is_ready, __import__("sqlalchemy").Integer))).where(CameraHealthLog.checked_at >= since)
    if scope_conds:
        q = q.where(CameraHealthLog.camera_id.in_(select(Camera.id).where(*scope_conds)))
    total, ready = (await db.execute(q)).one()
    if not total:
        return None
    return round(100.0 * float(ready or 0) / float(total), 1)


async def reads_last_minute(db: AsyncSession, scope_conds: list) -> int:
    since = utcnow() - timedelta(minutes=1)
    q = select(func.count()).select_from(PlateRead).where(PlateRead.created_at >= since)
    if scope_conds:
        q = q.where(PlateRead.camera_id.in_(select(Camera.id).where(*scope_conds)))
    return int((await db.execute(q)).scalar() or 0)
