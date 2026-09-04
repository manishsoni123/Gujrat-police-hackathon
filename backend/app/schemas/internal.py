"""ANPR worker → API payloads (CONTRACT §7)."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from app.db.models import OBJECT_CLASSES
from app.schemas.common import ApiModel


class ReadIn(ApiModel):
    captured_at: str
    stream_pts: float | None = None
    frame_index: int | None = None
    plate_raw: str = Field(max_length=64)
    plate_norm: str | None = Field(None, max_length=16)
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
    plate_norm: str = Field(max_length=16)
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


class DetectionBatch(ApiModel):
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

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in ("live", "preindex"):
            raise ValueError("must be live or preindex")
        return v


class EventsBatch(ApiModel):
    camera_id: int
    events: list[EventIn] = Field(default_factory=list)


class ObjectCountsBody(ApiModel):
    camera_id: int
    object_counts: list[ObjectCountIn] = Field(default_factory=list)


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
    worker_id: str = Field(max_length=64)
    mode: str = "live"
    version: str = Field("unknown", max_length=32)
    gpu: bool = False
    cpu_flag: bool | None = None
    started_at: str | None = None
    cameras: list[HeartbeatCamera] = Field(default_factory=list)
    detector: str | None = None
    object_detect: bool | None = None
    extra: dict[str, Any] | None = None
