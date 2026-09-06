"""Detected-vehicle evidence store (``ANPR_EVIDENCE_DIR``).

The organiser's output report must show *detected vehicles or number plates with timestamps*.
On the night footage most plates cannot be read, so every plate **track** that ends (a vehicle
that passed with a visible plate box) is kept as evidence in its own right: one JPEG crop of its
best shot plus one JSON line - camera, UTC timestamps (first/last seen, best shot), bbox and width
in decoded pixels, frames in the track, and the plate read for it when there was one.

Layout (``dir`` must be a writable mount; the worker image is read-only otherwise)::

    <dir>/<camera_id>/<YYYY-MM-DD>/<HHMMSS_mmm>_t<track>_w<px>.jpg
    <dir>/<camera_id>/<YYYY-MM-DD>/vehicles.jsonl

Writes are capped at ``per_hour`` files per camera (a busy junction at 2 fps would otherwise fill
the disk); the store disables itself after an ``OSError`` and says so once. The API is not
involved: ``CONTRACT.md`` section 7.2 only accepts ``loop_reset`` / ``intrusion`` events, so a
``vehicle_detected`` event type is a backend change for later (see docs/anpr-accuracy.md).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

log = logging.getLogger("anpr.evidence")


class EvidenceStore:
    def __init__(self, directory: str | os.PathLike[str] | None, per_hour: int = 200) -> None:
        self.dir = Path(directory) if directory else None
        self.per_hour = max(0, int(per_hour))
        self.enabled = self.dir is not None
        self.written = 0
        self.skipped = 0
        self._hour: dict[int, tuple[int, int]] = {}      # camera -> (hour bucket, files written in it)
        if self.dir is not None:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                probe = self.dir / ".write-test"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
            except OSError as exc:
                log.warning("evidence store %s is not writable (%s); vehicle evidence disabled", self.dir, exc)
                self.enabled = False

    def _allowed(self, camera_id: int) -> bool:
        if self.per_hour <= 0:
            return True
        hour = int(time.time() // 3600)
        bucket, count = self._hour.get(camera_id, (hour, 0))
        if bucket != hour:
            bucket, count = hour, 0
        if count >= self.per_hour:
            self._hour[camera_id] = (bucket, count)
            return False
        self._hour[camera_id] = (bucket, count + 1)
        return True

    def write(self, camera_id: int, crop: np.ndarray, record: dict[str, Any], captured_at: datetime) -> str | None:
        """Save one vehicle crop + record; returns the relative file path or None (disabled / capped / failed)."""
        if not self.enabled or self.dir is None or crop is None or crop.size == 0:
            return None
        if not self._allowed(camera_id):
            self.skipped += 1
            return None
        ts = captured_at.astimezone(timezone.utc)
        day_dir = self.dir / str(camera_id) / ts.strftime("%Y-%m-%d")
        name = f"{ts.strftime('%H%M%S')}_{ts.microsecond // 1000:03d}_t{record.get('track', 0)}_w{record.get('width_px', 0)}.jpg"
        try:
            day_dir.mkdir(parents=True, exist_ok=True)
            ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            if not ok:
                return None
            (day_dir / name).write_bytes(buf.tobytes())
            rel = f"{camera_id}/{ts.strftime('%Y-%m-%d')}/{name}"
            with (day_dir / "vehicles.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({**record, "file": rel}, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("evidence write failed (%s); vehicle evidence disabled", exc)
            self.enabled = False
            return None
        self.written += 1
        return rel

    def stats(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "dir": str(self.dir) if self.dir else None, "written": self.written, "skipped": self.skipped}
