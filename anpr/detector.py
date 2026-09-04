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


def build_detector(backend: str, model_path: str, *, conf: float, min_w: int, cpu: bool, threads: int = 2) -> PlateDetector:
    """Instantiate the configured backend.

    The ONNX model file is checked against ``anpr/weights/HASHES.txt`` before it is loaded; a
    missing or hash-mismatched file counts as "model unavailable". ``onnx`` then raises
    ``RuntimeError``; ``auto`` degrades to the contour detector, logs an ERROR and reports
    itself as ``contour-fallback`` (``detector.name``) so the heartbeat / Health page show the
    degraded state instead of a healthy-looking ``contour``.
    """
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
    }
