"""Settings and webhook bodies (CONTRACT §5.20)."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from app.db.models import WEBHOOK_EVENTS
from app.schemas.common import ApiModel


class SettingsUpdate(ApiModel):
    values: dict[str, Any] = Field(default_factory=dict)


class CatalogueTestRequest(ApiModel):
    base_url: str | None = None
    auth_type: str | None = None
    auth_username: str | None = None
    auth_password: str | None = None
    auth_header: str | None = None
    timeout_s: float | None = None
    field_map: dict[str, list[str]] | None = None


class WebhookCreate(ApiModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=512)
    secret: str | None = Field(None, max_length=128)
    event_types: list[str] = Field(default_factory=list)
    is_active: bool = True

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return v

    @field_validator("event_types")
    @classmethod
    def _events(cls, v: list[str]) -> list[str]:
        bad = [e for e in v if e not in WEBHOOK_EVENTS]
        if bad:
            raise ValueError(f"unknown event types: {', '.join(bad)}")
        return v


class WebhookUpdate(ApiModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    url: str | None = Field(None, min_length=1, max_length=512)
    secret: str | None = Field(None, max_length=128)
    event_types: list[str] | None = None
    is_active: bool | None = None

    @field_validator("url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if v is not None and not v.lower().startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return v

    @field_validator("event_types")
    @classmethod
    def _events(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        bad = [e for e in v if e not in WEBHOOK_EVENTS]
        if bad:
            raise ValueError(f"unknown event types: {', '.join(bad)}")
        return v
