"""Plate-box tracking and best-shot selection (real-feed accuracy pass, 5 Sept 2026).

On a wide-angle junction camera a vehicle approaches from far away: its plate is first boxed at
25-30 px, grows to 100-160 px in the near lane and leaves. Reading every frame wastes OCR calls
on the small, blurred instances and floods the voter with garbage. :class:`PlateTracker`
associates detector boxes across frames per camera (greedy IoU, with a centre-distance fallback
for fast movers at 1-2 fps), keeps the **best** ``keep`` crops per track (largest *and* sharpest:
width weighted by the Laplacian variance of the crop) and tells the caller when a track is
*settled* (its best width stopped growing) or *ended* (no box matched for ``gap_s``), which is
when the crop is worth an OCR call. Every ended track is also a **detected vehicle** with a
timestamp and a crop - evidence for the output report even when no plate could be read.

Coordinates are the decoded frame's pixels. Pure Python/NumPy; no I/O.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

import cv2
import numpy as np

from anpr.detector import Box, iou
from anpr.normalise import normalise

log = logging.getLogger("anpr.tracks")

SHARPNESS_REF_H = 32          # crops are normalised to this height before the Laplacian, so size does not bias sharpness
SHARPNESS_SAT = 8.0           # sharpness ratio at which a crop counts as fully sharp (see ``sharpness``)


def sharpness(crop: np.ndarray) -> float:
    """Blur measure of a crop: Laplacian variance / intensity variance on a 32 px-high copy.

    Dividing by the crop's own variance makes the figure independent of contrast and exposure
    (a dim IR plate and a floodlit one score alike). Measured on the organiser crops: sharp
    detector crops 7-33, the same crops after a 9x9 Gaussian blur 0.1-0.4, so ``SHARPNESS_SAT``
    = 8 treats everything sharper than that as fully sharp.
    """
    if crop is None or crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    h, w = gray.shape[:2]
    if h != SHARPNESS_REF_H and h > 0:
        gray = cv2.resize(gray, (max(4, int(round(w * SHARPNESS_REF_H / h))), SHARPNESS_REF_H), interpolation=cv2.INTER_AREA)
    small = gray.astype(np.float32)
    return float(cv2.Laplacian(small, cv2.CV_32F, ksize=3).var() / max(1.0, float(small.var())))


def shot_score(width: int, sharp: float) -> float:
    """Ranking of candidate shots: width, discounted for blur (a 120 px smear loses to a sharp 90 px crop)."""
    return float(width) * (0.5 + 0.5 * min(1.0, sharp / SHARPNESS_SAT))


def crop_with_context(image: np.ndarray, box: Box, mx: float = 0.08, my: float = 0.15) -> np.ndarray:
    """The pipeline's crop rule: a little horizontal / vertical context around the detector box."""
    h, w = image.shape[:2]
    dx, dy = int(box.w * mx), int(box.h * my)
    x1, y1 = max(0, box.x - dx), max(0, box.y - dy)
    x2, y2 = min(w, box.x + box.w + dx), min(h, box.y + box.h + dy)
    return image[y1:y2, x1:x2]


@dataclass
class Shot:
    """One candidate crop of a track."""

    crop: np.ndarray
    box: Box
    captured_at: datetime
    stream_pts: float
    frame_index: int
    sharp: float
    score: float

    @property
    def width(self) -> int:
        return self.box.w


@dataclass
class Track:
    id: int
    camera_id: int
    box: Box
    first_seen: datetime
    last_seen: datetime
    last_pts: float
    frames: int = 1
    shots: list[Shot] = field(default_factory=list)      # best ``keep`` shots, best first
    misses: int = 0
    stall: int = 0                                       # consecutive updates without the best width growing
    ocr_width: int = 0                                   # best width when OCR last ran (0 = never)
    ocr_runs: int = 0
    reads: list[str] = field(default_factory=list)       # plate_norm strings the OCR produced for this track
    text_only: int = 0                                   # OCR passes that returned letters only (signboard / caption)
    ended: bool = False

    @property
    def best(self) -> Shot | None:
        return self.shots[0] if self.shots else None

    @property
    def best_width(self) -> int:
        return self.shots[0].box.w if self.shots else 0

    @property
    def max_width(self) -> int:
        return max((s.box.w for s in self.shots), default=0)

    def offer(self, shot: Shot, keep: int) -> bool:
        """Insert ``shot`` into the best-``keep`` list; returns True when the best shot changed."""
        before = self.best_width
        self.shots.append(shot)
        self.shots.sort(key=lambda s: -s.score)
        del self.shots[keep:]
        grew = self.best_width > before * 1.05
        self.stall = 0 if grew else self.stall + 1
        return self.shots[0] is shot

    def settled(self, stall_updates: int) -> bool:
        """Best shot stopped growing for ``stall_updates`` consecutive frames."""
        return self.stall >= stall_updates


