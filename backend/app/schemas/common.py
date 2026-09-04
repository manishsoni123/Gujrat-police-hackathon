"""Shared Pydantic pieces: pagination, time-window parsing, enums (CONTRACT §3)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import validation_error
from app.core.tz import parse_iso, utcnow

T = TypeVar("T")


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True, str_strip_whitespace=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class PageParams:
    """Dependency: page/page_size/sort/order with a per-endpoint sort whitelist."""

    def __init__(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(25, ge=1, le=500),
        sort: str | None = Query(None),
        order: str | None = Query(None, pattern="^(asc|desc)$"),
    ) -> None:
        self.page = page
        self.page_size = page_size
        self.sort = sort
        self.order = order

    def resolve(self, whitelist: dict[str, Any], default_sort: str, default_order: str, max_size: int = 200):
        if self.page_size > max_size:
            self.page_size = max_size
        sort = self.sort or default_sort
        if sort not in whitelist:
            raise validation_error(
                "Unknown sort column",
                [{"field": "sort", "message": f"must be one of {', '.join(whitelist)}"}],
            )
        order = self.order or default_order
        return sort, order

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def parse_window(
    from_: str | None,
    to: str | None,
    default_hours: float | None = 24,
    max_days: float | None = None,
    from_required: bool = False,
) -> tuple[datetime | None, datetime]:
    """Inclusive ISO-8601 window; `to` defaults to now, `from` to now − default_hours."""
    try:
        t_to = parse_iso(to) or utcnow()
        t_from = parse_iso(from_)
    except ValueError:
        raise validation_error("Invalid timestamp", [{"field": "from/to", "message": "must be ISO-8601"}])
    if t_from is None:
        if from_required:
            raise validation_error("from is required", [{"field": "from", "message": "field required"}])
        if default_hours is not None:
            t_from = t_to - timedelta(hours=default_hours)
    if t_from is not None and t_from > t_to:
        raise validation_error("Invalid window", [{"field": "from", "message": "must be before to"}])
    if max_days is not None and t_from is not None and (t_to - t_from) > timedelta(days=max_days):
        raise validation_error("Window too large", [{"field": "from", "message": f"window must be ≤ {max_days} days"}])
    return t_from, t_to


def csv_list(value: str | None) -> list[str] | None:
    if value is None or value == "":
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


class OkResponse(ApiModel):
    ok: bool = True


class SavedResponse(ApiModel):
    saved: int = Field(ge=0)
