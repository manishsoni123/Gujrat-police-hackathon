"""ANPR worker → API payloads (CONTRACT §7)."""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, PrivateAttr, ValidationError, field_validator, model_validator
from pydantic.functional_validators import ModelWrapValidatorHandler

from app.db.models import OBJECT_CLASSES
from app.schemas.common import ApiModel

PLATE_NORM_MAX = 16  # varchar(16): plate_reads.plate_norm and sightings.plate_norm
MAX_REASON_CHARS = 300


class ReadIn(ApiModel):
    captured_at: str
    stream_pts: float | None = None
    frame_index: int | None = None
    plate_raw: str = Field(max_length=64)
    plate_norm: str | None = Field(None, max_length=PLATE_NORM_MAX)
    is_valid_format: bool | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: list[int] | None = None
    crop_file: str | None = None
    sighting_key: str = Field(max_length=80)

    @field_validator("bbox")
    @classmethod
    def _bbox(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return None
        if len(v) != 4:
            raise ValueError("bbox must be [x, y, w, h]")
        return [int(x) for x in v]


class SightingIn(ApiModel):
    key: str = Field(max_length=80)
    plate_norm: str = Field(max_length=PLATE_NORM_MAX)
    is_valid_format: bool | None = None
    first_seen: str
    last_seen: str
    read_count: int = Field(ge=0)
    best_conf: float = Field(ge=0.0, le=1.0)
    best_read_captured_at: str | None = None
    best_crop_file: str | None = None
    frame_file: str | None = None
    closed: bool = False


class ObjectCountIn(ApiModel):
    minute: str
    class_: str = Field(alias="class")
    count: int = Field(ge=0)

    @field_validator("class_")
    @classmethod
    def _class(cls, v: str) -> str:
        if v not in OBJECT_CLASSES:
            raise ValueError(f"must be one of {', '.join(OBJECT_CLASSES)}")
        return v


class LiveCountsIn(ApiModel):
    minute: str
    counts: dict[str, int] = Field(default_factory=dict)


class EventIn(ApiModel):
    type: str
    occurred_at: str
    note: str | None = None
    stream_pts_before: float | None = None
    stream_pts_after: float | None = None
    zone_id: int | None = None
    class_: str | None = Field(None, alias="class")
    dwell_s: float | None = None
    frame_file: str | None = None

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in ("loop_reset", "intrusion"):
            raise ValueError("must be loop_reset or intrusion")
        return v


def _item_reason(exc: ValidationError) -> str:
    """`field: message[; field: message]` for one list item; the input value is never echoed."""
    parts = []
    for e in exc.errors():
        loc = ".".join(str(p) for p in e.get("loc", ()))
        msg = str(e.get("msg", "invalid")).removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)[:MAX_REASON_CHARS]


# payload list → (item model, `rejected[].kind`)
_ITEM_LISTS: dict[str, tuple[type[ApiModel], str]] = {
    "reads": (ReadIn, "read"),
    "sightings": (SightingIn, "sighting"),
    "object_counts": (ObjectCountIn, "object_count"),
    "events": (EventIn, "event"),
}


