"""Settings and webhook bodies (CONTRACT §5.20)."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from app.core.urlguard import check_outbound_url
from app.db.models import WEBHOOK_EVENTS
from app.schemas.common import ApiModel


class SettingsUpdate(ApiModel):
    values: dict[str, Any] = Field(default_factory=dict)


class CatalogueTestRequest(ApiModel):
    # generic_json / mock
    base_url: str | None = None
    auth_type: str | None = None
    auth_username: str | None = None
    auth_password: str | None = None
    auth_header: str | None = None
    timeout_s: float | None = None
    field_map: dict[str, list[str]] | None = None
    # sentinel_portal (organiser sandbox): every value optional, "********" = keep the stored secret
    source: str | None = None
    camera_id: str | None = Field(None, max_length=64)
    stream_host: str | None = None
    rtsp_port: int | None = Field(None, ge=1, le=65535)
    whep_port: int | None = Field(None, ge=1, le=65535)
    stream_email: str | None = None
    stream_password: str | None = None
    portal_url: str | None = None
    portal_email: str | None = None
    portal_password: str | None = None
    check_portal: bool = True


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
        return check_outbound_url(v)  # SSRF guard (core/urlguard.py)

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
        if v is None:
            return None
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return check_outbound_url(v)

    @field_validator("event_types")
    @classmethod
    def _events(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        bad = [e for e in v if e not in WEBHOOK_EVENTS]
        if bad:
            raise ValueError(f"unknown event types: {', '.join(bad)}")
        return v
