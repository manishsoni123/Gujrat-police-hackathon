"""Bodies for watchlist, alerts, events, route confirm, clips, zones, QA (CONTRACT §5.10–§5.17)."""

from __future__ import annotations

from datetime import time
from typing import Any

from pydantic import Field, field_validator

from app.db.models import (
    ENTITY_TYPES,
    MANUAL_EVENT_TYPES,
    OBJECT_CLASSES,
    OUTCOMES,
    PRIORITIES,
    REASONS,
    WATCHLIST_SOURCES,
)
from app.schemas.common import ApiModel


def _enum(value: str | None, allowed: tuple[str, ...], allow_none: bool = False) -> str | None:
    if value is None:
        if allow_none:
            return None
        raise ValueError("field required")
    v = str(value).strip().lower()
    if v not in allowed:
        raise ValueError(f"must be one of {', '.join(allowed)}")
    return v


class WatchlistCreate(ApiModel):
    entity_type: str = "vehicle"
    plate: str | None = Field(None, max_length=32)
    name: str | None = Field(None, max_length=160)
    reason: str
    priority: str = "medium"
    source: str = "manual"
    notes: str | None = None
    expires_at: str | None = None
    is_active: bool = True

    @field_validator("entity_type", mode="before")
    @classmethod
    def _et(cls, v: Any) -> str:
        return _enum(v, ENTITY_TYPES) or "vehicle"

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, v: Any) -> str:
        return _enum(v, REASONS) or "other"

    @field_validator("priority", mode="before")
    @classmethod
    def _prio(cls, v: Any) -> str:
        return _enum(v if v not in (None, "") else "medium", PRIORITIES) or "medium"

    @field_validator("source", mode="before")
    @classmethod
    def _source(cls, v: Any) -> str:
        return _enum(v if v not in (None, "") else "manual", WATCHLIST_SOURCES) or "manual"


class WatchlistUpdate(ApiModel):
    entity_type: str | None = None
    plate: str | None = Field(None, max_length=32)
    name: str | None = Field(None, max_length=160)
    reason: str | None = None
    priority: str | None = None
    source: str | None = None
    notes: str | None = None
    expires_at: str | None = None
    is_active: bool | None = None

    @field_validator("entity_type", mode="before")
    @classmethod
    def _et(cls, v: Any) -> str | None:
        return _enum(v, ENTITY_TYPES, allow_none=True)

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, v: Any) -> str | None:
        return _enum(v, REASONS, allow_none=True)

    @field_validator("priority", mode="before")
    @classmethod
    def _prio(cls, v: Any) -> str | None:
        return _enum(v, PRIORITIES, allow_none=True)

    @field_validator("source", mode="before")
    @classmethod
    def _source(cls, v: Any) -> str | None:
        return _enum(v, WATCHLIST_SOURCES, allow_none=True)


class AlertAck(ApiModel):
    note: str | None = Field(None, max_length=2000)


class AlertClose(ApiModel):
    note: str | None = Field(None, max_length=2000)
    outcome: str = "resolved"

    @field_validator("outcome", mode="before")
    @classmethod
    def _outcome(cls, v: Any) -> str:
        return _enum(v if v not in (None, "") else "resolved", OUTCOMES) or "resolved"


class EventCreate(ApiModel):
    camera_id: int
    type: str
    occurred_at: str | None = None
    note: str | None = Field(None, max_length=2000)
    sighting_id: int | None = None
    read_id: int | None = None

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, v: Any) -> str:
        return _enum(v, MANUAL_EVENT_TYPES) or "other"


class RouteDecision(ApiModel):
    sighting_id: int
    decision: str

    @field_validator("decision", mode="before")
    @classmethod
    def _d(cls, v: Any) -> str:
        return _enum(v, ("confirmed", "rejected")) or "confirmed"


class RouteConfirmRequest(ApiModel):
    decisions: list[RouteDecision] = Field(default_factory=list, max_length=500)


class ClipCreate(ApiModel):
    camera_id: int
    start_at: str
    duration_s: int = Field(30, ge=1, le=120)
    alert_id: int | None = None
    sighting_id: int | None = None


class ZoneBase(ApiModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    polygon: list[list[float]] | None = None
    active_from: time | None = None
    active_to: time | None = None
    classes: list[str] | None = None
    dwell_s: float | None = Field(None, ge=0.1, le=600)
    priority: str | None = None
    is_active: bool | None = None

    @field_validator("polygon")
    @classmethod
    def _polygon(cls, v: list[list[float]] | None) -> list[list[float]] | None:
        if v is None:
            return None
        if len(v) < 3:
            raise ValueError("polygon needs at least 3 points")
        for pt in v:
            if len(pt) != 2 or not all(0.0 <= float(c) <= 1.0 for c in pt):
                raise ValueError("points must be [x, y] in normalised 0..1 coordinates")
        return [[float(p[0]), float(p[1])] for p in v]

    @field_validator("classes")
    @classmethod
    def _classes(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        bad = [c for c in v if c not in OBJECT_CLASSES]
        if bad:
            raise ValueError(f"unknown classes: {', '.join(bad)}")
        return v

    @field_validator("priority", mode="before")
    @classmethod
    def _prio(cls, v: Any) -> str | None:
        return _enum(v, PRIORITIES, allow_none=True)


class ZoneCreate(ZoneBase):
    camera_id: int
    name: str = Field(min_length=1, max_length=80)
    polygon: list[list[float]]


class ZoneUpdate(ZoneBase):
    pass


class QaLabelItem(ApiModel):
    read_id: int
    true_plate: str = Field("", max_length=32)


class QaLabelsRequest(ApiModel):
    labels: list[QaLabelItem] = Field(default_factory=list, max_length=500)