def track_kind(t: Track) -> str:
    """What an ended track is evidence of.

    ``plate``   - at least one OCR pass produced a valid-format registration;
    ``text``    - every OCR pass returned letters only (a signboard or a burnt-in caption the detector
                  boxed; a registration always carries digits) - **not** a vehicle, not counted as one;
    ``vehicle`` - a plate box that was never read, or read as an invalid string (blown-out / half plate).
    """
    if any(normalise(p).is_valid_format for p in t.reads if p):
        return "plate"
    if t.text_only and not any(t.reads):
        return "text"
    return "vehicle"


def _centre_match(a: Box, b: Box) -> bool:
    """Fallback association for 1-2 fps feeds: same size class and closer than 1.5 widths."""
    if not (0.5 <= b.w / max(1, a.w) <= 2.0):
        return False
    acx, acy = a.x + a.w / 2, a.y + a.h / 2
    bcx, bcy = b.x + b.w / 2, b.y + b.h / 2
    limit = 1.5 * max(a.w, b.w)
    return abs(acx - bcx) <= limit and abs(acy - bcy) <= limit


class PlateTracker:
    """Per-camera association of plate boxes across frames with best-shot memory.

    ``update`` takes the (already filtered) detector boxes of one frame and returns the tracks
    that were matched or opened in this frame plus the tracks that ended (no match for ``gap_s``
    seconds of ``captured_at``). The caller decides when to OCR (see :meth:`ready_for_ocr`).
    """

    def __init__(self, camera_id: int, *, gap_s: float = 2.0, keep: int = 3, iou_thr: float = 0.15,
                 stall_updates: int = 3, regrow: float = 1.25) -> None:
        self.camera_id = int(camera_id)
        self.gap_s = float(gap_s)
        self.keep = max(1, int(keep))
        self.iou_thr = float(iou_thr)
        self.stall_updates = max(1, int(stall_updates))
        self.regrow = float(regrow)
        self.tracks: list[Track] = []
        self._next_id = 1
        self.opened = 0
        self.ended_total = 0

    # ---- association ---------------------------------------------------
    def _match(self, boxes: list[Box]) -> tuple[dict[int, Track], list[Box]]:
        pairs: list[tuple[float, int, Track]] = []
        for i, b in enumerate(boxes):
            for t in self.tracks:
                if t.ended:
                    continue
                score = iou(b, t.box)
                if score >= self.iou_thr:
                    pairs.append((1.0 + score, i, t))
                elif _centre_match(t.box, b):
                    pairs.append((0.5, i, t))
        pairs.sort(key=lambda p: -p[0])
        matched: dict[int, Track] = {}
        used: set[int] = set()
        for _score, i, t in pairs:
            if i in matched or id(t) in used:
                continue
            matched[i] = t
            used.add(id(t))
        unmatched = [b for i, b in enumerate(boxes) if i not in matched]
        return matched, unmatched

    def update(self, boxes: Iterable[Box], image: np.ndarray, captured_at: datetime, stream_pts: float,
               frame_index: int) -> tuple[list[Track], list[Track]]:
        boxes = list(boxes)
        matched, unmatched = self._match(boxes)
        touched: list[Track] = []
        for i, b in enumerate(boxes):
            t = matched.get(i)
            crop = crop_with_context(image, b)
            if crop.size == 0:
                continue
            sharp = sharpness(crop)
            shot = Shot(crop=crop, box=b, captured_at=captured_at, stream_pts=stream_pts, frame_index=frame_index,
                        sharp=sharp, score=shot_score(b.w, sharp))
            if t is None:
                t = Track(id=self._next_id, camera_id=self.camera_id, box=b, first_seen=captured_at,
                          last_seen=captured_at, last_pts=stream_pts)
                self._next_id += 1
                self.opened += 1
                self.tracks.append(t)
            else:
                t.box = b
                t.frames += 1
                t.last_seen = max(t.last_seen, captured_at)
                t.last_pts = stream_pts
                t.misses = 0
            t.offer(shot, self.keep)
            touched.append(t)
        touched_ids = {id(t) for t in touched}
        ended: list[Track] = []
        for t in self.tracks:
            if id(t) in touched_ids or t.ended:
                continue
            t.misses += 1
            if (captured_at - t.last_seen).total_seconds() >= self.gap_s:
                t.ended = True
                ended.append(t)
        if ended:
            self.ended_total += len(ended)
            self.tracks = [t for t in self.tracks if not t.ended]
        return touched, ended

    def flush(self) -> list[Track]:
        """End every open track (discontinuity, camera stop, shutdown)."""
        out = [t for t in self.tracks if not t.ended]
        for t in out:
            t.ended = True
        self.ended_total += len(out)
        self.tracks = []
        return out

    # ---- OCR policy ----------------------------------------------------
    def ready_for_ocr(self, t: Track, min_w: int, *, ended: bool = False) -> bool:
        """OCR a track when its best crop is wide enough and (a) the track ended, (b) it settled and was
        never read at this size, or (c) its best shot grew ``regrow`` x since the last OCR."""
        w = t.best_width
        if w < min_w:
            return False
        if t.ocr_width == 0:
            return ended or t.settled(self.stall_updates)
        if w >= t.ocr_width * self.regrow:
            return True
        return False

    @property
    def open(self) -> int:
        return sum(1 for t in self.tracks if not t.ended)
