"""ORM → contract JSON shapes (CONTRACT §5). Sync functions over pre-warmed lookup caches."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.hashing import media_url
from app.core.tz import fmt_ist_time, iso_z, utcnow
from app.db.models import Alert, ApiKey, AuditLog, Camera, Clip, Event, PlateRead, ReportFile, Sighting, User, Watchlist, Webhook, Zone
from app.services import lookups
from app.services import settings_service as cfg
from app.services.mediamtx_client import play_path, relay_path
from app.services.plates import format_plate

RECORDING_RETENTION_H = 12
_CRED_RE = re.compile(r"^(rtsps?://[^:/@]+:)[^@]*(@)")

EVENT_LABELS = {
    "accident": "Accident", "suspicious": "Suspicious activity", "checkpoint": "Checkpoint",
    "other": "Other", "watchlist_hit": "Watchlist hit", "loop_reset": "Stream loop reset",
    "intrusion": "Intrusion", "camera_offline": "Camera offline", "camera_online": "Camera online",
}

REASON_LABELS = {
    "stolen": "Stolen vehicle", "wanted": "Wanted", "blacklisted": "Blacklisted", "missing": "Missing",
    "suspect": "Suspect", "arrested": "Arrested person's vehicle", "unidentified_body": "Unidentified body case", "other": "Watchlist",
}


async def warm(db: AsyncSession) -> None:
    await lookups.departments(db)
    await lookups.usernames(db)


def mask_rtsp(url: str | None, is_admin: bool) -> str | None:
    if not url or is_admin:
        return url
    return _CRED_RE.sub(r"\1***\2", url)


def amc_status(expiry: date | None, today: date | None = None) -> str:
    if expiry is None:
        return "none"
    today = today or utcnow().date()
    if expiry < today:
        return "expired"
    if expiry <= today + timedelta(days=30):
        return "expiring"
    return "ok"


def age_years(install_date: date | None) -> float | None:
    if install_date is None:
        return None
    return round((utcnow().date() - install_date).days / 365.25, 1)


def camera_summary(c: Camera) -> dict[str, Any]:
    d = lookups.dept_sync(c.department_id)
    return {
        "id": c.id,
        "external_id": c.external_id,
        "name": c.name,
        "department_id": c.department_id,
        "department_code": d.code if d else None,
        "department_name": d.name if d else None,
        "district": c.district,
        "police_station": c.police_station,
        "lat": c.lat,
        "lon": c.lon,
        "status": c.status,
        "type": c.type,
    }


def camera_full(c: Camera, is_admin: bool = False, uptime_24h_pct: float | None = None) -> dict[str, Any]:
    d = lookups.dept_sync(c.department_id)
    return {
        "id": c.id,
        "source": c.source,
        "external_id": c.external_id,
        "name": c.name,
        "department_id": c.department_id,
        "department_code": d.code if d else None,
        "department_name": d.name if d else None,
        "type": c.type,
        "ownership": c.ownership,
        "lat": c.lat,
        "lon": c.lon,
        "address": c.address,
        "district": c.district,
        "police_station": c.police_station,
        "ward": c.ward,
        "rtsp_url": mask_rtsp(c.rtsp_url, is_admin),
        "whep_url": c.whep_url,
        "hls_url": c.hls_url,
        "relay_path": c.relay_path,
        "play_path": play_path(c.id, c.codec) if c.rtsp_url else None,
        "codec": c.codec,
        "resolution": c.resolution,
        "fps": c.fps,
        "live": c.live,
        "storage_location": c.storage_location,
        "retention_days": c.retention_days,
        "install_date": c.install_date.isoformat() if c.install_date else None,
        "vendor": c.vendor,
        "model": c.model,
        "heading_deg": c.heading_deg,
        "fov_deg": c.fov_deg,
        "connectivity_type": c.connectivity_type,
        "bandwidth_kbps": c.bandwidth_kbps,
        "vms_platform": c.vms_platform,
        "nvr_id": c.nvr_id,
        "onvif_host": c.onvif_host,
        "anpr_enabled": c.anpr_enabled,
        "record_enabled": c.record_enabled,
        "status": c.status,
        "last_seen_at": iso_z(c.last_seen_at),
        "last_status_change_at": iso_z(c.last_status_change_at),
        "maintenance_status": c.maintenance_status,
        "last_maintenance_at": iso_z(c.last_maintenance_at),
        "maintenance_note": c.maintenance_note,
        "amc_vendor": c.amc_vendor,
        "amc_expiry": c.amc_expiry.isoformat() if c.amc_expiry else None,
        "amc_status": amc_status(c.amc_expiry),
        "age_years": age_years(c.install_date),
        "uptime_24h_pct": uptime_24h_pct,
        "created_by": c.created_by,
        "created_by_username": lookups.username_sync(c.created_by),
        "created_via": c.created_via,
        "created_at": iso_z(c.created_at),
        "updated_at": iso_z(c.updated_at),
        "retired_at": iso_z(c.retired_at),
    }


def camera_diff(c: Camera) -> dict[str, Any]:
    """Compact before/after snapshot for audit rows."""
    keys = (
        "external_id", "name", "department_id", "type", "ownership", "lat", "lon", "address", "district",
        "police_station", "ward", "rtsp_url", "codec", "resolution", "fps", "live", "storage_location",
        "retention_days", "install_date", "vendor", "model", "heading_deg", "fov_deg", "connectivity_type",
        "bandwidth_kbps", "vms_platform", "nvr_id", "anpr_enabled", "record_enabled", "status",
        "maintenance_status", "maintenance_note", "amc_vendor", "amc_expiry",
    )
    out: dict[str, Any] = {}
    for k in keys:
        v = getattr(c, k)
        if isinstance(v, (date, datetime)):
            v = v.isoformat()
        out[k] = v
    return out


def recording_available(c: Camera | None, at: datetime | None) -> bool:
    if c is None or not c.record_enabled or at is None:
        return False
    return (utcnow() - at) <= timedelta(hours=RECORDING_RETENTION_H)


def read_item(r: PlateRead, cam: Camera, alert_id: int | None = None, qa_label: dict | None = None) -> dict[str, Any]:
    return {
        "id": r.id,
        "camera": camera_summary(cam),
        "sighting_id": r.sighting_id,
        "captured_at": iso_z(r.captured_at),
        "stream_pts": r.stream_pts,
        "frame_index": r.frame_index,
        "plate_raw": r.plate_raw,
        "plate_norm": r.plate_norm,
        "plate_display": format_plate(r.plate_norm) if r.is_valid_format else r.plate_norm,
        "is_valid_format": r.is_valid_format,
        "confidence": round(float(r.confidence), 3),
        "bbox": list(r.bbox) if r.bbox else None,
        "crop_url": media_url(r.crop_path),
        "crop_sha256": r.crop_sha256,
        "mode": r.mode,
        "watchlist_hit": alert_id is not None,
        "alert_id": alert_id,
        "qa_label": qa_label,
    }


def read_brief(r: PlateRead) -> dict[str, Any]:
    return {
        "id": r.id,
        "plate_raw": r.plate_raw,
        "plate_norm": r.plate_norm,
        "confidence": round(float(r.confidence), 3),
        "captured_at": iso_z(r.captured_at),
        "crop_url": media_url(r.crop_path),
        "crop_sha256": r.crop_sha256,
    }


def sighting_item(s: Sighting, cam: Camera) -> dict[str, Any]:
    return {
        "id": s.id,
        "camera": camera_summary(cam),
        "plate_norm": s.plate_norm,
        "plate_display": format_plate(s.plate_norm) if s.is_valid_format else s.plate_norm,
        "is_valid_format": s.is_valid_format,
        "first_seen": iso_z(s.first_seen),
        "last_seen": iso_z(s.last_seen),
        "read_count": s.read_count,
        "best_conf": round(float(s.best_conf), 3),
        "best_read_id": s.best_read_id,
        "best_crop_url": media_url(s.best_crop_path),
        "frame_url": media_url(s.frame_path),
        "frame_sha256": s.frame_sha256,
        "closed": s.closed,
        "mode": s.mode,
        "recording_available": recording_available(cam, s.first_seen),
    }


def watchlist_brief(w: Watchlist | None) -> dict[str, Any] | None:
    if w is None:
        return None
    return {
        "id": w.id,
        "entity_type": w.entity_type,
        "plate_norm": w.plate_norm,
        "plate_display": format_plate(w.plate_norm) if w.plate_norm else None,
        "name": w.name,
        "reason": w.reason,
        "priority": w.priority,
        "source": w.source,
    }


def watchlist_item(w: Watchlist, alerts_24h: int = 0) -> dict[str, Any]:
    now = utcnow()
    return {
        "id": w.id,
        "entity_type": w.entity_type,
        "plate_norm": w.plate_norm,
        "plate_display": format_plate(w.plate_norm) if w.plate_norm else None,
        "name": w.name,
        "reason": w.reason,
        "priority": w.priority,
        "source": w.source,
        "notes": w.notes,
        "photo_url": media_url(w.photo_path),
        "added_by": w.added_by,
        "added_by_username": lookups.username_sync(w.added_by),
        "is_active": w.is_active,
        "expires_at": iso_z(w.expires_at),
        "is_effective": bool(w.is_active and (w.expires_at is None or w.expires_at > now)),
        "hit_count": w.hit_count,
        "last_hit_at": iso_z(w.last_hit_at),
        "alerts_24h": alerts_24h,
        "created_at": iso_z(w.created_at),
        "updated_at": iso_z(w.updated_at),
    }


def alert_escalated(a: Alert) -> bool:
    if a.status != "new":
        return False
    return (utcnow() - a.created_at) > timedelta(minutes=cfg.get_int("alerts.escalate_minutes"))


def alert_item(a: Alert, cam: Camera, wl: Watchlist | None = None, read: PlateRead | None = None) -> dict[str, Any]:
    return {
        "id": a.id,
        "type": a.type,
        "status": a.status,
        "priority": a.priority,
        "confidence_level": a.confidence_level,
        "escalated": alert_escalated(a),
        "created_at": iso_z(a.created_at),
        "updated_at": iso_z(a.updated_at),
        "latency_ms": a.latency_ms,
        "camera": camera_summary(cam),
        "watchlist": watchlist_brief(wl),
        "read": read_brief(read) if read else None,
        "sighting_id": a.sighting_id,
        "zone_id": a.zone_id,
        "plate_norm": a.plate_norm,
        "plate_display": format_plate(a.plate_norm) if a.plate_norm else None,
        "snapshot_url": media_url(a.snapshot_path),
        "snapshot_sha256": a.snapshot_sha256,
        "read_count": a.read_count,
        "last_read_at": iso_z(a.last_read_at),
        "acknowledged_by": a.acknowledged_by,
        "acknowledged_by_username": lookups.username_sync(a.acknowledged_by),
        "acknowledged_at": iso_z(a.acknowledged_at),
        "closed_by": a.closed_by,
        "closed_by_username": lookups.username_sync(a.closed_by),
        "closed_at": iso_z(a.closed_at),
        "outcome": a.outcome,
        "note": a.note,
        "recording_available": recording_available(cam, read.captured_at if read else a.created_at),
    }


def alert_update_payload(a: Alert) -> dict[str, Any]:
    return {
        "id": a.id,
        "status": a.status,
        "priority": a.priority,
        "confidence_level": a.confidence_level,
        "read_count": a.read_count,
        "last_read_at": iso_z(a.last_read_at),
        "acknowledged_by_username": lookups.username_sync(a.acknowledged_by),
        "acknowledged_at": iso_z(a.acknowledged_at),
        "closed_by_username": lookups.username_sync(a.closed_by),
        "closed_at": iso_z(a.closed_at),
        "outcome": a.outcome,
        "note": a.note,
        "escalated": alert_escalated(a),
        "updated_at": iso_z(a.updated_at),
    }


def notify_text(a: Alert, cam: Camera, wl: Watchlist | None, read: PlateRead | None) -> tuple[str, str]:
    """Server-rendered notification title/body (CONTRACT §9 `alert` envelope)."""
    prio = a.priority.upper()
    when = fmt_ist_time(read.captured_at if read else a.created_at)
    where = " · ".join(x for x in (cam.name, cam.district) if x)
    if a.type == "watchlist_hit":
        reason = REASON_LABELS.get(wl.reason if wl else "other", "Watchlist")
        plate = format_plate(a.plate_norm) if a.plate_norm else "?"
        suffix = " (possible match)" if a.confidence_level == "possible" else ""
        return f"{prio} · {reason} {plate}{suffix}", f"{where} · {when}"
    if a.type == "camera_offline":
        return f"{prio} · Camera offline: {cam.name}", f"{where} · {when}"
    if a.type == "intrusion":
        return f"{prio} · Intrusion at {cam.name}", f"{where} · {when}"
    return f"{prio} · {a.type} at {cam.name}", f"{where} · {when}"


def event_item(e: Event, cam: Camera) -> dict[str, Any]:
    return {
        "id": e.id,
        "camera_id": e.camera_id,
        "camera": camera_summary(cam),
        "occurred_at": iso_z(e.occurred_at),
        "type": e.type,
        "type_label": EVENT_LABELS.get(e.type, e.type),
        "note": e.note,
        "sighting_id": e.sighting_id,
        "read_id": e.read_id,
        "alert_id": e.alert_id,
        "frame_url": media_url(e.frame_path),
        "frame_sha256": e.frame_sha256,
        "is_auto": e.is_auto,
        "created_by": e.created_by,
        "created_by_username": lookups.username_sync(e.created_by),
        "created_at": iso_z(e.created_at),
    }


def clip_item(c: Clip, cam: Camera) -> dict[str, Any]:
    return {
        "id": c.id,
        "camera_id": c.camera_id,
        "camera": camera_summary(cam),
        "alert_id": c.alert_id,
        "sighting_id": c.sighting_id,
        "start_at": iso_z(c.start_at),
        "duration_s": c.duration_s,
        "path": c.path,
        "url": media_url(c.path),
        "sha256": c.sha256,
        "size_bytes": c.size_bytes,
        "created_by": c.created_by,
        "created_by_username": lookups.username_sync(c.created_by),
        "created_at": iso_z(c.created_at),
    }


def zone_item(z: Zone) -> dict[str, Any]:
    return {
        "id": z.id,
        "camera_id": z.camera_id,
        "name": z.name,
        "polygon": z.polygon_json,
        "active_from": z.active_from.strftime("%H:%M") if z.active_from else None,
        "active_to": z.active_to.strftime("%H:%M") if z.active_to else None,
        "classes": list(z.classes or []),
        "dwell_s": z.dwell_s,
        "priority": z.priority,
        "is_active": z.is_active,
        "created_by": z.created_by,
        "created_at": iso_z(z.created_at),
        "updated_at": iso_z(z.updated_at),
    }


def user_item(u: User) -> dict[str, Any]:
    d = lookups.dept_sync(u.department_id)
    return {
        "id": u.id,
        "username": u.username,
        "full_name": u.full_name,
        "role": u.role,
        "department_id": u.department_id,
        "department_name": d.name if d else None,
        "department_code": d.code if d else None,
        "district": u.district,
        "is_active": u.is_active,
        "last_login_at": iso_z(u.last_login_at),
        "created_at": iso_z(u.created_at),
        "updated_at": iso_z(u.updated_at),
    }


def user_brief(u: User) -> dict[str, Any]:
    d = lookups.dept_sync(u.department_id)
    return {
        "id": u.id,
        "username": u.username,
        "full_name": u.full_name,
        "role": u.role,
        "department_id": u.department_id,
        "department_name": d.name if d else None,
        "district": u.district,
    }


def api_key_item(k: ApiKey) -> dict[str, Any]:
    return {
        "id": k.id,
        "name": k.name,
        "key_prefix": k.key_prefix,
        "scope": k.scope,
        "is_active": k.is_active,
        "created_by_username": lookups.username_sync(k.created_by),
        "last_used_at": iso_z(k.last_used_at),
        "created_at": iso_z(k.created_at),
    }


def webhook_item(w: Webhook) -> dict[str, Any]:
    return {
        "id": w.id,
        "name": w.name,
        "url": w.url,
        "secret": "********" if w.secret else None,
        "event_types": list(w.event_types or []),
        "is_active": w.is_active,
        "last_status": w.last_status,
        "last_delivered_at": iso_z(w.last_delivered_at),
        "last_error": w.last_error,
        "created_by_username": lookups.username_sync(w.created_by),
        "created_at": iso_z(w.created_at),
    }


def report_file_item(r: ReportFile) -> dict[str, Any]:
    return {
        "id": r.id,
        "type": r.type,
        "path": r.path,
        "url": media_url(r.path),
        "sha256": r.sha256,
        "size_bytes": r.size_bytes,
        "params": r.params,
        "row_count": r.row_count,
        "created_by": r.created_by,
        "created_by_username": lookups.username_sync(r.created_by),
        "created_at": iso_z(r.created_at),
    }


def audit_item(a: AuditLog) -> dict[str, Any]:
    return {
        "id": a.id,
        "ts": iso_z(a.ts),
        "user_id": a.user_id,
        "username": lookups.username_sync(a.user_id) or (a.actor if a.role not in ("apikey", "system") else None),
        "actor": a.actor,
        "role": a.role,
        "action": a.action,
        "entity": a.entity,
        "entity_id": a.entity_id,
        "before": a.before,
        "after": a.after,
        "ip": str(a.ip) if a.ip else None,
        "user_agent": a.user_agent,
        "request_id": str(a.request_id) if a.request_id else None,
    }


def relay_path_for(c: Camera) -> str:
    return relay_path(c.id)
