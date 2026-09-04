"""Object counting and intrusion zones (plan 5.5 step 4b, CONTRACT.md section 7.7).

* Detector: YOLOX-s COCO (Apache-2.0, Megvii) ONNX at 640x640 through onnxruntime; classes kept:
  person, bicycle, car, motorcycle, bus, truck; score = objectness x class probability.
* Tracker: greedy IoU (>= 0.3) association across consecutive detection rounds; a track that is
  unmatched for ``max_missed`` rounds is dropped. Each track is counted **once per minute per
  class**, so a parked car is one count per minute, not sixty.
* Counts are aggregated per (camera, UTC minute, class); when the minute rolls over the finished
  minute is emitted once (``object_counts``), the running minute is reported as ``live_counts``.
* Zones: polygon in normalised 0..1 coordinates x frame size; a track centroid inside an active
  zone for >= ``dwell_s`` fires one ``intrusion`` event per track per zone, with the frame.
  ``active_from``/``active_to`` are IST clock times (``null`` = always).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import cv2
import numpy as np

log = logging.getLogger("anpr.objects")

IST = timezone(timedelta(hours=5, minutes=30))
COCO_KEEP = {0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
CLASSES = ("person", "bicycle", "car", "motorcycle", "bus", "truck")
VEHICLE_CLASSES = {"bicycle", "car", "motorcycle", "bus", "truck"}


@dataclass(frozen=True)
class Detection:
    cls: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def centroid(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0


def box_iou(a: Detection | tuple[float, float, float, float], b: Detection | tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = (a.x1, a.y1, a.x2, a.y2) if isinstance(a, Detection) else a
    bx1, by1, bx2, by2 = (b.x1, b.y1, b.x2, b.y2) if isinstance(b, Detection) else b
    ix1, iy1, ix2, iy2 = max(ax1, bx1), max(ay1, by1), min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# YOLOX-s ONNX
# ---------------------------------------------------------------------------
def _nms(boxes: np.ndarray, scores: np.ndarray, thr: float) -> list[int]:
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        xx1, yy1 = np.maximum(x1[i], x1[order[1:]]), np.maximum(y1[i], y1[order[1:]])
        xx2, yy2 = np.minimum(x2[i], x2[order[1:]]), np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1 + 1) * np.maximum(0.0, yy2 - yy1 + 1)
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(ovr <= thr)[0] + 1]
    return keep


class YoloxDetector:
    """YOLOX-s COCO on onnxruntime (input 1x3x640x640, BGR, no normalisation - YOLOX >= 0.1.1rc0)."""

    def __init__(self, model_path: str, conf: float = 0.35, cpu: bool = True, threads: int = 2, nms_thr: float = 0.45) -> None:
        import onnxruntime as ort

        if not os.path.exists(model_path):
            raise FileNotFoundError(model_path)
        providers = ["CPUExecutionProvider"]
        if not cpu and "CUDAExecutionProvider" in ort.get_available_providers():
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, int(threads))
        opts.log_severity_level = 3
        self.session = ort.InferenceSession(model_path, sess_options=opts, providers=providers)
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.size = int(inp.shape[-1]) if isinstance(inp.shape[-1], int) else 640
        self.conf = float(conf)
        self.nms_thr = float(nms_thr)
        self._grids, self._strides = self._make_grids(self.size)
        log.info("YOLOX object detector %s (%dx%d) on %s", os.path.basename(model_path), self.size, self.size, providers[0])

    @staticmethod
    def _make_grids(size: int) -> tuple[np.ndarray, np.ndarray]:
        grids, strides = [], []
        for stride in (8, 16, 32):
            n = size // stride
            xv, yv = np.meshgrid(np.arange(n), np.arange(n))
            grid = np.stack((xv, yv), 2).reshape(1, -1, 2)
            grids.append(grid)
            strides.append(np.full((1, grid.shape[1], 1), stride))
        return np.concatenate(grids, 1).astype(np.float32), np.concatenate(strides, 1).astype(np.float32)

    def _preprocess(self, image: np.ndarray) -> tuple[np.ndarray, float]:
        padded = np.full((self.size, self.size, 3), 114, dtype=np.uint8)
        r = min(self.size / image.shape[0], self.size / image.shape[1])
        new_w, new_h = int(image.shape[1] * r), int(image.shape[0] * r)
        resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        padded[:new_h, :new_w] = resized
        tensor = padded.transpose(2, 0, 1).astype(np.float32)
        return np.ascontiguousarray(tensor[None]), r

    def detect(self, image: np.ndarray) -> list[Detection]:
        tensor, r = self._preprocess(image)
        try:
            out = self.session.run(None, {self.input_name: tensor})[0]
        except Exception as exc:  # noqa: BLE001
            log.warning("YOLOX inference failed: %s", exc)
            return []
        preds = np.asarray(out, dtype=np.float32)
        preds[..., :2] = (preds[..., :2] + self._grids) * self._strides
        preds[..., 2:4] = np.exp(preds[..., 2:4]) * self._strides
        p = preds[0]
        boxes = np.empty((p.shape[0], 4), dtype=np.float32)
        boxes[:, 0] = p[:, 0] - p[:, 2] / 2
        boxes[:, 1] = p[:, 1] - p[:, 3] / 2
        boxes[:, 2] = p[:, 0] + p[:, 2] / 2
        boxes[:, 3] = p[:, 1] + p[:, 3] / 2
        boxes /= r
        scores = p[:, 4:5] * p[:, 5:]
        h, w = image.shape[:2]
        dets: list[Detection] = []
        for cls_id, name in COCO_KEEP.items():
            cls_scores = scores[:, cls_id]
            mask = cls_scores >= self.conf
            if not mask.any():
                continue
            cb, cs = boxes[mask], cls_scores[mask]
            for i in _nms(cb, cs, self.nms_thr):
                x1, y1, x2, y2 = cb[i]
                dets.append(Detection(name, float(cs[i]), max(0.0, float(x1)), max(0.0, float(y1)),
                                      min(float(w), float(x2)), min(float(h), float(y2))))
        return dets


# ---------------------------------------------------------------------------
# Tracking and counting
# ---------------------------------------------------------------------------
@dataclass
class Track:
    id: int
    cls: str
    box: tuple[float, float, float, float]
    last_round: int
    first_seen: datetime
    last_seen: datetime
    zone_entered: dict[int, datetime] = field(default_factory=dict)
    zone_fired: set[int] = field(default_factory=set)

    @property
    def centroid(self) -> tuple[float, float]:
        return (self.box[0] + self.box[2]) / 2.0, (self.box[1] + self.box[3]) / 2.0


class CentroidTracker:
    """Greedy IoU association (>= ``iou_thr``) between consecutive detection rounds."""

    def __init__(self, iou_thr: float = 0.3, max_missed: int = 3) -> None:
        self.iou_thr = float(iou_thr)
        self.max_missed = int(max_missed)
        self.tracks: dict[int, Track] = {}
        self._next_id = 1
        self._round = 0

    def update(self, dets: list[Detection], now: datetime) -> list[Track]:
        self._round += 1
        pairs: list[tuple[float, int, int]] = []
        track_ids = list(self.tracks)
        for ti, tid in enumerate(track_ids):
            t = self.tracks[tid]
            for di, d in enumerate(dets):
                v = box_iou(t.box, d)
                if v >= self.iou_thr:
                    pairs.append((v, ti, di))
        pairs.sort(reverse=True)
        used_t: set[int] = set()
        used_d: set[int] = set()
        matched: list[Track] = []
        for v, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            t = self.tracks[track_ids[ti]]
            d = dets[di]
            t.box = (d.x1, d.y1, d.x2, d.y2)
            t.cls = d.cls
            t.last_round = self._round
            t.last_seen = now
            matched.append(t)
        for di, d in enumerate(dets):
            if di in used_d:
                continue
            t = Track(self._next_id, d.cls, (d.x1, d.y1, d.x2, d.y2), self._round, now, now)
            self._next_id += 1
            self.tracks[t.id] = t
            matched.append(t)
        for tid in list(self.tracks):
            if self._round - self.tracks[tid].last_round > self.max_missed:
                del self.tracks[tid]
        return matched


def minute_start(dt: datetime) -> datetime:
    dt = dt.astimezone(timezone.utc)
    return dt.replace(second=0, microsecond=0)


def _parse_clock(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        hh, mm = str(value).split(":")[:2]
        return int(hh), int(mm)
    except ValueError:
        return None


@dataclass
class Zone:
    id: int
    name: str
    polygon: list[tuple[float, float]]           # normalised 0..1
    classes: set[str]
    dwell_s: float
    priority: str
    active_from: tuple[int, int] | None
    active_to: tuple[int, int] | None

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "Zone":
        poly = [(float(p[0]), float(p[1])) for p in cfg.get("polygon") or []]
        return cls(
            id=int(cfg["id"]),
            name=str(cfg.get("name") or f"zone {cfg['id']}"),
            polygon=poly,
            classes={str(c) for c in (cfg.get("classes") or [])},
            dwell_s=float(cfg.get("dwell_s") or 2.0),
            priority=str(cfg.get("priority") or "medium"),
            active_from=_parse_clock(cfg.get("active_from")),
            active_to=_parse_clock(cfg.get("active_to")),
        )

    def is_active(self, now: datetime) -> bool:
        if self.active_from is None or self.active_to is None:
            return True
        ist = now.astimezone(IST)
        cur = ist.hour * 60 + ist.minute
        start = self.active_from[0] * 60 + self.active_from[1]
        end = self.active_to[0] * 60 + self.active_to[1]
        if start <= end:
            return start <= cur < end
        return cur >= start or cur < end        # window crossing midnight, e.g. 22:00 -> 06:00

    def contains(self, cx: float, cy: float, width: int, height: int) -> bool:
        if len(self.polygon) < 3:
            return False
        pts = np.array([(x * width, y * height) for x, y in self.polygon], dtype=np.float32)
        return cv2.pointPolygonTest(pts, (float(cx), float(cy)), False) >= 0


class ObjectCounter:
    """Per-camera state: tracker, per-minute unique counts and zone dwell timers."""

    def __init__(self, camera_id: int, iou_thr: float = 0.3) -> None:
        self.camera_id = camera_id
        self.tracker = CentroidTracker(iou_thr=iou_thr)
        self.current_minute: datetime | None = None
        self.seen: dict[str, set[int]] = {c: set() for c in CLASSES}
        self.zones: list[Zone] = []

    def set_zones(self, zones: list[dict[str, Any]]) -> None:
        self.zones = [Zone.from_config(z) for z in zones or [] if z.get("polygon")]

    def _roll_minute(self, now: datetime) -> list[dict[str, Any]]:
        from anpr.client import iso_utc

        minute = minute_start(now)
        finished: list[dict[str, Any]] = []
        if self.current_minute is not None and minute != self.current_minute:
            for cls in CLASSES:
                if self.seen[cls]:
                    finished.append({"minute": iso_utc(self.current_minute), "class": cls, "count": len(self.seen[cls])})
            self.seen = {c: set() for c in CLASSES}
        self.current_minute = minute
        return finished

    def live_counts(self) -> dict[str, Any]:
        from anpr.client import iso_utc

        return {
            "minute": iso_utc(self.current_minute or minute_start(datetime.now(timezone.utc))),
            "counts": {c: len(s) for c, s in self.seen.items() if s},
        }

    def flush(self) -> list[dict[str, Any]]:
        """Emit the running minute as final (shutdown)."""
        from anpr.client import iso_utc

        out = []
        if self.current_minute is not None:
            for cls in CLASSES:
                if self.seen[cls]:
                    out.append({"minute": iso_utc(self.current_minute), "class": cls, "count": len(self.seen[cls])})
        self.seen = {c: set() for c in CLASSES}
        return out

    def update(self, dets: list[Detection], now: datetime, frame_shape: tuple[int, int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Feed one detection round; returns (finished_minute_counts, intrusion_events)."""
        finished = self._roll_minute(now)
        tracks = self.tracker.update(dets, now)
        for t in tracks:
            if t.cls in self.seen:
                self.seen[t.cls].add(t.id)
        events: list[dict[str, Any]] = []
        if self.zones:
            h, w = frame_shape
            active = [z for z in self.zones if z.is_active(now)]
            for t in tracks:
                cx, cy = t.centroid
                for z in active:
                    if z.classes and t.cls not in z.classes:
                        continue
                    if z.contains(cx, cy, w, h):
                        entered = t.zone_entered.setdefault(z.id, now)
                        dwell = (now - entered).total_seconds()
                        if dwell >= z.dwell_s and z.id not in t.zone_fired:
                            t.zone_fired.add(z.id)
                            events.append({
                                "type": "intrusion",
                                "occurred_at": now,
                                "note": f"{t.cls} in zone '{z.name}' for {dwell:.1f} s",
                                "zone_id": z.id,
                                "class": t.cls,
                                "dwell_s": round(dwell, 1),
                            })
                    else:
                        t.zone_entered.pop(z.id, None)
        return finished, events
