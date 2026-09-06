"""Plate detectors (CONTRACT.md section 7.7, ``ANPR_DETECTOR=auto|onnx|contour``).

* ``onnx``    - open-image-models ``yolo-v9-t-384-license-plates-end2end.onnx`` (Apache-2.0) through
              onnxruntime. The model is end-to-end (NMS inside); each output row is
              ``[batch_index, x1, y1, x2, y2, class_id, score]`` in letterboxed 384x384 coordinates.
* ``contour`` - classical CV: bright regions (fixed + adaptive threshold) -> external contours ->
              bounding boxes filtered by aspect ratio ``0.15 <= h/w <= 0.6``, ``w >= ANPR_MIN_PLATE_W``
              and fill ratio >= 0.6. Reads the synthetic plates on the CPU-only laptop and is a
              weak, weight-free fallback on real feeds.
* ``auto``    - ONNX first; the contour detector runs only for frames where ONNX returned no box.

All boxes are ``(x, y, w, h)`` in the decoded frame's pixel coordinates.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

from anpr.weights.download import WeightStatus, verify_weight_file

log = logging.getLogger("anpr.detector")

#: Heartbeat ``detector`` value when ``auto`` was requested but the ONNX model could not be loaded.
#: Kept at 16 characters: the API stores it in ``anpr_workers.detector VARCHAR(16)``.
CONTOUR_FALLBACK = "contour-fallback"


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int
    confidence: float
    source: str

    @property
    def area(self) -> int:
        return self.w * self.h

    def as_list(self) -> list[int]:
        return [int(self.x), int(self.y), int(self.w), int(self.h)]


class PlateDetector(Protocol):
    name: str

    def detect(self, image: np.ndarray) -> list[Box]: ...


def clip_box(x1: float, y1: float, x2: float, y2: float, width: int, height: int) -> tuple[int, int, int, int] | None:
    x1i, y1i = max(0, int(round(x1))), max(0, int(round(y1)))
    x2i, y2i = min(width, int(round(x2))), min(height, int(round(y2)))
    if x2i - x1i < 4 or y2i - y1i < 4:
        return None
    return x1i, y1i, x2i - x1i, y2i - y1i


def iou(a: Box, b: Box) -> float:
    ix1, iy1 = max(a.x, b.x), max(a.y, b.y)
    ix2, iy2 = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def letterbox(image: np.ndarray, size: int, color: tuple[int, int, int] = (114, 114, 114)) -> tuple[np.ndarray, float, float, float]:
    """Resize keeping aspect ratio and pad to ``size`` x ``size``; returns (img, ratio, dw, dh)."""
    h, w = image.shape[:2]
    r = min(size / h, size / w)
    new_w, new_h = int(round(w * r)), int(round(h * r))
    dw, dh = (size - new_w) / 2, (size - new_h) / 2
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR) if (new_w, new_h) != (w, h) else image
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)
    return padded, r, dw, dh


class OnnxPlateDetector:
    """YOLO-v9-t licence-plate detector (open-image-models) on onnxruntime."""

    name = "onnx"

    def __init__(self, model_path: str, conf: float = 0.4, min_w: int = 60, cpu: bool = True, threads: int = 2) -> None:
        import onnxruntime as ort

        if not os.path.exists(model_path):
            raise FileNotFoundError(model_path)
        available = ort.get_available_providers()
        providers = ["CPUExecutionProvider"]
        if not cpu and "CUDAExecutionProvider" in available:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, int(threads))
        opts.log_severity_level = 3
        self.session = ort.InferenceSession(model_path, sess_options=opts, providers=providers)
        self.providers = self.session.get_providers()
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        shape = inp.shape
        self.size = int(shape[-1]) if isinstance(shape[-1], int) else 384
        self.conf = float(conf)
        self.min_w = int(min_w)
        log.info("ONNX plate detector %s (%dx%d) on %s", os.path.basename(model_path), self.size, self.size, self.providers[0])

    @property
    def gpu(self) -> bool:
        return self.providers[0] == "CUDAExecutionProvider"

    def _preprocess(self, image: np.ndarray) -> tuple[np.ndarray, float, float, float]:
        padded, r, dw, dh = letterbox(image, self.size)
        tensor = padded[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
        return np.ascontiguousarray(tensor[None]), r, dw, dh

    def detect(self, image: np.ndarray) -> list[Box]:
        h, w = image.shape[:2]
        tensor, r, dw, dh = self._preprocess(image)
        try:
            out = self.session.run(None, {self.input_name: tensor})[0]
        except Exception as exc:  # noqa: BLE001 - e.g. CoreML/TensorRT empty-output quirks
            log.warning("ONNX detector inference failed: %s", exc)
            return []
        preds = np.asarray(out)
        if preds.ndim == 3:
            preds = preds[0]
        if preds.ndim != 2 or preds.shape[0] == 0:
            return []
        boxes: list[Box] = []
        for row in preds:
            if row.shape[0] >= 7:
                x1, y1, x2, y2, score = row[1], row[2], row[3], row[4], row[6]
            elif row.shape[0] == 6:      # [x1, y1, x2, y2, score, class]
                x1, y1, x2, y2, score = row[0], row[1], row[2], row[3], row[4]
            else:
                continue
            if score < self.conf:
                continue
            rect = clip_box((x1 - dw) / r, (y1 - dh) / r, (x2 - dw) / r, (y2 - dh) / r, w, h)
            if rect is None or rect[2] < self.min_w:
                continue
            boxes.append(Box(*rect, confidence=float(score), source="onnx"))
        boxes.sort(key=lambda b: -b.confidence)
        return boxes


class ContourPlateDetector:
    """Bright-rectangle detector for synthetic plates and as a no-weights fallback."""

    name = "contour"

    def __init__(self, min_w: int = 60, min_fill: float = 0.6, aspect: tuple[float, float] = (0.15, 0.6),
                 bright: int = 190, max_boxes: int = 6, max_w_fraction: float = 0.6) -> None:
        self.min_w = int(min_w)
        self.min_fill = float(min_fill)
        self.aspect = aspect
        self.bright = int(bright)
        self.max_boxes = int(max_boxes)
        self.max_w_fraction = float(max_w_fraction)

    def _candidates(self, mask: np.ndarray, width: int, height: int, tag: str) -> list[Box]:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out: list[Box] = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w < self.min_w or h < 12 or w > width * self.max_w_fraction or h > height * 0.5:
                continue
            ratio = h / float(w)
            if not self.aspect[0] <= ratio <= self.aspect[1]:
                continue
            rect_area = float(w * h)
            fill = float(cv2.countNonZero(mask[y:y + h, x:x + w])) / rect_area
            if fill < self.min_fill:
                continue
            rectangularity = cv2.contourArea(cnt) / rect_area
            if rectangularity < 0.7:
                continue
            conf = min(1.0, 0.5 * fill + 0.5 * rectangularity)
            out.append(Box(x, y, w, h, confidence=float(conf), source=tag))
        return out

    def detect(self, image: np.ndarray) -> list[Box]:
        h, w = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        _, fixed = cv2.threshold(gray, self.bright, 255, cv2.THRESH_BINARY)
        boxes = self._candidates(fixed, w, h, "contour")
        if not boxes:
            adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 41, -20)
            adaptive = cv2.morphologyEx(adaptive, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            boxes = self._candidates(adaptive, w, h, "contour")
        boxes.sort(key=lambda b: -b.area)
        kept: list[Box] = []
        for b in boxes:
            if all(iou(b, k) < 0.3 for k in kept):
                kept.append(b)
            if len(kept) >= self.max_boxes:
                break
        return kept


def tile_grid(width: int, height: int, tile: int, overlap: float) -> list[tuple[int, int, int, int]]:
    """``(x, y, w, h)`` tiles of ``tile`` px sharing ``overlap`` of their size with their neighbours.

    A frame no larger than the tile yields itself; the last row/column is shifted inwards so the
    grid always covers the frame edge (1920x1080 with 768 px tiles and 0.15 overlap -> 3 x 2).
    """
    if tile <= 0 or (width <= tile and height <= tile):
        return [(0, 0, width, height)]
    step = max(1, int(tile * (1.0 - overlap)))
    xs = list(range(0, max(0, width - tile) + 1, step))
    ys = list(range(0, max(0, height - tile) + 1, step))
    if xs[-1] + tile < width:
        xs.append(max(0, width - tile))
    if ys[-1] + tile < height:
        ys.append(max(0, height - tile))
    return [(x, y, min(tile, width - x), min(tile, height - y)) for y in ys for x in xs]


class TiledDetector:
    """Run the wrapped detector on overlapping tiles and on the whole frame; merge duplicates.

    The ONNX model sees a 384 x 384 letterbox, so a plate 60 px wide in a 1920 px wide-angle
    junction view (the organiser sandbox, plates 30-110 px at 1080p) becomes 12 px in the model
    input and is never found. Cutting the frame into ``tile`` px tiles (``ANPR_DET_TILE``, 0 = off)
    raises the effective resolution by ``width / tile`` at the cost of one inference per tile
    (about 9 ms each on the laptop CPU); the whole-frame pass keeps near/large plates.
    Boxes from tiles are dropped when they cannot be a plate (wider or taller than half the tile,
    or with an aspect ratio outside 0.12 <= h/w <= 0.9 - smeared night tiles occasionally yield one
    full-tile box with a high score); duplicates across tiles are merged by IoU >= 0.4, keeping the
    most confident. The heartbeat keeps the inner detector's name (``onnx``/``auto``/...).
    """

    def __init__(self, inner: PlateDetector, tile: int, overlap: float = 0.15, whole_frame: bool = True) -> None:
        self.inner = inner
        self.tile = int(tile)
        self.overlap = min(0.5, max(0.0, float(overlap)))
        self.whole_frame = bool(whole_frame)
        self.name = inner.name

    @property
    def primary(self) -> PlateDetector:
        return getattr(self.inner, "primary", self.inner)

    @property
    def weights(self) -> WeightStatus | None:
        return getattr(self.inner, "weights", None)

    @staticmethod
    def plausible(b: Box, tile_w: int, tile_h: int) -> bool:
        return b.w <= 0.5 * tile_w and b.h <= 0.5 * tile_h and 0.12 <= b.h / max(1, b.w) <= 0.9

    def detect(self, image: np.ndarray) -> list[Box]:
        h, w = image.shape[:2]
        found: list[Box] = list(self.inner.detect(image)) if self.whole_frame else []
        tiles = tile_grid(w, h, self.tile, self.overlap)
        if len(tiles) > 1:
            for x, y, tw, th in tiles:
                for b in self.inner.detect(image[y:y + th, x:x + tw]):
                    if self.plausible(b, tw, th):
                        found.append(Box(b.x + x, b.y + y, b.w, b.h, b.confidence, b.source))
        found.sort(key=lambda b: -b.confidence)
        kept: list[Box] = []
        for b in found:
            if all(iou(b, k) < 0.4 for k in kept):
                kept.append(b)
        return kept


class StaticBoxFilter:
    """Per-camera suppression of boxes that sit at the same place frame after frame.

    Burnt-in clocks and camera names, shop signboards and phone numbers are high-contrast text the
    plate detectors box with confidence and PaddleOCR reads at 0.9+ ("2314:16", "75750 03008");
    on the organiser sandbox the contour detector posted 800 such reads in 12 minutes from one
    camera. A vehicle plate moves; an overlay does not. A box is *static* when a box overlapping
    it (IoU >= ``iou_thr``) was present in at least ``min_hits`` of the last ``window`` frames; the
    filter only starts judging once half the window (min 5 frames) has been seen. A parked vehicle
    is read during its first ``window`` frames and then muted, which the 15 s sighting close
    already implies. ``window`` 0 disables the filter (``ANPR_STATIC_BOX_WINDOW``).
    """

    def __init__(self, window: int = 20, min_hits: float = 0.8, iou_thr: float = 0.6) -> None:
        self.window = max(0, int(window))
        self.min_hits = float(min_hits)
        self.iou_thr = float(iou_thr)
        self.history: list[list[Box]] = []
        self.suppressed = 0

    def filter(self, boxes: list[Box]) -> list[Box]:
        if self.window <= 0:
            return list(boxes)
        kept: list[Box] = []
        seen = len(self.history)
        if seen >= max(5, self.window // 2):
            for b in boxes:
                hits = sum(1 for past in self.history if any(iou(b, p) >= self.iou_thr for p in past))
                if hits / seen >= self.min_hits:
                    self.suppressed += 1
                    continue
                kept.append(b)
        else:
            kept = list(boxes)
        self.history.append(list(boxes))
        del self.history[:-self.window]
        return kept


class OsdMask:
    """Per-camera exclusion of burnt-in OSD text (clocks, captions, camera names) and signboards.

    Two rules, both applied to detector boxes before OCR:

    * **Band** - a box whose centre lies in the top or bottom ``band`` fraction of the frame
      (default 8 %) is never a plate: that is where every organiser camera burns its clock and
      caption in (``ANPR_OSD_BAND``, 0 disables).
    * **Static text regions** - the frame is sampled every ``sample_s`` at 1/``scale`` resolution
      in grayscale; after ``warmup_s`` of samples (and every ``recompute_s`` afterwards, on a rolling
      window) pixels with a low temporal standard deviation **and** strong edges in at least 80 % of
      the samples are marked. Moving traffic has a high deviation and only transient edges; a dark
      road is constant but edge-free; burnt-in text and lit signboards are constant *and* sharp. Marked
      pixels are grown by one cell and grouped into connected components; components at least
      ``min_w`` px wide and no taller than ``max_h_frac`` of the frame become exclusion regions
      (``ANPR_OSD_WARMUP_S``, 0 disables). A box is excluded when at least ``cover`` of its area lies
      inside a region. A vehicle parked for the whole window is treated as static too, exactly as
      :class:`StaticBoxFilter` does after its window; both are refreshed, so a car that leaves
      frees the region again.

    Cost: one ``cvtColor`` + ``resize`` per sample (about 1 ms at 1280 px), one small ``np.std`` per
    recompute. State is a ring of ``max_samples`` tiny float32 images.
    """

    def __init__(self, band: float = 0.08, warmup_s: float = 30.0, *, scale: int = 8, sample_s: float = 0.5,
                 recompute_s: float = 30.0, max_samples: int = 90, std_thr: float = 4.0, edge_thr: float = 24.0,
                 min_w: int = 24, max_h_frac: float = 0.2, cover: float = 0.5) -> None:
        self.band = min(0.45, max(0.0, float(band)))
        self.warmup_s = max(0.0, float(warmup_s))
        self.scale = max(1, int(scale))
        self.sample_s = max(0.05, float(sample_s))
        self.recompute_s = max(1.0, float(recompute_s))
        self.max_samples = max(10, int(max_samples))
        self.std_thr = float(std_thr)
        self.edge_thr = float(edge_thr)
        self.min_w = int(min_w)
        self.max_h_frac = float(max_h_frac)
        self.cover = float(cover)
        self.regions: list[Box] = []
        self.suppressed_band = 0
        self.suppressed_region = 0
        self._samples: list[np.ndarray] = []
        self._first_sample_at: float | None = None
        self._last_sample_at = -1e9
        self._last_compute_at = -1e9
        self._shape: tuple[int, int] | None = None

    # ---- learning ------------------------------------------------------
    def observe(self, image: np.ndarray, now_mono: float) -> None:
        """Feed one decoded frame (BGR); cheap enough for every frame, samples every ``sample_s``."""
        if self.warmup_s <= 0 or now_mono - self._last_sample_at < self.sample_s:
            return
        h, w = image.shape[:2]
        if self._shape != (h, w):
            self._shape = (h, w)
            self._samples.clear()
            self.regions = []
            self._first_sample_at = None
        self._last_sample_at = now_mono
        if self._first_sample_at is None:
            self._first_sample_at = now_mono
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        small = cv2.resize(gray, (max(1, w // self.scale), max(1, h // self.scale)), interpolation=cv2.INTER_AREA)
        self._samples.append(small.astype(np.float32))
        del self._samples[:-self.max_samples]
        if now_mono - self._first_sample_at >= self.warmup_s and len(self._samples) >= 10 \
                and now_mono - self._last_compute_at >= self.recompute_s:
            self._last_compute_at = now_mono
            self.regions = self._compute()
            log.debug("osd regions (%d samples): %s", len(self._samples), [b.as_list() for b in self.regions])

    def _compute(self) -> list[Box]:
        assert self._shape is not None
        stack = np.stack(self._samples, axis=0)
        std = stack.std(axis=0)
        # Edge energy per sample, 20th percentile over time: burnt-in text is sharp in (almost) every
        # sample, whereas the road next to a busy lane only shows an edge while a vehicle passes
        # (an edge in the temporal *mean* would wrongly mark that static road cell).
        lap = np.stack([np.abs(cv2.Laplacian(s, cv2.CV_32F, ksize=3)) for s in self._samples], axis=0)
        edges = np.percentile(lap, 20, axis=0)
        mask = ((std < self.std_thr) & (edges > self.edge_thr)).astype(np.uint8)
        if not mask.any():
            return []
        mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
        n, _labels, stats, _cent = cv2.connectedComponentsWithStats(mask, connectivity=8)
        h, w = self._shape
        out: list[Box] = []
        for i in range(1, n):
            x, y, bw, bh, area = (int(v) for v in stats[i])
            bx, by, bw_px, bh_px = x * self.scale, y * self.scale, bw * self.scale, bh * self.scale
            if bw_px < self.min_w or bh_px > self.max_h_frac * h or area < 3:
                continue
            out.append(Box(bx, by, min(bw_px, w - bx), min(bh_px, h - by), 1.0, "osd"))
        return out

    # ---- filtering -----------------------------------------------------
    def in_band(self, box: Box, frame_h: int) -> bool:
        if self.band <= 0 or frame_h <= 0:
            return False
        cy = box.y + box.h / 2.0
        return cy < self.band * frame_h or cy > (1.0 - self.band) * frame_h

    def in_region(self, box: Box) -> bool:
        if not self.regions or box.area <= 0:
            return False
        covered = 0
        for r in self.regions:
            ix1, iy1 = max(box.x, r.x), max(box.y, r.y)
            ix2, iy2 = min(box.x + box.w, r.x + r.w), min(box.y + box.h, r.y + r.h)
            covered += max(0, ix2 - ix1) * max(0, iy2 - iy1)
            if covered >= self.cover * box.area:
                return True
        return False

    def filter(self, boxes: list[Box], frame_h: int) -> list[Box]:
        kept: list[Box] = []
        for b in boxes:
            if self.in_band(b, frame_h):
                self.suppressed_band += 1
            elif self.in_region(b):
                self.suppressed_region += 1
            else:
                kept.append(b)
        return kept


class AutoDetector:
    """ONNX with contour fallback for frames where ONNX found nothing."""

    name = "auto"

    def __init__(self, primary: OnnxPlateDetector, fallback: ContourPlateDetector) -> None:
        self.primary = primary
        self.fallback = fallback
        self.weights: WeightStatus | None = None

    def detect(self, image: np.ndarray) -> list[Box]:
        boxes = self.primary.detect(image)
        if boxes:
            return boxes
        return self.fallback.detect(image)


def build_detector(backend: str, model_path: str, *, conf: float, min_w: int, cpu: bool, threads: int = 2,
                   tile: int = 0, tile_overlap: float = 0.15) -> PlateDetector:
    """Instantiate the configured backend.

    The ONNX model file is checked against ``anpr/weights/HASHES.txt`` before it is loaded; a
    missing or hash-mismatched file counts as "model unavailable". ``onnx`` then raises
    ``RuntimeError``; ``auto`` degrades to the contour detector, logs an ERROR and reports
    itself as ``contour-fallback`` (``detector.name``) so the heartbeat / Health page show the
    degraded state instead of a healthy-looking ``contour``. ``tile`` > 0 wraps the result in a
    :class:`TiledDetector` (``ANPR_DET_TILE``).
    """
    detector = _build_backend(backend, model_path, conf=conf, min_w=min_w, cpu=cpu, threads=threads)
    if tile > 0:
        log.info("plate detector tiles: %d px, overlap %.2f", tile, tile_overlap)
        return TiledDetector(detector, tile, tile_overlap)
    return detector


def _build_backend(backend: str, model_path: str, *, conf: float, min_w: int, cpu: bool, threads: int) -> PlateDetector:
    contour = ContourPlateDetector(min_w=min_w)
    if backend == "contour":
        return contour
    if backend not in ("onnx", "auto"):
        raise RuntimeError(f"ANPR_DETECTOR={backend!r} is not one of auto|onnx|contour")
    try:
        status = verify_weight_file(model_path)
        if not status.ok:
            raise RuntimeError(status.describe())
        log.info("plate detector weights: %s", status.describe())
        onnx = OnnxPlateDetector(model_path, conf=conf, min_w=min_w, cpu=cpu, threads=threads)
        onnx.weights = status
    except Exception as exc:  # noqa: BLE001 - missing/corrupt weights, broken onnxruntime, ...
        if backend == "onnx":
            raise RuntimeError(f"ANPR_DETECTOR=onnx but the model could not be loaded: {exc}") from exc
        log.error(
            "DEGRADED: ONNX plate detector unavailable (%s); running the contour detector only. "
            "It reads the synthetic plates but is weak on real footage - rebuild the image with "
            "DOWNLOAD_WEIGHTS=1 or place the file at %s. Heartbeat detector=%s",
            exc, model_path, CONTOUR_FALLBACK,
        )
        contour.name = CONTOUR_FALLBACK
        return contour
    if backend == "onnx":
        return onnx
    auto = AutoDetector(onnx, contour)
    auto.weights = status
    return auto


def detector_status(detector: PlateDetector, requested: str) -> dict[str, object]:
    """Heartbeat ``extra.detector`` block: what was asked for, what runs, and whether it is degraded."""
    primary = getattr(detector, "primary", detector)
    weights = getattr(detector, "weights", None)
    return {
        "requested": requested,
        "active": detector.name,
        "degraded": detector.name == CONTOUR_FALLBACK,
        "providers": list(getattr(primary, "providers", []) or []),
        "weights": None if weights is None else {"state": weights.state, "sha256": weights.sha256, "licence": weights.licence},
        "tile": detector.tile if isinstance(detector, TiledDetector) else 0,
    }
