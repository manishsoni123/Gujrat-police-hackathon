"""Char-wise majority voting over a short window (CONTRACT.md section 7.7, plan 5.5 step 7).

Every OCR result on every frame is a ``Candidate``. Candidates from the same camera whose
normalised strings are within one edit of each other share a *bucket*; when the bucket's
window (``ANPR_VOTE_WINDOW_S``, default 3 s) expires the bucket emits **one** ``VotedRead``:
the position-wise majority string (over the members of the majority length), the mean
confidence, and the bbox / crop / frame of the highest-confidence member.

``plate_raw`` of the emitted read is **consistent with the vote**: it is the raw OCR string of the
highest-confidence member whose own normalisation equals the voted ``plate_norm`` (falling back to
the display form of the voted string). The API re-normalises ``plate_raw`` and trusts that over the
worker's ``plate_norm`` (CONTRACT section 7.2), so a raw string taken blindly from the best member -
which may be exactly the mis-read the vote corrected - silently undid the correction (Amendments,
2026-09-05 anpr). Invariant, asserted in ``tests/test_voting.py``:
``normalise(read.plate_raw).plate_norm == read.plate_norm``.

Only voted reads are posted to the API - never per-frame reads.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from anpr.normalise import format_plate, levenshtein, normalise

log = logging.getLogger("anpr.voting")


@dataclass
class Candidate:
    """One OCR result on one frame."""

    camera_id: int
    captured_at: datetime
    stream_pts: float
    frame_index: int
    plate_raw: str
    plate_norm: str
    is_valid_format: bool
    confidence: float
    bbox: tuple[int, int, int, int]          # x, y, w, h in the decoded (960 px) frame
    crop_jpeg: bytes
    frame: np.ndarray | None = None          # full decoded frame (kept only for the best member)


@dataclass
class VotedRead:
    """The accepted read of one plate for one vote window."""

    camera_id: int
    captured_at: datetime
    stream_pts: float
    frame_index: int
    plate_raw: str
    plate_norm: str
    is_valid_format: bool
    confidence: float
    bbox: tuple[int, int, int, int]
    crop_jpeg: bytes
    frame: np.ndarray | None
    votes: int
    first_seen: datetime
    last_seen: datetime
    sighting_key: str = ""

    def to_payload(self, crop_field: str) -> dict[str, Any]:
        from anpr.client import iso_utc  # local import keeps this module free of HTTP concerns

        return {
            "captured_at": iso_utc(self.captured_at),
            "stream_pts": round(float(self.stream_pts), 3),
            "frame_index": int(self.frame_index),
            "plate_raw": self.plate_raw,
            "plate_norm": self.plate_norm,
            "is_valid_format": bool(self.is_valid_format),
            "confidence": round(float(self.confidence), 4),
            "bbox": [int(v) for v in self.bbox],
            "crop_file": crop_field,
            "sighting_key": self.sighting_key,
        }


@dataclass
class _Bucket:
    camera_id: int
    key: str                                  # plate_norm of the first member
    first_seen: datetime
    last_seen: datetime
    members: list[Candidate] = field(default_factory=list)
    best: Candidate | None = None

    def add(self, cand: Candidate) -> None:
        self.members.append(cand)
        self.last_seen = max(self.last_seen, cand.captured_at)
        if self.best is None or cand.confidence > self.best.confidence:
            if self.best is not None:
                self.best.frame = None      # keep only one full frame per bucket
            self.best = cand
        else:
            cand.frame = None


def raw_for_vote(plate_norm: str, members: list[Candidate]) -> str:
    """The ``plate_raw`` to report for a voted ``plate_norm``.

    Preference order, the first whose normalisation reproduces ``plate_norm``:

    1. the raw OCR string of the highest-confidence member that normalised to ``plate_norm``
       (real OCR output, keeps the ``GJ 01 AB 1234`` spacing the reader produced);
    2. ``format_plate(plate_norm)`` - the display form, when no member agrees with the vote
       (possible with ``max_distance > 1`` or when ``normalise`` rewrote the majority string);
    3. ``plate_norm`` itself.
    """
    agreeing = [m for m in members if m.plate_norm == plate_norm]
    candidates: list[str] = []
    if agreeing:
        candidates.append(max(agreeing, key=lambda m: m.confidence).plate_raw)
    candidates.append(format_plate(plate_norm))
    candidates.append(plate_norm)
    for raw in candidates:
        if raw and normalise(raw).plate_norm == plate_norm:
            return raw
    return candidates[-1]


def majority_string(strings: list[str], weights: list[float] | None = None) -> str:
    """Position-wise majority over the members of the most common length.

    ``weights`` (confidences) break ties: a tie at a position goes to the character with the
    highest summed weight, then to the character of the highest-weight member.
    """
    if not strings:
        return ""
    weights = weights or [1.0] * len(strings)
    length_votes = Counter(len(s) for s in strings)
    top = max(length_votes.values())
    # tie on length -> the length with the highest total weight
    lengths = [n for n, c in length_votes.items() if c == top]
    if len(lengths) > 1:
        lengths.sort(key=lambda n: -sum(w for s, w in zip(strings, weights) if len(s) == n))
    n = lengths[0]
    pool = [(s, w) for s, w in zip(strings, weights) if len(s) == n]
    out = []
    for i in range(n):
        counts: Counter[str] = Counter()
        weight_sum: dict[str, float] = {}
        best_w: dict[str, float] = {}
        for s, w in pool:
            counts[s[i]] += 1
            weight_sum[s[i]] = weight_sum.get(s[i], 0.0) + w
            best_w[s[i]] = max(best_w.get(s[i], 0.0), w)
        out.append(max(counts, key=lambda c: (counts[c], weight_sum[c], best_w[c])))
    return "".join(out)


class Voter:
    """Per-camera vote buckets with a fixed window measured on ``captured_at``."""

    def __init__(self, window_s: float = 3.0, max_distance: int = 1) -> None:
        self.window_s = float(window_s)
        self.max_distance = int(max_distance)
        self._buckets: dict[int, list[_Bucket]] = {}

    # ---- input ----------------------------------------------------------
    def _find_bucket(self, cand: Candidate) -> _Bucket | None:
        best: tuple[int, _Bucket] | None = None
        for bucket in self._buckets.get(cand.camera_id, []):
            d = levenshtein(bucket.key, cand.plate_norm)
            if d <= self.max_distance and (best is None or d < best[0]):
                best = (d, bucket)
        return best[1] if best else None

    def add(self, cand: Candidate) -> None:
        if not cand.plate_norm:
            return
        bucket = self._find_bucket(cand)
        if bucket is None:
            bucket = _Bucket(cand.camera_id, cand.plate_norm, cand.captured_at, cand.captured_at)
            self._buckets.setdefault(cand.camera_id, []).append(bucket)
        bucket.add(cand)

    # ---- output ---------------------------------------------------------
    def _emit(self, bucket: _Bucket) -> VotedRead | None:
        if not bucket.members or bucket.best is None:
            return None
        strings = [m.plate_norm for m in bucket.members]
        weights = [m.confidence for m in bucket.members]
        voted = majority_string(strings, weights)
        norm = normalise(voted)
        plate_norm = norm.plate_norm or voted
        mean_conf = float(sum(weights) / len(weights))
        best = bucket.best                                      # bbox / crop / frame / time: best member
        plate_raw = raw_for_vote(plate_norm, bucket.members)    # raw string: a member that agrees with the vote
        if plate_raw != best.plate_raw:
            log.debug("camera %s: vote %s overrides best member %r (%.2f); plate_raw=%r",
                      bucket.camera_id, plate_norm, best.plate_raw, best.confidence, plate_raw)
        return VotedRead(
            camera_id=bucket.camera_id,
            captured_at=best.captured_at,
            stream_pts=best.stream_pts,
            frame_index=best.frame_index,
            plate_raw=plate_raw,
            plate_norm=plate_norm,
            is_valid_format=norm.is_valid_format,
            confidence=mean_conf,
            bbox=best.bbox,
            crop_jpeg=best.crop_jpeg,
            frame=best.frame,
            votes=len(bucket.members),
            first_seen=bucket.first_seen,
            last_seen=bucket.last_seen,
        )

    def expire(self, now: datetime, camera_id: int | None = None) -> list[VotedRead]:
        """Emit every bucket whose window has elapsed at ``now``."""
        out: list[VotedRead] = []
        cameras = [camera_id] if camera_id is not None else list(self._buckets)
        for cam in cameras:
            keep: list[_Bucket] = []
            for bucket in self._buckets.get(cam, []):
                if (now - bucket.first_seen).total_seconds() >= self.window_s:
                    read = self._emit(bucket)
                    if read:
                        out.append(read)
                else:
                    keep.append(bucket)
            if keep:
                self._buckets[cam] = keep
            else:
                self._buckets.pop(cam, None)
        return out

    def flush(self, camera_id: int | None = None) -> list[VotedRead]:
        """Emit every open bucket immediately (loop reset, decoder restart, shutdown)."""
        out: list[VotedRead] = []
        cameras = [camera_id] if camera_id is not None else list(self._buckets)
        for cam in cameras:
            for bucket in self._buckets.pop(cam, []):
                read = self._emit(bucket)
                if read:
                    out.append(read)
        return out

    def open_buckets(self, camera_id: int | None = None) -> int:
        if camera_id is not None:
            return len(self._buckets.get(camera_id, []))
        return sum(len(v) for v in self._buckets.values())
