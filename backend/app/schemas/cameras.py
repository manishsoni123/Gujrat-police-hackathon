"""Camera import row (CONTRACT §5 `CameraImportRow`) and related bodies.

The validators here are the single source of per-field rules used by CSV, bulk JSON,
sandbox import and the manual form. Field-level errors are raised as pydantic
ValueErrors and converted to `{field, message}` entries by the importer.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from pydantic import Field, field_validator, model_validator

from app.db.models import CAMERA_TYPES, CONNECTIVITY, LOCATION_CONFIDENCE, MAINTENANCE, OWNERSHIPS
from app.schemas.common import ApiModel

GUJARAT_BBOX = (20.1, 24.8, 68.1, 74.5)  # lat_min, lat_max, lon_min, lon_max
_RES_RE = re.compile(r"^\d{2,5}x\d{2,5}$")
_TRUE = {"true", "1", "yes", "y", "t", "live", "online", "on"}
_FALSE = {"false", "0", "no", "n", "f", "offline", "off", "dead"}

CODEC_MAP = {
    "h264": "H264", "avc": "H264", "h.264": "H264", "avc1": "H264", "x264": "H264",
    "h265": "H265", "hevc": "H265", "h.265": "H265", "hvc1": "H265", "hev1": "H265", "x265": "H265",
    "mjpeg": "MJPEG", "mjpg": "MJPEG", "jpeg": "MJPEG",
}


def parse_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    raise ValueError("must be true/false/1/0/yes/no")


def normalise_codec(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "UNKNOWN"
    s = str(value).strip().lower()
    return CODEC_MAP.get(s, "UNKNOWN")


def normalise_resolution(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        w, h = value.get("width"), value.get("height")
        if w and h:
            return f"{int(w)}x{int(h)}"
        raise ValueError("resolution object needs width and height")
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{int(value[0])}x{int(value[1])}"
    s = str(value).strip().lower().replace("×", "x").replace(" ", "")
    if not _RES_RE.match(s):
        raise ValueError("must look like 1920x1080")
    return s


def title_case_district(value: str | None) -> str | None:
    from app.services.districts import canonical_district

    if value is None or str(value).strip() == "":
        return None
    return canonical_district(str(value))


def _empty_to_none(v: Any) -> Any:
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


def json_dumps(v: Any) -> str:
    import json

    return json.dumps(v, default=str)


class CameraImportRow(ApiModel):
    external_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    department_code: str | None = None
    type: str | None = None
    ownership: str | None = None
    lat: float | None = None
    lon: float | None = None
    address: str | None = Field(None, max_length=255)
    district: str | None = Field(None, max_length=64)
    police_station: str | None = Field(None, max_length=120)
    ward: str | None = Field(None, max_length=64)
    rtsp_url: str | None = Field(None, max_length=512)
    whep_url: str | None = Field(None, max_length=512)
    hls_url: str | None = Field(None, max_length=512)
    codec: str | None = None
    resolution: str | None = None
    fps: int | None = Field(None, ge=1, le=60)
    live: bool | None = None
    storage_location: str | None = Field(None, max_length=120)
    retention_days: int | None = Field(None, ge=0, le=3650)
    install_date: date | None = None
    vendor: str | None = Field(None, max_length=80)
    model: str | None = Field(None, max_length=80)
    heading_deg: int | None = Field(None, ge=0, le=359)
    fov_deg: int | None = Field(None, ge=1, le=360)
    connectivity_type: str | None = None
    bandwidth_kbps: int | None = Field(None, ge=0)
    vms_platform: str | None = Field(None, max_length=80)
    nvr_id: str | None = Field(None, max_length=64)
    onvif_host: str | None = Field(None, max_length=120)
    amc_vendor: str | None = Field(None, max_length=120)
    amc_expiry: date | None = None
    maintenance_status: str | None = None
    anpr_enabled: bool | None = None
    record_enabled: bool | None = None
    # Organiser-sandbox enrichment (Amendments 2026-09-05): exact | approx | guess, and a small JSON bag
    location_confidence: str | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _blank_to_none(cls, data: Any) -> Any:
        if isinstance(data, dict):
            out: dict[str, Any] = {}
            for k, v in data.items():
                if k in ("external_id", "name"):
                    out[k] = v.strip() if isinstance(v, str) else v
                else:
                    out[k] = _empty_to_none(v)
            return out
        return data

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip().lower()
        if s not in CAMERA_TYPES:
            raise ValueError(f"must be one of {', '.join(CAMERA_TYPES)}")
        return s

    @field_validator("ownership", mode="before")
    @classmethod
    def _ownership(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip().lower()
        if s not in OWNERSHIPS:
            raise ValueError(f"must be one of {', '.join(OWNERSHIPS)}")
        return s

    @field_validator("connectivity_type", mode="before")
    @classmethod
    def _conn(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip().lower()
        if s not in CONNECTIVITY:
            raise ValueError(f"must be one of {', '.join(CONNECTIVITY)}")
        return s

    @field_validator("maintenance_status", mode="before")
    @classmethod
    def _maint(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip().lower()
        if s not in MAINTENANCE:
            raise ValueError(f"must be one of {', '.join(MAINTENANCE)}")
        return s

    @field_validator("location_confidence", mode="before")
    @classmethod
    def _loc_conf(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip().lower()
        if s not in LOCATION_CONFIDENCE:
            raise ValueError(f"must be one of {', '.join(LOCATION_CONFIDENCE)}")
        return s

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, v: Any) -> dict[str, Any] | None:
        if v is None:
            return None
        if isinstance(v, str):
            import json

            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                raise ValueError("must be a JSON object")
        if not isinstance(v, dict):
            raise ValueError("must be an object")
        if len(json_dumps(v)) > 16 * 1024:
            raise ValueError("must be at most 16 KB")
        return v

    @field_validator("lat", mode="before")
    @classmethod
    def _lat(cls, v: Any) -> float | None:
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise ValueError("must be a number")
        if not -90 <= f <= 90:
            raise ValueError(f"must be between -90 and 90 (got {v})")
        return round(f, 6)

    @field_validator("lon", mode="before")
    @classmethod
    def _lon(cls, v: Any) -> float | None:
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise ValueError("must be a number")
        if not -180 <= f <= 180:
            raise ValueError(f"must be between -180 and 180 (got {v})")
        return round(f, 6)

    @field_validator("rtsp_url")
    @classmethod
    def _rtsp(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not v.lower().startswith(("rtsp://", "rtsps://")):
            raise ValueError("must start with rtsp:// or rtsps://")
        return v

    @field_validator("whep_url", "hls_url")
    @classmethod
    def _http(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return v

    @field_validator("codec", mode="before")
    @classmethod
    def _codec(cls, v: Any) -> str | None:
        if v is None:
            return None
        return normalise_codec(v)

    @field_validator("resolution", mode="before")
    @classmethod
    def _resolution(cls, v: Any) -> str | None:
        return normalise_resolution(v)

    @field_validator("live", "anpr_enabled", "record_enabled", mode="before")
    @classmethod
    def _bools(cls, v: Any) -> bool | None:
        return parse_bool(v)

    @field_validator("fps", "retention_days", "heading_deg", "fov_deg", "bandwidth_kbps", mode="before")
    @classmethod
    def _ints(cls, v: Any) -> int | None:
        if v is None:
            return None
        if isinstance(v, bool):
            raise ValueError("must be an integer")
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise ValueError("must be an integer")
        if f != int(f):
            raise ValueError("must be an integer")
        return int(f)

    @field_validator("install_date", "amc_expiry", mode="before")
    @classmethod
    def _dates(cls, v: Any) -> date | None:
        if v is None:
            return None
        if isinstance(v, date):
            return v
        s = str(v).strip()[:10]
        try:
            return date.fromisoformat(s)
        except ValueError:
            raise ValueError("must be YYYY-MM-DD")

    @field_validator("district", mode="before")
    @classmethod
    def _district(cls, v: Any) -> str | None:
        if v is None:
            return None
        return title_case_district(str(v))

    @field_validator("department_code", mode="before")
    @classmethod
    def _dept(cls, v: Any) -> str | None:
        if v is None:
            return None
        return str(v).strip()

    @model_validator(mode="after")
    def _latlon_pair(self) -> "CameraImportRow":
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must both be present or both empty")
        return self

    def outside_gujarat(self) -> bool:
        if self.lat is None or self.lon is None:
            return False
        lat_min, lat_max, lon_min, lon_max = GUJARAT_BBOX
        return not (lat_min <= self.lat <= lat_max and lon_min <= self.lon <= lon_max)


class CameraCreate(CameraImportRow):
    source: str | None = None

    @field_validator("source")
    @classmethod
    def _source(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in ("manual", "own"):
            raise ValueError("must be 'manual' or 'own'")
        return v


class CameraUpdate(ApiModel):
    """Partial update: identical fields to CameraImportRow but everything optional."""

    name: str | None = Field(None, min_length=1, max_length=160)
    external_id: str | None = Field(None, min_length=1, max_length=64)
    department_code: str | None = None
    department_id: int | None = None
    type: str | None = None
    ownership: str | None = None
    lat: float | None = None
    lon: float | None = None
    address: str | None = None
    district: str | None = None
    police_station: str | None = None
    ward: str | None = None
    rtsp_url: str | None = None
    whep_url: str | None = None
    hls_url: str | None = None
    codec: str | None = None
    resolution: str | None = None
    fps: int | None = None
    live: bool | None = None
    storage_location: str | None = None
    retention_days: int | None = None
    install_date: date | None = None
    vendor: str | None = None
    model: str | None = None
    heading_deg: int | None = None
    fov_deg: int | None = None
    connectivity_type: str | None = None
    bandwidth_kbps: int | None = None
    vms_platform: str | None = None
    nvr_id: str | None = None
    onvif_host: str | None = None
    amc_vendor: str | None = None
    amc_expiry: date | None = None
    maintenance_status: str | None = None
    anpr_enabled: bool | None = None
    record_enabled: bool | None = None
    location_confidence: str | None = None
    metadata: dict[str, Any] | None = None


class MaintenanceUpdate(ApiModel):
    maintenance_status: str
    last_maintenance_at: str | None = None
    maintenance_note: str | None = Field(None, max_length=255)
    amc_vendor: str | None = Field(None, max_length=120)
    amc_expiry: date | None = None

    @field_validator("maintenance_status")
    @classmethod
    def _m(cls, v: str) -> str:
        if v not in MAINTENANCE:
            raise ValueError(f"must be one of {', '.join(MAINTENANCE)}")
        return v


class SandboxImportRequest(ApiModel):
    measure_first_stream: bool = False
    dry_run: bool = False
    # sentinel_portal source only: ffprobe every camera (codec/resolution/fps/live) before the upsert
    probe: bool = True


class BulkImportRequest(ApiModel):
    cameras: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)