class DetectionBatch(ApiModel):
    """CONTRACT §7.2 payload.

    The envelope is validated strictly (a `ValidationError` is a 422 for the whole batch), but
    every item of `reads`, `sightings`, `object_counts` and `events` is validated on its own: an
    invalid item (e.g. a 35-character OCR string as `plate_norm`) is left out of its list and
    recorded in `rejected_items` as `{index, kind, reason}`, so the rest of the batch is ingested.
    `positions(field)` gives the payload index of every kept item, for the `rejected[].index` of
    later, per-item rejections.
    """

    worker_id: str = Field("unknown", max_length=64)
    mode: str = "live"
    camera_id: int
    camera_external_id: str | None = None
    sent_at: str | None = None
    reads: list[ReadIn] = Field(default_factory=list)
    sightings: list[SightingIn] = Field(default_factory=list)
    object_counts: list[ObjectCountIn] = Field(default_factory=list)
    live_counts: LiveCountsIn | None = None
    events: list[EventIn] = Field(default_factory=list)

    _positions: dict[str, list[int]] = PrivateAttr(default_factory=dict)
    _rejected: list[dict[str, Any]] = PrivateAttr(default_factory=list)

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in ("live", "preindex"):
            raise ValueError("must be live or preindex")
        return v

    @model_validator(mode="wrap")
    @classmethod
    def _per_item(cls, data: Any, handler: ModelWrapValidatorHandler[Self]) -> Self:
        if not isinstance(data, dict):
            return handler(data)
        kept_lists: dict[str, list[Any]] = {}
        positions: dict[str, list[int]] = {}
        rejected: list[dict[str, Any]] = []
        for field, (model, kind) in _ITEM_LISTS.items():
            raw = data.get(field)
            if not isinstance(raw, list):
                continue  # absent → default; not a list → the envelope's 422
            kept: list[Any] = []
            kept_at: list[int] = []
            for idx, item in enumerate(raw):
                try:
                    kept.append(model.model_validate(item))
                except ValidationError as exc:
                    rejected.append({"index": idx, "kind": kind, "reason": _item_reason(exc)})
                    continue
                kept_at.append(idx)
            kept_lists[field] = kept
            positions[field] = kept_at
        batch = handler({**data, **kept_lists})
        batch._positions = positions
        batch._rejected = rejected
        return batch

    def positions(self, field: str) -> list[int]:
        """Payload index of each item kept in `field` (reads, sightings, object_counts, events)."""
        kept_at = self._positions.get(field)
        return list(kept_at) if kept_at is not None else list(range(len(getattr(self, field))))

    @property
    def rejected_items(self) -> list[dict[str, Any]]:
        """Items left out because they failed their own schema: `{index, kind, reason}` (§7.2)."""
        return [dict(r) for r in self._rejected]


class EventsBatch(ApiModel):
    camera_id: int
    events: list[EventIn] = Field(default_factory=list)


class ObjectCountsBody(ApiModel):
    camera_id: int
    object_counts: list[ObjectCountIn] = Field(default_factory=list)


class OverlayBox(ApiModel):
    """One box of a live overlay frame: ``[x, y, w, h]`` as fractions (0..1) of the decoded frame."""

    box: list[float]
    conf: float = Field(ge=0.0, le=1.0)
    label: str | None = Field(None, max_length=16)   # plate: the track's best valid read
    track: int | None = None                         # plate: worker track id
    cls: str | None = None                           # object: OBJECT_CLASSES

    @field_validator("box")
    @classmethod
    def _box(cls, v: list[float]) -> list[float]:
        if len(v) != 4 or any(not 0.0 <= float(x) <= 1.0 for x in v):
            raise ValueError("box must be [x, y, w, h] with every value in 0..1")
        return [float(x) for x in v]

    @field_validator("cls")
    @classmethod
    def _cls(cls, v: str | None) -> str | None:
        if v is not None and v not in OBJECT_CLASSES:
            raise ValueError(f"must be one of {', '.join(OBJECT_CLASSES)}")
        return v


class OverlayFrame(ApiModel):
    camera_id: int
    captured_at: str
    frame_w: int = Field(ge=1, le=16384)
    frame_h: int = Field(ge=1, le=16384)
    plates: list[OverlayBox] = Field(default_factory=list, max_length=16)
    objects: list[OverlayBox] = Field(default_factory=list, max_length=64)
    objects_at: str | None = None


class OverlayBatch(ApiModel):
    """POST /internal/overlay: the newest frame per watched camera (never stored, relayed on /ws/reads/{id})."""

    frames: list[OverlayFrame] = Field(default_factory=list, max_length=64)


class HeartbeatCamera(ApiModel):
    id: int
    state: str = "running"
    fps_actual: float | None = None
    frames: int | None = None
    reads: int | None = None
    last_frame_at: str | None = None
    decoder_restarts: int | None = None
    last_error: str | None = None


class Heartbeat(ApiModel):
    worker_id: str = Field(min_length=1, max_length=64)
    mode: str = Field("live", pattern="^(live|preindex)$")
    version: str = Field("unknown", max_length=32)
    gpu: bool = False
    cpu_flag: bool | None = None
    started_at: str | None = None
    cameras: list[HeartbeatCamera] = Field(default_factory=list)
    detector: str | None = None
    object_detect: bool | None = None
    extra: dict[str, Any] | None = None
