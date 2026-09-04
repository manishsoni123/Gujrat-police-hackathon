"""Key/value settings with env-seeded defaults, secrets masking and validation (CONTRACT §4.2, §5.20)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as env
from app.core.errors import validation_error
from app.core.tz import utcnow
from app.db.models import Setting

log = logging.getLogger("sentinel.settings")

MASK = "********"

DEFAULT_FIELD_MAP: dict[str, list[str]] = {
    "external_id": ["id", "camera_id", "cameraId", "stream_id", "uid"],
    "name": ["name", "camera_name", "title", "label"],
    "department_code": ["department", "dept", "department_name", "owner_department", "agency"],
    "district": ["district", "location.district", "city"],
    "lat": ["location.lat", "lat", "latitude", "gps.lat", "geo.lat", "coordinates.lat"],
    "lon": ["location.lon", "location.lng", "lon", "lng", "longitude", "gps.lon", "gps.lng", "geo.lon", "coordinates.lon"],
    "address": ["location.address", "address", "location.name", "place"],
    "type": ["type", "camera_type", "kind"],
    "codec": ["codec", "video_codec", "stream_properties.codec", "properties.codec"],
    "resolution": ["resolution", "stream_properties.resolution", "properties.resolution"],
    "fps": ["fps", "frame_rate", "stream_properties.fps", "properties.fps"],
    "live": ["live", "is_live", "online", "status.live", "active"],
    "rtsp_url": ["rtsp_url", "rtsp", "urls.rtsp", "streams.rtsp", "rtspUrl"],
    "whep_url": ["whep_url", "whep", "urls.whep", "streams.webrtc", "webrtc_url"],
    "hls_url": ["hls_url", "hls", "urls.hls", "streams.hls", "hlsUrl"],
    "police_station": ["police_station", "ps"],
    "install_date": ["install_date", "installed_on"],
}

DEFAULT_DEPT_ALIASES: dict[str, str] = {
    "police": "POLICE", "gujarat police": "POLICE", "home": "POLICE",
    "health": "HEALTH", "health & family welfare": "HEALTH", "health and family welfare": "HEALTH", "hospital": "HEALTH",
    "gsrtc": "GSRTC", "transport corporation": "GSRTC",
    "panchayat": "PANCHAYAT", "gram panchayat": "PANCHAYAT", "rural development": "PANCHAYAT",
    "municipal corporation": "MUNICIPAL", "municipal": "MUNICIPAL", "amc": "MUNICIPAL", "urban development": "MUNICIPAL", "smart city": "MUNICIPAL",
    "rto": "RTO", "transport": "RTO",
    "food & civil supplies": "FCS", "food and civil supplies": "FCS", "fcs": "FCS",
}


def _v_url(v: Any) -> str:
    s = str(v or "").strip().rstrip("/")
    if not s.lower().startswith(("http://", "https://")):
        raise ValueError("must be an http(s) URL")
    return s


def _v_enum(*allowed: str) -> Callable[[Any], str]:
    def f(v: Any) -> str:
        s = str(v or "").strip().lower()
        if s not in allowed:
            raise ValueError(f"must be one of {', '.join(allowed)}")
        return s

    return f


def _v_str(v: Any) -> str:
    return "" if v is None else str(v)


def _v_num(lo: float, hi: float, integer: bool = False) -> Callable[[Any], float | int]:
    def f(v: Any) -> float | int:
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise ValueError("must be a number")
        if not lo <= n <= hi:
            raise ValueError(f"must be between {lo} and {hi}")
        return int(n) if integer else n

    return f


def _v_field_map(v: Any) -> dict[str, list[str]]:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            raise ValueError("must be a JSON object")
    if not isinstance(v, dict):
        raise ValueError("must be an object of target → [candidate paths]")
    out: dict[str, list[str]] = {}
    for k, cands in v.items():
        if isinstance(cands, str):
            cands = [cands]
        if not isinstance(cands, list) or not all(isinstance(c, str) for c in cands):
            raise ValueError(f"'{k}' must map to a list of dotted paths")
        out[str(k)] = cands
    return out


def _v_aliases(v: Any) -> dict[str, str]:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            raise ValueError("must be a JSON object")
    if not isinstance(v, dict):
        raise ValueError("must be an object of alias → department code")
    return {str(k).strip().lower(): str(val).strip().upper() for k, val in v.items()}


def _v_map_center(v: Any) -> list[float]:
    if isinstance(v, str):
        v = json.loads(v)
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError("must be [lat, lon]")
    return [float(v[0]), float(v[1])]


@dataclass(frozen=True)
class SettingDef:
    key: str
    default: Callable[[], Any]
    validator: Callable[[Any], Any]
    is_secret: bool = False
    public: bool = False


REGISTRY: dict[str, SettingDef] = {
    d.key: d
    for d in [
        SettingDef("catalogue.base_url", lambda: env.SANDBOX_BASE_URL, _v_url),
        SettingDef("catalogue.auth_type", lambda: env.SANDBOX_AUTH_TYPE, _v_enum("none", "basic", "bearer", "header")),
        SettingDef("catalogue.auth_username", lambda: env.SANDBOX_USERNAME, _v_str),
        SettingDef("catalogue.auth_password", lambda: env.SANDBOX_PASSWORD, _v_str, is_secret=True),
        SettingDef("catalogue.auth_header", lambda: env.SANDBOX_AUTH_HEADER, _v_str, is_secret=True),
        SettingDef("catalogue.timeout_s", lambda: env.SANDBOX_TIMEOUT_S, _v_num(1, 600)),
        SettingDef("catalogue.field_map", lambda: DEFAULT_FIELD_MAP, _v_field_map),
        SettingDef("catalogue.department_aliases", lambda: DEFAULT_DEPT_ALIASES, _v_aliases),
        SettingDef("retention.days_frames", lambda: env.RETENTION_DAYS_FRAMES, _v_num(1, 3650, True)),
        SettingDef("retention.days_reads", lambda: env.RETENTION_DAYS_READS, _v_num(1, 3650, True)),
        SettingDef("retention.days_clips", lambda: env.RETENTION_DAYS_CLIPS, _v_num(1, 3650, True)),
        SettingDef("gap.coverage_radius_m", lambda: env.GAP_COVERAGE_RADIUS_M, _v_num(10, 5000, True), public=True),
        SettingDef("gap.poi_radius_m", lambda: env.GAP_POI_RADIUS_M, _v_num(10, 10000, True), public=True),
        SettingDef("gap.grid_m", lambda: env.GAP_GRID_M, _v_num(100, 5000, True), public=True),
        SettingDef("gap.ageing_years", lambda: env.AGEING_YEARS, _v_num(1, 50, True), public=True),
        SettingDef("alerts.suppression_s", lambda: env.ALERT_SUPPRESSION_SECONDS, _v_num(0, 3600, True)),
        SettingDef("alerts.re_alert_minutes", lambda: env.ALERT_RE_ALERT_MINUTES, _v_num(0, 1440, True)),
        SettingDef("alerts.fuzzy_min_conf", lambda: env.FUZZY_ALERT_MIN_CONF, _v_num(0, 1)),
        SettingDef("alerts.escalate_minutes", lambda: env.ALERT_ESCALATE_MINUTES, _v_num(1, 1440, True), public=True),
        SettingDef("route.speed_flag_kmh", lambda: env.ROUTE_SPEED_FLAG_KMH, _v_num(10, 1000), public=True),
        SettingDef("route.default_window_h", lambda: env.ROUTE_DEFAULT_WINDOW_HOURS, _v_num(1, 720, True), public=True),
        SettingDef("notify.telegram_bot_token", lambda: env.TELEGRAM_BOT_TOKEN, _v_str, is_secret=True),
        SettingDef("notify.telegram_chat_id", lambda: env.TELEGRAM_CHAT_ID, _v_str),
        SettingDef("notify.telegram_min_priority", lambda: "high", _v_enum("critical", "high", "medium", "low")),
        SettingDef("ui.product_name", lambda: env.PRODUCT_NAME, _v_str, public=True),
        SettingDef("ui.map_center", lambda: [23.2156, 72.6369], _v_map_center, public=True),
        SettingDef("ui.map_zoom", lambda: 8, _v_num(1, 20, True), public=True),
    ]
}

_cache: dict[str, Any] = {}
_meta: dict[str, dict[str, Any]] = {}


def get(key: str) -> Any:
    if key in _cache:
        return _cache[key]
    d = REGISTRY.get(key)
    return d.default() if d else None


def get_int(key: str) -> int:
    return int(get(key))


def get_float(key: str) -> float:
    return float(get(key))


def get_all() -> dict[str, Any]:
    return {k: get(k) for k in REGISTRY}


def public_values() -> dict[str, Any]:
    return {k: get(k) for k, d in REGISTRY.items() if d.public and not d.is_secret}


def masked(key: str) -> Any:
    d = REGISTRY[key]
    v = get(key)
    if d.is_secret:
        return MASK if v else ""
    return v


async def load(db: AsyncSession) -> None:
    """Seed missing keys from env defaults and load everything into the cache."""
    rows = {r.key: r for r in (await db.execute(select(Setting))).scalars().all()}
    changed = False
    for key, d in REGISTRY.items():
        if key not in rows:
            db.add(Setting(key=key, value=d.default(), is_secret=d.is_secret, updated_at=utcnow()))
            changed = True
    if changed:
        await db.commit()
        rows = {r.key: r for r in (await db.execute(select(Setting))).scalars().all()}
    _cache.clear()
    _meta.clear()
    for key, r in rows.items():
        _cache[key] = r.value
        _meta[key] = {"updated_by": r.updated_by, "updated_at": r.updated_at, "is_secret": r.is_secret}


def meta(key: str) -> dict[str, Any]:
    return _meta.get(key, {"updated_by": None, "updated_at": None, "is_secret": REGISTRY[key].is_secret})


def validate_values(values: dict[str, Any]) -> dict[str, Any]:
    """Validate incoming PUT values; masked secrets are dropped (= unchanged)."""
    errors: list[dict[str, str]] = []
    clean: dict[str, Any] = {}
    for key, val in values.items():
        d = REGISTRY.get(key)
        if d is None:
            errors.append({"field": key, "message": "unknown setting key"})
            continue
        if d.is_secret and val == MASK:
            continue
        try:
            clean[key] = d.validator(val)
        except (ValueError, TypeError) as exc:
            errors.append({"field": key, "message": str(exc)})
    if errors:
        raise validation_error("Invalid settings", errors)
    return clean


async def set_many(db: AsyncSession, values: dict[str, Any], user_id: int | None) -> tuple[dict, dict]:
    """Persist validated values; returns (before, after) with secrets redacted for audit."""
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    now = utcnow()
    for key, val in values.items():
        d = REGISTRY[key]
        row = await db.get(Setting, key)
        old = row.value if row else d.default()
        before[key] = MASK if d.is_secret else old
        after[key] = MASK if d.is_secret else val
        if row is None:
            db.add(Setting(key=key, value=val, is_secret=d.is_secret, updated_by=user_id, updated_at=now))
        else:
            row.value = val
            row.updated_by = user_id
            row.updated_at = now
        _cache[key] = val
        _meta[key] = {"updated_by": user_id, "updated_at": now, "is_secret": d.is_secret}
    await db.commit()
    return before, after


async def set_internal(db: AsyncSession, key: str, value: Any, is_secret: bool = False) -> None:
    """Store a non-registry key (e.g. seed password hashes)."""
    row = await db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, is_secret=is_secret, updated_at=utcnow()))
    else:
        row.value = value
        row.updated_at = utcnow()
    await db.commit()


async def get_internal(db: AsyncSession, key: str) -> Any:
    row = await db.get(Setting, key)
    return row.value if row else None
