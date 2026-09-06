"""Health poller (CONTRACT §5.6): MediaMTX state + ffprobe probes → state machine → alerts/events/WS."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rbac import Scope
from app.core.tz import iso_z, utcnow
from app.db.models import Alert, AnprWorker, Camera, CameraHealthLog, Event, PlateRead
from app.db.session import SessionLocal
from app.services import lookups, serializers
from app.services.audit import write_audit
from app.services.mediamtx_client import client as mtx
from app.services.mediamtx_client import (
    PERSISTENT,
    config_policy,
    dial_gate,
    external_probe_timeout_s,
    ffprobe_ok,
    h264_path,
    is_external_source,
    relay_path,
    relay_policy,
    relay_rtsp_url,
)
from app.services.notifications import dispatch_camera_status
from app.services.scope import camera_conditions
from app.services.ws_manager import manager

log = logging.getLogger("sentinel.health")

VIDEO_PREFIXES = ("H264", "H265", "MJPEG", "AV1", "VP")
STEADY_STATES = ("online", "degraded", "offline", "not_streaming")


def decide_status(
    prev_status: str, is_ready: bool, has_video: bool, bytes_delta: int, source_flag: str,
    fail_count: int, ever_seen: bool, offline_after: int,
) -> tuple[str, int]:
    """CONTRACT §5.6 state machine (pure; `tests/test_health_state.py`). Returns (new_status, fail_count).

    `offline` is reserved for cameras that have delivered a stream before (`ever_seen`, i.e.
    `last_seen_at` is set): only an online/degraded → offline transition is an outage that
    raises a `camera_offline` alert. A camera that never came up (catalogue `live=false`, or a
    source that never answered) becomes `not_streaming` after the same number of failed checks
    and raises nothing (CONTRACT Amendments 2026-09-05).
    """
    if is_ready and has_video and (bytes_delta > 0 or source_flag == "probe"):
        return "online", 0
    if is_ready:
        return "degraded", 0
    fail_count += 1
    if fail_count >= offline_after:
        return ("offline" if ever_seen else "not_streaming"), fail_count
    return (prev_status if prev_status in STEADY_STATES else "unknown"), fail_count


def select_probe_candidates(
    candidates: list[tuple[int, datetime | None]], now: datetime, min_interval_s: int, max_per_tick: int,
) -> tuple[list[int], list[int]]:
    """"Pace your load" (organiser rule; CONTRACT Amendments 2026-09-05). `candidates` = (camera_id, last_probe_at)
    for the idle cameras that would need an active probe this tick. Returns (probe_now, reuse_last): a camera is
    probed at most once per `min_interval_s` while idle — in between its last probe result is reused — and at most
    `max_per_tick` cameras are probed per tick, least-recently-probed first.
    """
    due: list[tuple[int, datetime | None]] = []
    reuse: list[int] = []
    for cam_id, last in candidates:
        if last is not None and (now - last).total_seconds() < min_interval_s:
            reuse.append(cam_id)
        else:
            due.append((cam_id, last))
    due.sort(key=lambda t: (t[1] is not None, t[1] or now))
    probe_now = [cam_id for cam_id, _ in due[:max_per_tick]]
    # over budget this tick: those keep whatever they had (reused) and move to the front next tick
    reuse.extend(cam_id for cam_id, _ in due[max_per_tick:])
    return probe_now, reuse


RELAY_START_MARGIN_S = 15  # relay on-demand start window is probe cap + 10 s; the probe waits 5 s beyond it


def probe_target(
    rtsp_url: str | None, camera_id: int, local_timeout_s: float, external_timeout_s: float,
    source: str | None = "sandbox", via: str = "direct",
) -> tuple[str, float]:
    """(url, timeout_s) for the active probe of an idle **on-demand** camera (pure; `tests/test_sentinel_portal.py`).
    Persistent cameras are never probed (`tick_action`). The probe is issued only when no reader holds the relay
    path, so it is never a second copy next to a wall viewer or the worker.

    A local source (synthetic loop, own gate published into our MediaMTX) is probed through the relay path with the
    short `HEALTH_PROBE_TIMEOUT_S`. An organiser-sandbox camera (`source='sandbox'`, external URL) follows
    `HEALTH_PROBE_VIA` (CONTRACT Amendments 2026-09-05, relay policy):
    - `direct` (default): one short connection straight to the source with the `sandbox.probe_timeout_s` cap
      (DESCRIBE/PLAY, a few packets, TEARDOWN — closed within seconds; the sandbox answers in 4-38 s);
    - `relay`: through `cam_<id>` — the copy the wall would open — with cap + `RELAY_START_MARGIN_S` so the
      relay's on-demand start window (cap + 10 s) completes first. Note that MediaMTX then keeps that upstream
      copy for the full 10-minute close-after, i.e. up to ~15 idle sandbox streams held on average at a 15-minute
      probe interval, which is why `direct` is the default ("open only what you process").
    Any other external source (vendor NVR, phone) keeps the earlier behaviour: probed directly with the cap.
    """
    if rtsp_url and is_external_source(rtsp_url):
        if source == "sandbox" and via == "relay":
            return relay_rtsp_url(camera_id), float(external_timeout_s) + RELAY_START_MARGIN_S
        return rtsp_url, float(external_timeout_s)
    return relay_rtsp_url(camera_id), float(local_timeout_s)


def seed_last_probe(rows: list[tuple[int, datetime | None]], current: dict[int, datetime]) -> dict[int, datetime]:
    """Merge `(camera_id, last probe time)` rows from the health log into the in-memory history, keeping the
    later of the two for a camera present in both (pure; `tests/test_relay_policy.py`)."""
    out: dict[int, datetime] = {}
    for cam_id, at in rows:
        if at is None:
            continue
        have = current.get(int(cam_id))
        if have is None or at > have:
            out[int(cam_id)] = at
    return out


def tick_action(policy: str, live: bool | None, is_ready: bool) -> str:
    """What the poller does with a camera this tick (pure; `tests/test_health_state.py`):

    - `observed`   – the relay path is ready: state from `ready`/`tracks`/`bytesReceived` (every policy);
    - `catalogue`  – not ready and the catalogue says `live=false`: counted as a miss, never probed;
    - `relay_only` – not ready and the relay source is **persistent**: the relay is already retrying the
                     upstream itself, so the miss is taken from the relay state — no probe, no extra dial;
    - `probe`      – not ready and on demand: an active probe is due (subject to the pacing budget); when the
                     budget defers it the camera is left untouched ("unknown, retry later").
    """
    if is_ready:
        return "observed"
    if live is False:
        return "catalogue"
    if policy == PERSISTENT:
        return "relay_only"
    return "probe"


def worker_degraded(extra: dict[str, Any] | None) -> bool:
    """True when the heartbeat `extra.detector` block says the worker fell back (e.g. ONNX requested, contour active)."""
    det = extra.get("detector") if isinstance(extra, dict) else None
    if not isinstance(det, dict):
        return False
    if det.get("degraded") is True:
        return True
    requested, active = det.get("requested"), det.get("active")
    return bool(requested and active and requested != "auto" and requested != active)


class HealthPoller:
    def __init__(self) -> None:
        self._prev_bytes: dict[str, int] = {}
        self._last_probe: dict[int, datetime] = {}
        self._lock = asyncio.Lock()
        self.last_run_at: datetime | None = None
        self.mediamtx_ok: bool | None = None
        self.mediamtx_paths = 0
        self.mediamtx_ready = 0
        self.relay_persistent = 0  # cam_<id> paths configured with a persistent (always-on) upstream source
        self.relay_persistent_ready = 0
        self._primed = False

    async def _prime_last_probe(self, db: AsyncSession) -> None:
        """Seed the in-memory probe history from `camera_health_log` once per process, so an API restart does
        not start a fresh probe cycle over every idle camera (the 15-minute spacing survives restarts)."""
        rows = (
            await db.execute(
                select(CameraHealthLog.camera_id, func.max(CameraHealthLog.checked_at)).where(CameraHealthLog.source_flag == "probe").group_by(CameraHealthLog.camera_id)
            )
        ).all()
        self._last_probe.update(seed_last_probe(rows, self._last_probe))
        self._primed = True

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
        config_items = await mtx.list_config_path_items()
        self.mediamtx_ok = paths is not None
        if paths is not None:
            self.mediamtx_paths = len(paths)
            self.mediamtx_ready = sum(1 for p in paths.values() if p.get("ready"))
        async with SessionLocal() as db:
            await lookups.departments(db)
            if not self._primed:
                await self._prime_last_probe(db)
            cams = (await db.execute(select(Camera).where(Camera.status != "retired"))).scalars().all()
            checks: dict[int, dict[str, Any]] = {}
            probe_candidates: list[Camera] = []
            persistent = persistent_ready = 0
            for cam in cams:
                if not cam.rtsp_url:
                    continue
                rp = relay_path(cam.id)
                policy = relay_policy(cam)
                if config_items is not None:
                    cfg_item = config_items.get(rp)
                    if cfg_item is None:
                        # lost runtime config (MediaMTX restart) → self-heal (full path set, policy included)
                        await mtx.ensure_camera_paths(cam)
                    elif config_policy(cfg_item) != policy:
                        # policy drift (older API, manual change, anpr flag flipped) → re-assert it, paced
                        await mtx.ensure_relay_policy(cam)
                item = (paths or {}).get(rp)
                is_ready = bool(item and item.get("ready"))
                if policy == PERSISTENT:
                    persistent += 1
                    persistent_ready += int(is_ready)
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
                action = tick_action(policy, cam.live, is_ready)
                if action == "catalogue":
                    checks[cam.id]["source_flag"] = "catalogue"
                elif action == "probe":
                    probe_candidates.append(cam)
                elif action == "observed":
                    # a ready path (wall viewer, worker) is as good as a probe: the next active probe of this
                    # on-demand camera is due HEALTH_PROBE_MIN_INTERVAL_S after it was last seen ready
                    self._last_probe[cam.id] = now
                # "relay_only": a persistent source that is down — the relay retries it itself; the miss counts
            self.relay_persistent, self.relay_persistent_ready = persistent, persistent_ready
            # Probe budget ("pace your load"): ≤ HEALTH_PROBE_PARALLEL at once, RELAY_DIAL_SPACING_S apart,
            # ≤ HEALTH_PROBE_MAX per tick, and an idle camera at most once per HEALTH_PROBE_MIN_INTERVAL_S.
            probe_now, deferred = select_probe_candidates(
                [(c.id, self._last_probe.get(c.id)) for c in probe_candidates], now, settings.HEALTH_PROBE_MIN_INTERVAL_S, settings.HEALTH_PROBE_MAX,
            )
            # A deferred camera had no observation this tick: status and fail count stay as they are ("unknown,
            # retry later"); only HEALTH_OFFLINE_AFTER consecutive probe misses take an on-demand camera offline.
            for cam_id in deferred:
                checks.pop(cam_id, None)
            probe_ids = set(probe_now)
            # 1. everything that needs no probe is applied now, so slow probes never delay the live cameras
            for cam in cams:
                chk = checks.get(cam.id)
                if chk is not None and cam.id not in probe_ids:
                    await self._apply(db, cam, chk, now)
            await db.commit()
            # 2. paced active probes of idle on-demand cameras
            by_id = {c.id: c for c in probe_candidates}
            for cam_id in probe_now:
                self._last_probe[cam_id] = now
            probe_results = await self._probe([by_id[i] for i in probe_now])
            probed_at = utcnow()
            for cam_id, ok in probe_results.items():
                chk = checks[cam_id]
                chk["source_flag"] = "probe"
                if ok:
                    chk.update({"is_ready": True, "has_video": True, "bytes_delta": 0})
                await self._apply(db, by_id[cam_id], chk, probed_at)
            await db.commit()
        self.last_run_at = now

    async def _probe(self, cams: list[Camera]) -> dict[int, bool]:
        results: dict[int, bool] = {}
        if not cams:
            return results
        sem = asyncio.Semaphore(max(1, min(6, settings.HEALTH_PROBE_PARALLEL)))
        external_timeout = external_probe_timeout_s()

        async def one(cam: Camera) -> None:
            url, timeout_s = probe_target(cam.rtsp_url, cam.id, settings.HEALTH_PROBE_TIMEOUT_S, external_timeout, cam.source, settings.HEALTH_PROBE_VIA)
            async with sem:
                await dial_gate.wait()  # one new upstream connection per RELAY_DIAL_SPACING_S, shared with the relay
                results[cam.id] = await ffprobe_ok(url, timeout_s=timeout_s)

        await asyncio.gather(*(one(c) for c in cams))
        return results

    async def _apply(self, db: AsyncSession, cam: Camera, chk: dict[str, Any], now: datetime) -> None:
        prev_status = cam.status
        is_ready = chk["is_ready"]
        new_status, cam.health_fail_count = decide_status(
            prev_status, is_ready, chk["has_video"], chk["bytes_delta"], chk["source_flag"],
            cam.health_fail_count or 0, cam.last_seen_at is not None, settings.HEALTH_OFFLINE_AFTER,
        )
        if is_ready:
            cam.last_seen_at = now
        db.add(CameraHealthLog(camera_id=cam.id, checked_at=now, is_ready=is_ready, has_video=chk["has_video"], bytes_delta=chk["bytes_delta"], readers=chk["readers"], source_flag=chk["source_flag"], status_after=new_status))
        if new_status != prev_status:
            cam.status = new_status
            cam.last_status_change_at = now
            await self._transition(db, cam, prev_status, new_status, chk, now)

    async def _transition(self, db: AsyncSession, cam: Camera, prev: str, new: str, chk: dict[str, Any], now: datetime) -> None:
        # `not_streaming` (never seen online) is not an outage: status + health log + WS `health` only —
        # no alert, no event, no webhook (CONTRACT Amendments 2026-09-05). Only a camera that streamed
        # before can go `offline`, and only that transition alerts.
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


def worker_item(
    worker_id: str, mode: str, gpu: bool, version: str | None, detector: str | None, cams: list[dict[str, Any]],
    last_heartbeat_at: datetime, stale: bool, extra: dict[str, Any] | None, started_at: datetime | None = None,
) -> dict[str, Any]:
    """One `anpr_workers` item (CONTRACT §5.5 + Amendments: `extra`, `degraded`, `started_at`)."""
    return {
        "id": worker_id, "mode": mode, "gpu": gpu, "version": version, "detector": detector,
        "cameras": len(cams), "camera_states": cams, "fps_total": round(sum(float(c.get("fps_actual") or 0) for c in cams), 1),
        "started_at": iso_z(started_at), "last_heartbeat_at": iso_z(last_heartbeat_at), "stale": stale,
        "extra": extra if isinstance(extra, dict) else None, "degraded": worker_degraded(extra),
    }


async def scope_camera_ids(db: AsyncSession, scope: Scope | None, camera_ids: list[int]) -> set[int] | None:
    """Ids from `camera_ids` the scope may see; None = unrestricted (nothing to filter)."""
    if scope is None or scope.unrestricted:
        return None
    if not camera_ids:
        return set()
    rows = (await db.execute(select(Camera.id).where(Camera.id.in_(list(set(camera_ids))), *camera_conditions(scope)))).all()
    return {int(r[0]) for r in rows}


def _int_ids(cams: list[dict[str, Any]]) -> list[int]:
    out = []
    for c in cams:
        try:
            out.append(int(c.get("id")))
        except (TypeError, ValueError):
            continue
    return out


async def worker_status(db: AsyncSession, scope: Scope | None = None) -> list[dict[str, Any]]:
    """Heartbeat rows for /health/summary, /dashboard/stats and the `anpr_status` envelope.

    `camera_states` is filtered to the caller's scope (a dept_admin never learns the ids of other
    departments' ANPR cameras); `cameras`/`fps_total` are counted over the visible subset.
    """
    rows = (await db.execute(select(AnprWorker))).scalars().all()
    now = utcnow()
    allowed = await scope_camera_ids(db, scope, [i for w in rows for i in _int_ids(w.cameras or [])])
    out = []
    for w in rows:
        cams = [c for c in (w.cameras or []) if allowed is None or c.get("id") in allowed]
        stale = (now - w.last_heartbeat_at) > timedelta(seconds=3 * settings.HEARTBEAT_S)
        out.append(worker_item(w.id, w.mode, w.gpu, w.version, w.detector, cams, w.last_heartbeat_at, stale, w.extra, w.started_at))
    return out


async def camera_counts(db: AsyncSession, scope_conds: list) -> dict[str, int]:
    q = select(Camera.status, func.count()).group_by(Camera.status)
    for c in scope_conds:
        q = q.where(c)
    rows = (await db.execute(q)).all()
    counts = {"total": 0, "online": 0, "degraded": 0, "offline": 0, "not_streaming": 0, "unknown": 0, "retired": 0}
    for status, n in rows:
        counts[status] = counts.get(status, 0) + int(n)
        if status != "retired":
            counts["total"] += int(n)
    return counts


async def uptime_24h(db: AsyncSession, scope_conds: list) -> float | None:
    """Ready checks / all checks over 24 h for **monitored** cameras.

    `source_flag='catalogue'` rows (cameras the catalogue marks not live, never probed) are
    excluded so 42 not-streaming mock cameras cannot drag the estate figure to 17 % while the 9
    monitored cameras are 100 % up (CONTRACT Amendments 2026-09-05).
    """
    since = utcnow() - timedelta(hours=24)
    q = select(func.count(), func.sum(func.cast(CameraHealthLog.is_ready, Integer))).where(CameraHealthLog.checked_at >= since, CameraHealthLog.source_flag != "catalogue")
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
