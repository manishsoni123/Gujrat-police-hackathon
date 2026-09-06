"""Sentinel Gujarat ANPR worker main loop (plan 5.5, CONTRACT.md section 7).

One process = N ffmpeg decoder threads (``decode.Decoder``) + **one** inference loop (this
module, main thread) + one HTTP sender thread (``client.Sender``) + one control thread
(config reload every ``CONFIG_RELOAD_S``, heartbeat every ``HEARTBEAT_S``).

Per decoded frame (newest frame per camera only - older frames are dropped, never queued):

    detector (on the frame resized to ANPR_DETECT_WIDTH) -> OSD/static filters -> boxes in frame px
    ANPR_BEST_SHOT=1: tracks.PlateTracker -> best crops per track -> OCR when the track settles/ends
                      -> normalise -> voter.add; every ended track = a detected vehicle (evidence)
    ANPR_BEST_SHOT=0: crops -> OCR -> normalise -> voter.add on every frame (pre-5-Sept behaviour)
    voter.expire   -> accepted reads -> sightings -> pending batch
    YOLOX every Nth frame -> tracker -> per-minute counts / intrusion events (live mode)
    snapshot every SNAPSHOT_INTERVAL_S (live mode)
    batch POST /internal/detections every POST_BATCH_S per camera when there is anything to send

Discontinuities (PTS jump back or decoder restart) flush the camera's vote buckets, close its
open sightings, reset its object tracker and emit a ``loop_reset`` event.

Run modes:
    python -m anpr.pipeline                                   # cameras from GET /internal/anpr-config
    python -m anpr.pipeline --source 1=/media/synthetic/cam_1.mp4 --detector contour --dry-run
    ANPR_MODE=preindex python -m anpr.pipeline

Exit codes: 0 normal / SIGTERM, 2 configuration error, 3 API rejected the internal key (401).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np

from anpr import __version__
from anpr.client import ApiClient, ApiError, FatalAuthError, Job, Sender, iso_utc
from anpr.config import Settings, load_settings, redacted
from anpr.decode import Decoder, DialGate, Frame, encode_jpeg, jpeg_under, resize_width
from anpr.detector import Box, OsdMask, StaticBoxFilter, build_detector, detector_status
from anpr.evidence import EvidenceStore
from anpr.normalise import normalise
from anpr.objects import ObjectCounter, YoloxDetector
from anpr.sightings import Sighting, SightingTracker
from anpr.tracks import PlateTracker, Track, crop_with_context, track_kind
from anpr.voting import Candidate, VotedRead, Voter
from anpr.weights.download import WeightStatus, verify_weight_file

log = logging.getLogger("anpr.pipeline")

CROP_MAX_SIDE = 320
CROP_MAX_BYTES = 200_000
FRAME_MAX_BYTES = 400_000
FRAME_JPEG_WIDTH = 960          # CONTRACT section 7.2: sighting frames are 960 px wide (frames may be decoded natively)
SNAPSHOT_MAX_BYTES = 150_000
MIN_OCR_CHARS = 6
EDGE_MARGIN_PX = 2
LIVE_COUNTS_EVERY_S = 5.0
STATS_LOG_EVERY_S = 30.0


# ---------------------------------------------------------------------------
# Logging (JSON lines on stdout, per CONTRACT section 1.1)
# ---------------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": iso_utc(datetime.fromtimestamp(record.created, tz=timezone.utc)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for noisy in ("urllib3", "requests", "ppocr", "paddle"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Camera state
# ---------------------------------------------------------------------------
@dataclass
class CameraConfig:
    id: int
    external_id: str
    name: str
    source: str
    mode: str = "live"
    codec: str = "UNKNOWN"
    zones: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_api(cls, item: dict[str, Any], rtsp_base: str) -> "CameraConfig":
        source = item.get("rtsp_url") or f"{rtsp_base.rstrip('/')}/{item.get('relay_path') or 'cam_' + str(item['id'])}"
        return cls(
            id=int(item["id"]),
            external_id=str(item.get("external_id") or item["id"]),
            name=str(item.get("name") or f"camera {item['id']}"),
            source=str(source),
            mode=str(item.get("mode") or "live"),
            codec=str(item.get("codec") or "UNKNOWN"),
            zones=list(item.get("zones") or []),
        )


@dataclass
class PendingBatch:
    reads: list[VotedRead] = field(default_factory=list)
    object_counts: list[dict[str, Any]] = field(default_factory=list)
    events: list[tuple[dict[str, Any], bytes | None]] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.reads or self.object_counts or self.events)


class CameraWorker:
    """Runtime state of one camera: decoder, newest-frame slot, counters and the pending batch."""

    def __init__(self, cfg: CameraConfig, settings: Settings, pipeline: "Pipeline") -> None:
        self.cfg = cfg
        self.settings = settings
        self.pipeline = pipeline
        self._slot_lock = threading.Lock()
        self._slot: Frame | None = None
        self._dropped = 0
        self.discontinuities: list[tuple[str, float | None, float | None]] = []
        self.pending = PendingBatch()
        self.reads = 0
        self.frames_processed = 0
        self.last_snapshot_mono = 0.0
        self.last_flush_mono = time.monotonic()
        self.last_live_counts_mono = 0.0
        self.static_boxes = StaticBoxFilter(window=settings.static_box_window, min_hits=settings.static_box_hits)
        self.osd = OsdMask(band=settings.osd_band, warmup_s=settings.osd_warmup_s)
        self.tracker = PlateTracker(cfg.id, gap_s=settings.track_gap_s, keep=settings.track_keep, stall_updates=settings.track_stall)
        self.vehicles = 0                 # ended plate tracks = detected vehicles (evidence, whether or not read)
        self.vehicles_ocr = 0             # tracks wide enough for at least one OCR pass
        self.vehicles_read = 0            # tracks whose OCR produced a valid-format plate
        self.text_tracks = 0              # ended tracks whose OCR only ever returned letters (caption / signboard) - not vehicles
        self.evidence_files = 0
        self.ocr_calls = 0
        self.no_digit_reads = 0           # OCR strings without a digit (signboards / captions), dropped before voting
        self.invalid_minute: tuple[int, int] = (0, 0)      # (minute bucket, invalid-format reads posted in it)
        self.invalid_suppressed = 0
        self.counter: ObjectCounter | None = None
        if pipeline.objects is not None:
            self.counter = ObjectCounter(cfg.id)
            self.counter.set_zones(cfg.zones)
        self.decoder = Decoder(
            cfg.id, cfg.source,
            mode=settings.mode, target_fps=settings.fps, out_width=settings.frame_width, cpu=settings.cpu,
            on_frame=self._on_frame, on_discontinuity=self._on_discontinuity,
            reconnect_min_s=settings.reconnect_min_s, reconnect_max_s=settings.reconnect_max_s,
            file_realtime=settings.file_realtime, file_loop=settings.file_loop,
            stall_timeout_s=settings.stall_timeout_s, start_timeout_s=settings.start_timeout_s,
            probe_timeout_s=settings.probe_timeout_s, rtsp_probe=settings.rtsp_probe,
            rtsp_timeout_s=settings.rtsp_timeout_s, decode_threads=settings.decode_threads,
            dial_gate=pipeline.dial_gate,
        )

    def suppression(self) -> dict[str, int]:
        """Boxes / reads this camera dropped as overlay or noise (heartbeat ``extra.suppression``)."""
        return {
            "static": int(self.static_boxes.suppressed),
            "osd_band": int(self.osd.suppressed_band),
            "osd_region": int(self.osd.suppressed_region),
            "osd_regions_now": len(self.osd.regions),
            "invalid_capped": int(self.invalid_suppressed),
            "no_digit": int(self.no_digit_reads),
        }

    def vehicle_stats(self) -> dict[str, int]:
        """Detected-vehicle counters (heartbeat ``extra.vehicles``): tracks ended, OCR'd, read, evidence files."""
        return {
            "detected": int(self.vehicles),
            "ocr": int(self.vehicles_ocr),
            "read": int(self.vehicles_read),
            "text": int(self.text_tracks),
            "open_tracks": int(self.tracker.open),
            "evidence_files": int(self.evidence_files),
            "ocr_calls": int(self.ocr_calls),
        }

    # ---- decoder thread side ---------------------------------------------
    def _on_frame(self, frame: Frame) -> None:
        with self._slot_lock:
            if self._slot is not None:
                self._dropped += 1
            self._slot = frame

    def _on_discontinuity(self, camera_id: int, reason: str, before: float | None, after: float | None) -> None:
        with self._slot_lock:
            self.discontinuities.append((reason, before, after))

    # ---- inference thread side -------------------------------------------
    def take_frame(self) -> Frame | None:
        with self._slot_lock:
            frame, self._slot = self._slot, None
        return frame

    def take_discontinuities(self) -> list[tuple[str, float | None, float | None]]:
        with self._slot_lock:
            items, self.discontinuities = self.discontinuities, []
        return items

    @property
    def dropped(self) -> int:
        return self._dropped

    def start(self) -> None:
        self.decoder.start()

    def stop(self) -> None:
        self.decoder.stop()

    def heartbeat_entry(self) -> dict[str, Any]:
        st = self.decoder.stats
        return {
            "id": self.cfg.id,
            "state": self.decoder.state,
            "fps_actual": round(float(st.fps_actual), 2),
            "frames": int(st.frames),
            "reads": int(self.reads),
            "last_frame_at": iso_utc(st.last_frame_at) if st.last_frame_at else None,
            "decoder_restarts": int(st.restarts),
            "last_error": st.last_error,
        }


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
class Pipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.started_at = datetime.now(timezone.utc)
        self.stop_event = threading.Event()
        self.client = ApiClient(settings.api_url, settings.internal_api_key, dry_run=settings.api_dry_run,
                                timeout_s=settings.api_timeout_s)
        self.sender = Sender(settings.reconnect_min_s, settings.reconnect_max_s)
        # One RTSP dial at a time, ANPR_DIAL_SPACING_S apart (start-up and reconnects): the organiser
        # server refuses bursts of concurrent dials and answers a DESCRIBE in 4-38 s.
        self.dial_gate = DialGate(spacing_s=settings.dial_spacing_s)
        self.detector = build_detector(
            settings.detector, settings.det_model, conf=settings.det_conf, min_w=settings.min_plate_w,
            cpu=settings.cpu, threads=settings.ocr_threads, tile=settings.det_tile, tile_overlap=settings.det_tile_overlap,
        )
        from anpr.ocr import build_ocr

        self.ocr = build_ocr(
            settings.ocr_backend, weights_dir=os.path.dirname(settings.det_model) or "/app/weights", lang=settings.ocr_lang,
            cpu=settings.cpu, threads=settings.ocr_threads, fast_model=settings.ocr_fast_model,
            paddle_mode=settings.ocr_paddle_mode, ensemble_min_conf=settings.ocr_ensemble_min_conf,
        )
        self.evidence = EvidenceStore(settings.evidence_dir or None, settings.evidence_per_hour)
        self.objects: YoloxDetector | None = None
        self.object_weights: WeightStatus | None = None
        if settings.object_detect and settings.mode == "live":
            try:
                self.object_weights = verify_weight_file(settings.object_model)
                if not self.object_weights.ok:
                    raise RuntimeError(self.object_weights.describe())
                log.info("object detector weights: %s", self.object_weights.describe())
                self.objects = YoloxDetector(settings.object_model, conf=settings.object_conf, cpu=settings.cpu, threads=settings.ocr_threads)
            except Exception as exc:  # noqa: BLE001 - counting is optional
                log.warning("object counting disabled: %s", exc)
        self.voter = Voter(window_s=settings.vote_window_s)
        self.sightings = SightingTracker(close_after_s=settings.sighting_close_s)
        self.cameras: dict[int, CameraWorker] = {}
        self._desired: list[CameraConfig] | None = None
        self._desired_lock = threading.Lock()
        self._config_ok = False
        self.static = bool(settings.sources)
        self.timing = {"detect_ms": 0.0, "ocr_ms": 0.0, "objects_ms": 0.0, "frames": 0, "ocr_calls": 0, "object_calls": 0}
        self._last_stats_log = time.monotonic()
        self._loop_started = time.monotonic()

    # ---- camera list -------------------------------------------------------
    def _select(self, configs: list[CameraConfig]) -> list[CameraConfig]:
        s = self.settings
        chosen = [c for c in configs if not s.cameras or c.id in s.cameras]
        if s.mode == "preindex" and s.preindex_skip_live:
            chosen = [c for c in chosen if c.mode != "both"]
        chosen.sort(key=lambda c: c.id)
        cap = s.effective_max_cameras
        if len(chosen) > cap:
            log.info("%d cameras configured, capped at ANPR_MAX_CAMERAS=%d (lowest ids first)", len(chosen), cap)
            chosen = chosen[:cap]
        return chosen

    def _static_configs(self) -> list[CameraConfig]:
        return [CameraConfig(id=cid, external_id=str(cid), name=f"source {cid}", source=src)
                for cid, src in sorted(self.settings.sources.items())]

    def fetch_config(self) -> list[CameraConfig]:
        """GET /internal/anpr-config, apply the server settings, return the camera configs."""
        data = self.client.get_config(self.settings.mode)
        changed = self.settings.apply_server_settings(data.get("settings") or {})
        if changed:
            log.info("server settings applied: %s", {k: getattr(self.settings, k) for k in changed})
            self.voter.window_s = self.settings.vote_window_s
            self.sightings.close_after_s = self.settings.sighting_close_s
        configs = [CameraConfig.from_api(item, self.settings.rtsp_base) for item in data.get("cameras") or []]
        self._config_ok = True
        return configs

    def _initial_cameras(self) -> list[CameraConfig]:
        if self.static:
            return self._select(self._static_configs())
        delay = self.settings.reconnect_min_s
        while not self.stop_event.is_set():
            try:
                return self._select(self.fetch_config())
            except FatalAuthError as exc:
                log.error("%s", exc)
                sys.exit(3)
            except ApiError as exc:
                log.warning("config endpoint unavailable (%s); retry in %.0f s", exc, delay)
                self.stop_event.wait(delay)
                delay = min(self.settings.reconnect_max_s, delay * 2)
        return []

    def reconcile(self, desired: list[CameraConfig]) -> None:
        """Start new cameras, stop vanished ones, restart changed sources, refresh zones."""
        wanted = {c.id: c for c in desired}
        for cid in list(self.cameras):
            cam = self.cameras[cid]
            new = wanted.get(cid)
            if new is None or new.source != cam.cfg.source:
                log.info("camera %s: %s", cid, "removed from config" if new is None else "source changed - restarting")
                self._finish_camera(cam, note="camera stopped")
                cam.stop()
                del self.cameras[cid]
            elif new.zones != cam.cfg.zones:
                cam.cfg.zones = new.zones
                if cam.counter:
                    cam.counter.set_zones(new.zones)
        for cid, cfg in wanted.items():
            if cid not in self.cameras:
                cam = CameraWorker(cfg, self.settings, self)
                self.cameras[cid] = cam
                cam.start()
                log.info("camera %s (%s) started: %s", cid, cfg.name, cfg.source)

    # ---- control thread ---------------------------------------------------------
    def _control_loop(self) -> None:
        next_config = time.monotonic() + self.settings.config_reload_s
        next_heartbeat = time.monotonic() + 2.0
        while not self.stop_event.is_set():
            now = time.monotonic()
            if now >= next_heartbeat:
                self._send_heartbeat()
                next_heartbeat = now + self.settings.heartbeat_s
            if not self.static and now >= next_config:
                try:
                    desired = self._select(self.fetch_config())
                    with self._desired_lock:
                        self._desired = desired
                except FatalAuthError as exc:
                    log.error("%s", exc)
                    self.sender.fatal = exc
                except ApiError as exc:
                    log.warning("config reload failed: %s", exc)
                next_config = now + self.settings.config_reload_s
            self.stop_event.wait(0.5)

    def heartbeat_body(self) -> dict[str, Any]:
        gpu = bool(getattr(getattr(self.detector, "primary", self.detector), "gpu", False))
        return {
            "worker_id": self.settings.worker_id,
            "mode": self.settings.mode,
            "version": __version__,
            "gpu": gpu,
            "cpu_flag": bool(self.settings.cpu),
            "started_at": iso_utc(self.started_at),
            "cameras": [cam.heartbeat_entry() for cam in self.cameras.values()],
            "detector": self.detector.name,
            "object_detect": self.objects is not None,
            "extra": {
                "detector": detector_status(self.detector, self.settings.detector),
                "object_weights": None if self.object_weights is None else self.object_weights.state,
                "suppression": {str(cid): cam.suppression() for cid, cam in self.cameras.items()},
                "dials": self.dial_gate.dials,
                "ocr": {"backend": self.ocr.name, "requested": self.settings.ocr_backend, "min_w": self.settings.ocr_min_w,
                        "best_shot": bool(self.settings.best_shot), "detect_width": self.settings.detect_width},
                "vehicles": {str(cid): cam.vehicle_stats() for cid, cam in self.cameras.items()},
                "evidence": self.evidence.stats(),
            },
        }

    def _send_heartbeat(self) -> None:
        body = self.heartbeat_body()
        self.sender.submit_latest("heartbeat", Job("heartbeat", None, lambda: self.client.post_heartbeat(body)))

    # ---- frame processing ----------------------------------------------------
    @staticmethod
    def _crop_jpeg(crop: np.ndarray) -> bytes:
        h, w = crop.shape[:2]
        longest = max(h, w)
        if longest > CROP_MAX_SIDE:
            scale = CROP_MAX_SIDE / longest
            import cv2

            crop = cv2.resize(crop, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        return jpeg_under(crop, CROP_MAX_BYTES, quality=85)

    def _detect(self, cam: CameraWorker, frame: Frame) -> list[Box]:
        """Detector + OSD/static/edge filters; boxes are returned in decoded-frame pixels.

        With ``ANPR_DETECT_WIDTH`` below the decoded width the detector (and the OSD learner) sees a
        downscaled copy - the tiled ONNX cost stays that of the smaller image - while crops are
        taken from the native frame.
        """
        image = frame.image
        frame_h, frame_w = image.shape[:2]
        det_img, sx, sy = image, 1.0, 1.0
        dw = int(self.settings.detect_width)
        if dw > 0 and frame_w > dw:
            import cv2

            dh = max(2, int(round(frame_h * dw / frame_w)) // 2 * 2)
            det_img = cv2.resize(image, (dw, dh), interpolation=cv2.INTER_AREA)
            sx, sy = frame_w / dw, frame_h / dh
        t0 = time.perf_counter()
        boxes = self.detector.detect(det_img)
        self.timing["detect_ms"] += (time.perf_counter() - t0) * 1000
        det_h, det_w = det_img.shape[:2]
        # A box touching the left/right frame edge is a plate still entering or leaving the
        # picture: its text is cut off and would only feed partial strings into the voter.
        boxes = [b for b in boxes if b.x > EDGE_MARGIN_PX and b.x + b.w < det_w - EDGE_MARGIN_PX]
        # Burnt-in clocks / captions live in the top/bottom bands and in static high-contrast regions
        # (learnt from the first 30 s of the feed); a box there is never a plate.
        cam.osd.observe(det_img, time.monotonic())
        boxes = cam.osd.filter(boxes, det_h)
        # Anything else that sits at the same place in every frame (signboards, parked vehicles).
        boxes = cam.static_boxes.filter(boxes)
        if sx != 1.0 or sy != 1.0:
            boxes = [Box(int(round(b.x * sx)), int(round(b.y * sy)), max(1, int(round(b.w * sx))), max(1, int(round(b.h * sy))),
                         b.confidence, b.source) for b in boxes]
        return sorted(boxes, key=lambda b: (-b.confidence, -b.area))[: self.settings.max_boxes_per_frame]

    def _ocr_crop(self, cam: CameraWorker, crop: np.ndarray, box: Box, captured_at: datetime, stream_pts: float,
                  frame_index: int, frame: np.ndarray | None, track: Track | None = None) -> str | None:
        """OCR one crop into the voter; returns the normalised plate (None when nothing usable came back)."""
        t2 = time.perf_counter()
        result = self.ocr.read(crop)
        self.timing["ocr_ms"] += (time.perf_counter() - t2) * 1000
        self.timing["ocr_calls"] += 1
        cam.ocr_calls += 1
        if not result.raw:
            return None
        norm = normalise(result.raw)
        if len(norm.plate_norm) < MIN_OCR_CHARS:
            log.debug("camera %s: ignored short OCR %r", cam.cfg.id, result.raw)
            return None
        if not any(ch.isdigit() for ch in norm.plate_norm):
            # Every Indian registration carries digits; letters-only text is a signboard or a burnt-in
            # caption ("GUJARAT POLICE", "Bhavani") the detector boxed - never a plate, never voted.
            log.debug("camera %s: ignored letters-only OCR %r", cam.cfg.id, result.raw)
            cam.no_digit_reads += 1
            if track is not None:
                track.text_only += 1
            return None
        conf = float(result.confidence) * (1.0 if box.source == "contour" else min(1.0, 0.5 + box.confidence / 2))
        cand = Candidate(
            camera_id=cam.cfg.id, captured_at=captured_at, stream_pts=stream_pts, frame_index=frame_index,
            plate_raw=result.raw.replace("\n", " "), plate_norm=norm.plate_norm, is_valid_format=norm.is_valid_format,
            confidence=conf, bbox=(box.x, box.y, box.w, box.h), crop_jpeg=self._crop_jpeg(crop), frame=frame,
        )
        self.voter.add(cand)
        log.debug("camera %s: ocr %r -> %s (%.2f, %s, %s, w=%d)", cam.cfg.id, result.raw, norm.plate_norm, conf, box.source,
                  result.backend, box.w)
        return norm.plate_norm

    def _ocr_track(self, cam: CameraWorker, t: Track, frame: Frame | None) -> None:
        """OCR the best shots of a track (largest, sharpness-weighted); their reads are voted like frames.

        ``frame`` is the full frame the best shot's sighting is illustrated with; ``None`` when the
        track is flushed without a current frame (discontinuity / shutdown) - a crop must never be
        posted as a sighting frame.
        """
        if t.ocr_runs == 0:
            cam.vehicles_ocr += 1
        t.ocr_runs += 1
        t.ocr_width = t.best_width
        for i, shot in enumerate(t.shots):
            plate = self._ocr_crop(cam, shot.crop, shot.box, shot.captured_at, shot.stream_pts, shot.frame_index,
                                   frame.image if (i == 0 and frame is not None) else None, track=t)
            if plate:
                t.reads.append(plate)

    def _finish_track(self, cam: CameraWorker, t: Track, frame: Frame | None) -> None:
        """A plate track ended: last OCR chance, then record the detected vehicle (evidence).

        A track whose OCR only ever returned letters (``tracks.track_kind`` = ``text``: a signboard or
        burnt-in caption the detector boxed) is logged and counted separately - it is not a vehicle
        and gets no evidence file, so the detected-vehicle counts stay honest.
        """
        if cam.tracker.ready_for_ocr(t, self.settings.ocr_min_w, ended=True):
            self._ocr_track(cam, t, frame)
        best = t.best
        if best is None:
            return
        kind = track_kind(t)
        if kind == "text":
            cam.text_tracks += 1
            log.info("camera %s: static text t%d w=%dpx frames=%d %s..%s (letters-only OCR x%d, not a vehicle)", cam.cfg.id,
                     t.id, best.box.w, t.frames, iso_utc(t.first_seen)[11:23], iso_utc(t.last_seen)[11:23], t.text_only)
            return
        cam.vehicles += 1
        plates = [p for p in t.reads if p]
        valid = [p for p in plates if normalise(p).is_valid_format]
        if valid:
            cam.vehicles_read += 1
        record = {
            "camera_id": cam.cfg.id, "camera_external_id": cam.cfg.external_id, "track": t.id,
            "first_seen": iso_utc(t.first_seen), "last_seen": iso_utc(t.last_seen), "best_at": iso_utc(best.captured_at),
            "stream_pts": round(float(best.stream_pts), 3), "bbox": best.box.as_list(), "width_px": int(best.box.w),
            "height_px": int(best.box.h), "frames": int(t.frames), "det_conf": round(float(best.box.confidence), 3),
            "sharpness": round(float(best.sharp), 1), "ocr_runs": int(t.ocr_runs), "ocr_min_w": int(self.settings.ocr_min_w),
            "plate_norm": (valid or plates or [""])[-1], "is_valid_format": bool(valid), "kind": kind,
        }
        rel = self.evidence.write(cam.cfg.id, best.crop, record, best.captured_at)
        if rel:
            cam.evidence_files += 1
        log.info("camera %s: vehicle t%d w=%dpx frames=%d %s..%s plate=%s%s", cam.cfg.id, t.id, best.box.w, t.frames,
                 iso_utc(t.first_seen)[11:23], iso_utc(t.last_seen)[11:23], record["plate_norm"] or "-",
                 f" evidence={rel}" if rel else "")

    def _track_boxes(self, cam: CameraWorker, frame: Frame, boxes: list[Box]) -> None:
        touched, ended = cam.tracker.update(boxes, frame.image, frame.captured_at, frame.stream_pts, frame.frame_index)
        for t in touched:
            if cam.tracker.ready_for_ocr(t, self.settings.ocr_min_w):
                self._ocr_track(cam, t, frame)
        for t in ended:
            self._finish_track(cam, t, frame)

    def _ocr_boxes(self, cam: CameraWorker, frame: Frame, boxes: list[Box]) -> None:
        """Per-frame OCR (``ANPR_BEST_SHOT=0``): every box at least ``ANPR_OCR_MIN_W`` wide is read."""
        for box in boxes:
            if box.w < self.settings.ocr_min_w:
                continue
            crop = crop_with_context(frame.image, box)
            if crop.size == 0:
                continue
            self._ocr_crop(cam, crop, box, frame.captured_at, frame.stream_pts, frame.frame_index, frame.image)

    def process_frame(self, cam: CameraWorker, frame: Frame) -> None:
        boxes = self._detect(cam, frame)
        if self.settings.best_shot:
            self._track_boxes(cam, frame, boxes)
        else:
            self._ocr_boxes(cam, frame, boxes)

        if self.objects is not None and cam.counter is not None:
            if cam.frames_processed % max(1, self.settings.object_every_n) == 0:
                t3 = time.perf_counter()
                dets = self.objects.detect(frame.image)
                finished, events = cam.counter.update(dets, frame.captured_at, frame.image.shape[:2])
                self.timing["objects_ms"] += (time.perf_counter() - t3) * 1000
                self.timing["object_calls"] += 1
                cam.pending.object_counts.extend(finished)
                for ev in events:
                    cam.pending.events.append((ev, jpeg_under(frame.image, FRAME_MAX_BYTES, quality=80)))
                    log.info("camera %s: intrusion - %s", cam.cfg.id, ev["note"])

        if self.settings.mode == "live":
            now = time.monotonic()
            if now - cam.last_snapshot_mono >= self.settings.snapshot_interval_s:
                cam.last_snapshot_mono = now
                self._snapshot(cam, frame)
        cam.frames_processed += 1
        self.timing["frames"] += 1

    def _snapshot(self, cam: CameraWorker, frame: Frame) -> None:
        jpeg = jpeg_under(resize_width(frame.image, self.settings.snapshot_width), SNAPSHOT_MAX_BYTES, quality=70)
        captured = iso_utc(frame.captured_at)
        cid = cam.cfg.id
        self.sender.submit_latest(f"snapshot:{cid}", Job("snapshot", cid, lambda: self.client.post_snapshot(cid, captured, jpeg)))

    # ---- voting / sightings ----------------------------------------------------
    def _accept_reads(self, cam: CameraWorker, reads: list[VotedRead]) -> None:
        for read in reads:
            if read.confidence < self.settings.min_read_conf:
                log.debug("camera %s: %s below ANPR_MIN_READ_CONF (%.2f)", cam.cfg.id, read.plate_norm, read.confidence)
                continue
            if not read.is_valid_format and self._invalid_capped(cam):
                log.debug("camera %s: invalid-format read %s dropped (ANPR_INVALID_READS_PER_MIN=%d reached)",
                          cam.cfg.id, read.plate_norm, self.settings.invalid_reads_per_min)
                continue
            crop_field = f"crop_{len(cam.pending.reads)}"
            s = self.sightings.add_read(read, crop_field, None)
            if s.best_changed and s.best_frame_jpeg is None and read.frame is not None and s.best_read_captured_at == read.captured_at:
                frame_img = read.frame if read.frame.shape[1] <= FRAME_JPEG_WIDTH else resize_width(read.frame, FRAME_JPEG_WIDTH)
                s.best_frame_jpeg = jpeg_under(frame_img, FRAME_MAX_BYTES, quality=80)
            read.frame = None
            cam.pending.reads.append(read)
            cam.reads += 1
            log.info("camera %s: read %s conf=%.2f votes=%d valid=%s sighting=%s", cam.cfg.id, read.plate_norm,
                     read.confidence, read.votes, read.is_valid_format, s.key)

    def _invalid_capped(self, cam: CameraWorker) -> bool:
        """True when this camera already posted ``ANPR_INVALID_READS_PER_MIN`` invalid-format reads this minute.

        Invalid-format reads (OSD fragments, signboard text, half plates) never raise alerts, but
        hundreds per minute from one camera drown the reads list and the dashboard counters; the
        first N per minute are kept as evidence of what the OCR saw, the rest are counted and dropped.
        Valid plates are never capped.
        """
        cap = int(self.settings.invalid_reads_per_min)
        if cap <= 0:
            return False
        minute = int(time.monotonic() // 60)
        bucket, count = cam.invalid_minute
        if bucket != minute:
            bucket, count = minute, 0
        if count >= cap:
            cam.invalid_minute = (bucket, count)
            cam.invalid_suppressed += 1
            return True
        cam.invalid_minute = (bucket, count + 1)
        return False

    def _handle_discontinuity(self, cam: CameraWorker, reason: str, before: float | None, after: float | None) -> None:
        cid = cam.cfg.id
        log.info("camera %s: discontinuity - %s", cid, reason)
        self._flush_tracks(cam)
        self._accept_reads(cam, self.voter.flush(cid))
        self.sightings.close_all(cid)
        if cam.counter is not None:
            cam.counter.tracker.tracks.clear()
        event: dict[str, Any] = {"type": "loop_reset", "occurred_at": datetime.now(timezone.utc), "note": reason}
        if before is not None and after is not None:
            event["stream_pts_before"] = round(before, 3)
            event["stream_pts_after"] = round(after, 3)
        cam.pending.events.append((event, None))

    def _tick_time_based(self, now: datetime) -> None:
        """Vote-window expiry, sighting closure and batch flushes for every camera."""
        for cam in list(self.cameras.values()):
            for reason, before, after in cam.take_discontinuities():
                self._handle_discontinuity(cam, reason, before, after)
            self._accept_reads(cam, self.voter.expire(now, cam.cfg.id))
        self.sightings.expire(now)
        mono = time.monotonic()
        for cam in list(self.cameras.values()):
            if mono - cam.last_flush_mono < self.settings.post_batch_s:
                continue
            cam.last_flush_mono = mono
            dirty = self.sightings.drain_dirty(cam.cfg.id)
            if dirty or not cam.pending.empty():
                self._flush(cam, dirty)
            elif cam.counter is not None and mono - cam.last_live_counts_mono >= LIVE_COUNTS_EVERY_S:
                if cam.counter.live_counts()["counts"]:
                    self._flush(cam, [])
                cam.last_live_counts_mono = mono

    # ---- batching ------------------------------------------------------------
    def _build_batch(self, cam: CameraWorker, sightings: list[Sighting]) -> tuple[dict[str, Any], dict[str, bytes]]:
        files: dict[str, bytes] = {}
        reads_payload = []
        for i, read in enumerate(cam.pending.reads):
            field_name = f"crop_{i}"
            files[field_name] = read.crop_jpeg
            reads_payload.append(read.to_payload(field_name))
        sightings_payload = []
        for i, s in enumerate(sightings):
            frame_field = None
            if s.best_changed and s.best_frame_jpeg:
                frame_field = f"frame_{i}"
                files[frame_field] = s.best_frame_jpeg
            sightings_payload.append(s.to_payload(frame_field))
        events_payload = []
        for i, (ev, jpeg) in enumerate(cam.pending.events):
            item = dict(ev)
            item["occurred_at"] = iso_utc(item["occurred_at"]) if isinstance(item["occurred_at"], datetime) else item["occurred_at"]
            if jpeg:
                name = f"event_frame_{i}"
                files[name] = jpeg
                item["frame_file"] = name
            events_payload.append(item)
        payload: dict[str, Any] = {
            "worker_id": self.settings.worker_id,
            "mode": self.settings.mode,
            "camera_id": cam.cfg.id,
            "camera_external_id": cam.cfg.external_id,
            "sent_at": iso_utc(),
            "reads": reads_payload,
            "sightings": sightings_payload,
            "object_counts": list(cam.pending.object_counts),
            "events": events_payload,
        }
        if cam.counter is not None:
            payload["live_counts"] = cam.counter.live_counts()
        return payload, files

    def _flush(self, cam: CameraWorker, sightings: list[Sighting]) -> None:
        payload, files = self._build_batch(cam, sightings)
        self.sightings.finalize_batch(sightings)
        cam.pending = PendingBatch()
        cam.last_live_counts_mono = time.monotonic()
        cid = cam.cfg.id
        self.sender.submit(Job("detections", cid, lambda: self.client.post_detections(payload, files)))
        log.debug("camera %s: batch queued (%d reads, %d sightings, %d counts, %d events)", cid,
                  len(payload["reads"]), len(payload["sightings"]), len(payload["object_counts"]), len(payload["events"]))

    def _flush_tracks(self, cam: CameraWorker) -> None:
        """End every open plate track of a camera (discontinuity, stop): final OCR + vehicle evidence."""
        for t in cam.tracker.flush():
            if t.best is None:
                continue
            self._finish_track(cam, t, None)      # no current frame: the read carries no sighting frame

    def _finish_camera(self, cam: CameraWorker, note: str) -> None:
        """Flush everything a camera still holds (stop / shutdown)."""
        cid = cam.cfg.id
        self._flush_tracks(cam)
        self._accept_reads(cam, self.voter.flush(cid))
        self.sightings.close_all(cid)
        if cam.counter is not None:
            cam.pending.object_counts.extend(cam.counter.flush())
        dirty = self.sightings.drain_dirty(cid)
        if dirty or not cam.pending.empty():
            self._flush(cam, dirty)

    # ---- stats -----------------------------------------------------------------
    def _log_stats(self) -> None:
        t = self.timing
        frames = max(1, t["frames"])
        elapsed = max(1e-6, time.monotonic() - self._loop_started)
        per_cam = {
            cid: {"state": c.decoder.state, "fps": round(c.decoder.stats.fps_actual, 2), "processed": c.frames_processed,
                  "dropped": c.dropped, "reads": c.reads, "restarts": c.decoder.stats.restarts,
                  "suppressed": c.suppression(), "vehicles": c.vehicle_stats()}
            for cid, c in self.cameras.items()
        }
        log.info("stats: frames=%d (%.2f/s) detect=%.1f ms/frame ocr=%s %.1f ms/call (%d) objects=%.1f ms/call (%d) "
                 "open_buckets=%d open_sightings=%d evidence=%s sender=%s cameras=%s",
                 t["frames"], t["frames"] / elapsed, t["detect_ms"] / frames, self.ocr.name,
                 t["ocr_ms"] / max(1, t["ocr_calls"]), t["ocr_calls"],
                 t["objects_ms"] / max(1, t["object_calls"]), t["object_calls"],
                 self.voter.open_buckets(), self.sightings.open_count(), json.dumps(self.evidence.stats()),
                 self.sender.stats, json.dumps(per_cam))

    # ---- main loop -----------------------------------------------------------
    def run(self) -> int:
        s = self.settings
        log.info("starting worker %s mode=%s detector=%s ocr=%s best_shot=%s ocr_min_w=%d detect_width=%d objects=%s cpu=%s dry_run=%s",
                 s.worker_id, s.mode, self.detector.name, self.ocr.name, s.best_shot, s.ocr_min_w, s.detect_width,
                 self.objects is not None, s.cpu, s.api_dry_run)
        status = detector_status(self.detector, s.detector)
        if status["degraded"]:
            log.error("worker %s runs DEGRADED: ANPR_DETECTOR=%s requested but the active detector is %s "
                      "(ONNX weights missing or corrupt); the heartbeat reports detector=%s",
                      s.worker_id, s.detector, self.detector.name, self.detector.name)
        cameras = self._initial_cameras()
        if not cameras:
            log.warning("no cameras to process (mode=%s); idling until the config lists some", s.mode)
        self.reconcile(cameras)
        self.sender.start()
        control = threading.Thread(target=self._control_loop, name="control", daemon=True)
        control.start()
        self._loop_started = time.monotonic()

        exit_code = 0
        try:
            while not self.stop_event.is_set():
                with self._desired_lock:
                    desired, self._desired = self._desired, None
                if desired is not None:
                    self.reconcile(desired)
                busy = False
                for cam in list(self.cameras.values()):
                    frame = cam.take_frame()
                    if frame is None:
                        continue
                    busy = True
                    try:
                        self.process_frame(cam, frame)
                    except Exception:  # noqa: BLE001 - one bad frame must not stop the worker
                        log.exception("camera %s: frame processing failed", cam.cfg.id)
                self._tick_time_based(datetime.now(timezone.utc))
                if self.sender.fatal is not None:
                    exit_code = 3
                    break
                if time.monotonic() - self._last_stats_log >= STATS_LOG_EVERY_S:
                    self._last_stats_log = time.monotonic()
                    self._log_stats()
                if self.static and self.cameras and all(c.decoder.state == "stopped" for c in self.cameras.values()):
                    log.info("all file sources finished")
                    break
                if not busy:
                    time.sleep(0.005)
        finally:
            self.shutdown()
            self._log_stats()
        return exit_code

    def shutdown(self) -> None:
        self.stop_event.set()
        for cam in list(self.cameras.values()):
            try:
                self._finish_camera(cam, note="shutdown")
            except Exception:  # noqa: BLE001
                log.exception("camera %s: flush on shutdown failed", cam.cfg.id)
            cam.stop()
        if not self.settings.api_dry_run:
            body = self.heartbeat_body()
            body["cameras"] = [{**e, "state": "stopped"} for e in body["cameras"]]
            try:
                self.client.post_heartbeat(body)
            except ApiError:
                pass
        self.sender.stop(flush_timeout_s=15.0)
        log.info("worker stopped; sender stats %s", self.sender.stats)


def ensure_bind_now(argv: list[str] | None) -> None:
    """Re-exec the worker with ``LD_BIND_NOW=1`` on Linux when it is not set.

    paddlepaddle 2.6 exports its own zlib symbols from ``libpaddle.so`` (loaded RTLD_GLOBAL); with
    lazy binding the system ``libz.so.1`` resolves its internal ``inflateReset2`` call to Paddle's
    incompatible copy and every gzip/tar operation after ``import paddle`` segfaults. Binding at
    load time avoids the clash. The Docker image sets the variable; this is the safety net for
    other environments. ``ANPR_NO_REEXEC=1`` disables it.
    """
    if sys.platform != "linux" or os.environ.get("LD_BIND_NOW") == "1" or os.environ.get("ANPR_NO_REEXEC") == "1":
        return
    env = {**os.environ, "LD_BIND_NOW": "1"}
    args = list(argv) if argv is not None else sys.argv[1:]
    os.execve(sys.executable, [sys.executable, "-m", "anpr.pipeline", *args], env)


def main(argv: list[str] | None = None) -> int:
    ensure_bind_now(argv)
    try:
        settings = load_settings(argv)
    except (ValueError, OSError) as exc:
        print(json.dumps({"level": "ERROR", "msg": f"configuration error: {exc}"}), file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    log.info("settings: %s", json.dumps(redacted(settings), default=str))
    if not settings.sources and settings.api_dry_run:
        log.warning("API_DRY_RUN=1 without static sources: the config endpoint is still queried")
    try:
        pipeline = Pipeline(settings)
    except Exception as exc:  # noqa: BLE001
        log.exception("worker failed to initialise: %s", exc)
        return 2
    configure_logging(settings.log_level)   # model imports (paddleocr) alter the root logger level

    def _stop(signum: int, _frame: Any) -> None:
        log.info("signal %s received - shutting down", signum)
        pipeline.stop_event.set()
        for cam in list(pipeline.cameras.values()):
            cam.decoder.request_stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    return pipeline.run()


if __name__ == "__main__":
    sys.exit(main())
