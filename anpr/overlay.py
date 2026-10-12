"""Live detection overlay feed (CONTRACT amendment 2026-10-12, POST /internal/overlay).

Per processed frame of a *watched* camera the worker keeps one small JSON frame: plate boxes (the
plate tracker's live tracks, labelled with the track's best valid read once OCR ran) and the most
recent object boxes, all normalised to 0..1 of the decoded frame so the browser can draw them over
any rendition of the stream. Only the newest frame per camera is kept ("latest wins"); the sender
posts them together in one request whenever it is free, so a slow API never builds a backlog.

Which cameras are watched is the API's answer to every POST (``watched``: cameras with at least one
``/ws/reads/{id}`` subscriber). With nobody watching the worker only polls every ``poll_s`` seconds
with an empty batch, so the feature costs nothing when no camera page is open.
"""
from __future__ import annotations

import threading
import time
from collections import Counter
from typing import Any, Iterable

from anpr.normalise import normalise

MAX_PLATES = 16
MAX_OBJECTS = 64


def _norm_box(x: float, y: float, w: float, h: float, fw: int, fh: int) -> list[float]:
    """Pixel ``x, y, w, h`` -> ``[x, y, w, h]`` as fractions of the frame, clamped to the frame."""
    x0, y0 = max(0.0, min(1.0, x / fw)), max(0.0, min(1.0, y / fh))
    x1, y1 = max(0.0, min(1.0, (x + w) / fw)), max(0.0, min(1.0, (y + h) / fh))
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


def track_label(reads: Iterable[str]) -> str | None:
    """Most frequent valid-format read of a plate track (None until OCR produced one)."""
    valid = [p for p in reads if p and normalise(p).is_valid_format]
    return Counter(valid).most_common(1)[0][0] if valid else None


def overlay_frame(camera_id: int, captured_at: str, frame_shape: tuple[int, ...], tracks: Iterable[Any],
                  objects: Iterable[Any] = (), objects_at: str | None = None) -> dict[str, Any]:
    """One overlay frame. ``tracks``: plate tracks seen in this frame (``.id``, ``.box``, ``.reads``);
    ``objects``: YOLOX detections (``.cls``, ``.confidence``, ``x1..y2``) from the run at ``objects_at``."""
    fh, fw = int(frame_shape[0]), int(frame_shape[1])
    plates = []
    for t in list(tracks)[:MAX_PLATES]:
        b = t.box
        plates.append({"box": _norm_box(b.x, b.y, b.w, b.h, fw, fh), "conf": round(float(b.confidence), 3),
                       "label": track_label(t.reads), "track": int(t.id)})
    objs = []
    for d in sorted(objects, key=lambda d: -d.confidence)[:MAX_OBJECTS]:
        objs.append({"box": _norm_box(d.x1, d.y1, d.x2 - d.x1, d.y2 - d.y1, fw, fh),
                     "conf": round(float(d.confidence), 3), "cls": d.cls})
    return {"camera_id": int(camera_id), "captured_at": captured_at, "frame_w": fw, "frame_h": fh,
            "plates": plates, "objects": objs, "objects_at": objects_at if objs else None}


class OverlayFeed:
    """Thread-safe "latest frame per watched camera" buffer shared by the inference loop and the sender."""

    def __init__(self, enabled: bool = True, poll_s: float = 2.0) -> None:
        self.enabled = bool(enabled)
        self.poll_s = float(poll_s)
        self._lock = threading.Lock()
        self._latest: dict[int, dict[str, Any]] = {}
        self._watched: set[int] = set()
        self._inflight = False
        self._last_post = 0.0
        self.sent_frames = 0

    def watched(self, camera_id: int) -> bool:
        with self._lock:
            return self.enabled and camera_id in self._watched

    def offer(self, frame: dict[str, Any]) -> None:
        with self._lock:
            if self.enabled and frame["camera_id"] in self._watched:
                self._latest[frame["camera_id"]] = frame

    def due(self, now: float | None = None) -> bool:
        """True when a POST should be queued: frames are waiting, or the subscriber poll is due."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if not self.enabled or self._inflight:
                return False
            if self._latest or now - self._last_post >= self.poll_s:
                self._inflight = True
                return True
            return False

    def take(self) -> list[dict[str, Any]]:
        with self._lock:
            frames = list(self._latest.values())
            self._latest.clear()
            return frames

    def done(self, watched: Iterable[int] | None, now: float | None = None) -> None:
        """A POST finished; ``watched`` is the API's subscriber list (None = the call failed: keep the old one)."""
        with self._lock:
            self._inflight = False
            self._last_post = time.monotonic() if now is None else now
            if watched is not None:
                self._watched = {int(c) for c in watched}
                for cid in [c for c in self._latest if c not in self._watched]:
                    del self._latest[cid]

    def disable(self) -> None:
        with self._lock:
            self.enabled = False
            self._inflight = False
            self._latest.clear()
            self._watched.clear()
