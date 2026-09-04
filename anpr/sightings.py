"""Sighting state machine (CONTRACT.md section 7.7 / plan 5.5 step 8).

A sighting is opened on the first accepted (voted) read of a plate on a camera, extended by
every further read of the same ``plate_norm``, and closed after ``SIGHTING_CLOSE_S`` of
silence or on a discontinuity. The key ``<camera_id>:<plate_norm>:<first_seen_epoch_ms>`` is
generated here and stored by the API as ``sightings.worker_key`` for idempotent upserts. A
plate that returns after closure gets a new key - a looping feed truthfully yields a second
sighting.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from anpr.voting import VotedRead


def epoch_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def make_key(camera_id: int, plate_norm: str, first_seen: datetime) -> str:
    key = f"{camera_id}:{plate_norm}:{epoch_ms(first_seen)}"
    return key[:80]


@dataclass
class Sighting:
    key: str
    camera_id: int
    plate_norm: str
    is_valid_format: bool
    first_seen: datetime
    last_seen: datetime
    read_count: int
    best_conf: float
    best_read_captured_at: datetime
    best_crop_jpeg: bytes
    best_frame_jpeg: bytes | None
    closed: bool = False
    dirty: bool = True            # changed since the last batch
    best_changed: bool = True     # best read changed since the last batch (send the frame)
    best_crop_field: str | None = None   # crop field of the best read inside the current batch

    def to_payload(self, frame_field: str | None) -> dict[str, Any]:
        from anpr.client import iso_utc

        return {
            "key": self.key,
            "plate_norm": self.plate_norm,
            "is_valid_format": bool(self.is_valid_format),
            "first_seen": iso_utc(self.first_seen),
            "last_seen": iso_utc(self.last_seen),
            "read_count": int(self.read_count),
            "best_conf": round(float(self.best_conf), 4),
            "best_read_captured_at": iso_utc(self.best_read_captured_at),
            "best_crop_file": self.best_crop_field,
            "frame_file": frame_field,
            "closed": bool(self.closed),
        }


class SightingTracker:
    """Open / extend / close sightings per (camera, plate_norm)."""

    def __init__(self, close_after_s: float = 15.0) -> None:
        self.close_after_s = float(close_after_s)
        self._open: dict[tuple[int, str], Sighting] = {}
        self._pending: dict[int, dict[str, Sighting]] = {}   # camera -> key -> dirty sightings

    def _mark(self, s: Sighting) -> None:
        s.dirty = True
        self._pending.setdefault(s.camera_id, {})[s.key] = s

    def add_read(self, read: VotedRead, crop_field: str, frame_jpeg: bytes | None) -> Sighting:
        """Attach a voted read; returns the (new or extended) sighting and sets ``read.sighting_key``."""
        key = (read.camera_id, read.plate_norm)
        s = self._open.get(key)
        if s is None:
            s = Sighting(
                key=make_key(read.camera_id, read.plate_norm, read.first_seen),
                camera_id=read.camera_id,
                plate_norm=read.plate_norm,
                is_valid_format=read.is_valid_format,
                first_seen=read.first_seen,
                last_seen=read.last_seen,
                read_count=1,
                best_conf=read.confidence,
                best_read_captured_at=read.captured_at,
                best_crop_jpeg=read.crop_jpeg,
                best_frame_jpeg=frame_jpeg,
                best_changed=True,
                best_crop_field=crop_field,
            )
            self._open[key] = s
        else:
            s.read_count += 1
            s.last_seen = max(s.last_seen, read.last_seen)
            s.first_seen = min(s.first_seen, read.first_seen)
            if read.confidence > s.best_conf:
                s.best_conf = read.confidence
                s.best_read_captured_at = read.captured_at
                s.best_crop_jpeg = read.crop_jpeg
                s.best_frame_jpeg = frame_jpeg
                s.best_changed = True
                s.best_crop_field = crop_field
        read.sighting_key = s.key
        self._mark(s)
        return s

    def expire(self, now: datetime, camera_id: int | None = None) -> list[Sighting]:
        """Close sightings silent for longer than ``close_after_s``."""
        closed: list[Sighting] = []
        for key, s in list(self._open.items()):
            if camera_id is not None and s.camera_id != camera_id:
                continue
            if (now - s.last_seen).total_seconds() >= self.close_after_s:
                s.closed = True
                self._mark(s)
                closed.append(s)
                del self._open[key]
        return closed

    def close_all(self, camera_id: int | None = None) -> list[Sighting]:
        """Close every open sighting (loop reset, decoder restart, shutdown)."""
        closed: list[Sighting] = []
        for key, s in list(self._open.items()):
            if camera_id is not None and s.camera_id != camera_id:
                continue
            s.closed = True
            self._mark(s)
            closed.append(s)
            del self._open[key]
        return closed

    def drain_dirty(self, camera_id: int) -> list[Sighting]:
        """Return the sightings changed since the last batch for one camera and reset their flags.

        The caller must read ``best_changed`` / ``best_crop_field`` before this returns; they are
        reset here so the next batch only carries a frame when the best read changed again.
        """
        pending = self._pending.pop(camera_id, {})
        out = list(pending.values())
        for s in out:
            s.dirty = False
        return out

    def finalize_batch(self, sightings: list[Sighting]) -> None:
        for s in sightings:
            s.best_changed = False
            s.best_crop_field = None
            s.best_frame_jpeg = None if s.closed else s.best_frame_jpeg

    def open_count(self, camera_id: int | None = None) -> int:
        if camera_id is None:
            return len(self._open)
        return sum(1 for s in self._open.values() if s.camera_id == camera_id)

    def open_sightings(self, camera_id: int | None = None) -> list[Sighting]:
        return [s for s in self._open.values() if camera_id is None or s.camera_id == camera_id]
