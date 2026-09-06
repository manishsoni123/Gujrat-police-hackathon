"""SQLAlchemy 2 models – every table of CONTRACT §4.2 (PostgreSQL 16 + PostGIS)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from geoalchemy2 import Geography, Geometry
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.tz import utcnow

# ---- enumerations (VARCHAR + CHECK, §4.1) ---------------------------------------------------

ROLES = ("admin", "dept_admin", "operator", "viewer")
API_KEY_SCOPES = ("bulk", "internal")
CAMERA_SOURCES = ("sandbox", "csv", "api", "manual", "own")
CAMERA_TYPES = ("analog", "ip", "ptz", "dome", "bullet", "anpr", "other")
OWNERSHIPS = ("govt_dept", "private", "public_facing")
CONNECTIVITY = ("lan", "fibre", "4g", "5g", "leased_line", "wifi", "other")
CODECS = ("H264", "H265", "MJPEG", "UNKNOWN")
# : the camera has never delivered a stream (catalogue live=false or a source that never came up);
# it is not an outage, so no camera_offline alert/event is raised (CONTRACT Amendments 2026-09-05, §5.6).
CAMERA_STATUSES = ("unknown", "online", "degraded", "offline", "not_streaming", "retired")
MAINTENANCE = ("ok", "under_maintenance", "faulty", "decommissioned")
# How sure we are about a camera's coordinates (organiser sandbox rows are team-inferred from the name alone):
# exact = landmark found (~100 m), approx = area/junction (~1 km), guess = district HQ fallback or best candidate.
LOCATION_CONFIDENCE = ("exact", "approx", "guess")
HEALTH_SOURCES = ("mediamtx", "probe", "catalogue", "manual")
ENTITY_TYPES = ("vehicle", "person")
REASONS = ("stolen", "wanted", "blacklisted", "missing", "suspect", "arrested", "unidentified_body", "other")
PRIORITIES = ("critical", "high", "medium", "low")
WATCHLIST_SOURCES = ("own", "egujcop", "vahan", "manual", "import")
ALERT_TYPES = ("watchlist_hit", "camera_offline", "intrusion", "frs_hit")
ALERT_STATUSES = ("new", "acknowledged", "closed")
CONFIDENCE_LEVELS = ("exact", "possible")
OUTCOMES = ("resolved", "false_positive", "duplicate", "other")
MANUAL_EVENT_TYPES = ("accident", "suspicious", "checkpoint", "other")
AUTO_EVENT_TYPES = ("watchlist_hit", "loop_reset", "intrusion", "camera_offline", "camera_online")
EVENT_TYPES = MANUAL_EVENT_TYPES + AUTO_EVENT_TYPES
DECISIONS = ("confirmed", "rejected")
POI_TYPES = (
    "checkpost", "bus_stand", "hospital", "school", "highway", "railway_station",
    "temple", "market", "govt_office", "border", "other",
)
OBJECT_CLASSES = ("person", "bicycle", "car", "motorcycle", "bus", "truck")
WEBHOOK_EVENTS = ("alert.created", "alert.updated", "camera.offline", "camera.online", "event.created")
REPORT_TYPES = (
    "detections_csv", "detections_pdf", "route_pdf", "gap_csv", "gap_pdf",
    "quality_pdf", "cameras_csv", "import_errors_csv", "audit_csv",
)
MODES = ("live", "preindex")


def _check(col: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{col} IN ({quoted})", name=name)


class Base(DeclarativeBase):
    def as_dict(self) -> dict[str, Any]:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


# ---- tables ---------------------------------------------------------------------------------


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (_check("role", ROLES, "ck_users_role"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="viewer")
    department_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("departments.id", ondelete="SET NULL"))
    district: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    wall_layout: Mapped[dict | None] = mapped_column(JSONB)
    # Tokens issued (iat) before this instant are rejected: set on password reset/change and deactivation.
    token_not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (_check("scope", API_KEY_SCOPES, "ck_api_keys_scope"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Camera(Base):
    __tablename__ = "cameras"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_cameras_source_external"),
        CheckConstraint("lat IS NULL OR (lat BETWEEN -90 AND 90)", name="ck_cameras_lat"),
        CheckConstraint("lon IS NULL OR (lon BETWEEN -180 AND 180)", name="ck_cameras_lon"),
        _check("source", CAMERA_SOURCES, "ck_cameras_source"),
        _check("type", CAMERA_TYPES, "ck_cameras_type"),
        _check("ownership", OWNERSHIPS, "ck_cameras_ownership"),
        _check("codec", CODECS, "ck_cameras_codec"),
        _check("status", CAMERA_STATUSES, "ck_cameras_status"),
        _check("maintenance_status", MAINTENANCE, "ck_cameras_maint"),
        CheckConstraint(
            "connectivity_type IS NULL OR connectivity_type IN ('lan','fibre','4g','5g','leased_line','wifi','other')",
            name="ck_cameras_conn",
        ),
        CheckConstraint("location_confidence IS NULL OR location_confidence IN ('exact','approx','guess')", name="ck_cameras_loc_conf"),
        Index("ix_cameras_department", "department_id"),
        Index("ix_cameras_district", "district"),
        Index("ix_cameras_status", "status"),
        Index("ix_cameras_anpr", "anpr_enabled"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    department_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="ip")
    ownership: Mapped[str] = mapped_column(String(16), nullable=False, default="govt_dept")
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    geog = mapped_column(Geography(geometry_type="POINT", srid=4326, spatial_index=True), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255))
    district: Mapped[str | None] = mapped_column(String(64))
    police_station: Mapped[str | None] = mapped_column(String(120))
    ward: Mapped[str | None] = mapped_column(String(64))
    rtsp_url: Mapped[str | None] = mapped_column(String(512))
    whep_url: Mapped[str | None] = mapped_column(String(512))
    hls_url: Mapped[str | None] = mapped_column(String(512))
    relay_path: Mapped[str | None] = mapped_column(String(64))
    codec: Mapped[str] = mapped_column(String(8), nullable=False, default="UNKNOWN")
    resolution: Mapped[str | None] = mapped_column(String(16))
    fps: Mapped[int | None] = mapped_column(SmallInteger)
    live: Mapped[bool | None] = mapped_column(Boolean)
    storage_location: Mapped[str | None] = mapped_column(String(120))
    retention_days: Mapped[int | None] = mapped_column(SmallInteger)
    install_date: Mapped[date | None] = mapped_column(Date)
    vendor: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(80))
    heading_deg: Mapped[int | None] = mapped_column(SmallInteger)
    fov_deg: Mapped[int | None] = mapped_column(SmallInteger)
    connectivity_type: Mapped[str | None] = mapped_column(String(16))
    bandwidth_kbps: Mapped[int | None] = mapped_column(Integer)
    vms_platform: Mapped[str | None] = mapped_column(String(80))
    nvr_id: Mapped[str | None] = mapped_column(String(64))
    onvif_host: Mapped[str | None] = mapped_column(String(120))
    anpr_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    record_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    health_fail_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    maintenance_status: Mapped[str] = mapped_column(String(24), nullable=False, default="ok")
    last_maintenance_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    maintenance_note: Mapped[str | None] = mapped_column(String(255))
    amc_vendor: Mapped[str | None] = mapped_column(String(120))
    amc_expiry: Mapped[date | None] = mapped_column(Date)
    # Organiser-sandbox enrichment (CONTRACT Amendments 2026-09-05): confidence of the inferred coordinates and a
    # JSONB bag (`metadata` column; attribute `meta` because SQLAlchemy reserves `metadata`) with the enrichment
    # source/notes, the last import-time probe and the catalogue mode. Null for CSV/API/manual cameras.
    location_confidence: Mapped[str | None] = mapped_column(String(8))
    meta: Mapped[dict | None] = mapped_column("metadata", JSONB)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    created_via: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class CameraHealthLog(Base):
    __tablename__ = "camera_health_log"
    __table_args__ = (
        Index("ix_health_camera_checked", "camera_id", text("checked_at DESC")),
        _check("source_flag", HEALTH_SOURCES, "ck_health_source"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_ready: Mapped[bool] = mapped_column(Boolean, nullable=False)
    has_video: Mapped[bool] = mapped_column(Boolean, nullable=False)
    bytes_delta: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    readers: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    source_flag: Mapped[str] = mapped_column(String(16), nullable=False)
    status_after: Mapped[str] = mapped_column(String(16), nullable=False)


class Poi(Base):
    __tablename__ = "pois"
    __table_args__ = (_check("type", POI_TYPES, "ck_pois_type"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    district: Mapped[str] = mapped_column(String(64), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    geog = mapped_column(Geography(geometry_type="POINT", srid=4326, spatial_index=True), nullable=False)


class District(Base):
    __tablename__ = "districts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    code: Mapped[str | None] = mapped_column(String(8))
    geom = mapped_column(Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=True), nullable=False)


class PlateRead(Base):
    __tablename__ = "plate_reads"
    __table_args__ = (
        Index("ix_reads_plate_captured", "plate_norm", text("captured_at DESC")),
        Index("ix_reads_camera_captured", "camera_id", text("captured_at DESC")),
        Index("ix_reads_captured", "captured_at"),
        Index("uq_reads_camera_captured_plate", "camera_id", "captured_at", "plate_norm", unique=True),
        _check("mode", MODES, "ck_reads_mode"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    sighting_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sightings.id", ondelete="SET NULL", use_alter=True, name="fk_reads_sighting"))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stream_pts: Mapped[float | None] = mapped_column(Float)
    frame_index: Mapped[int | None] = mapped_column(BigInteger)
    plate_raw: Mapped[str] = mapped_column(String(32), nullable=False)
    plate_norm: Mapped[str] = mapped_column(String(16), nullable=False)
    is_valid_format: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    bbox: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    crop_path: Mapped[str | None] = mapped_column(String(255))
    crop_sha256: Mapped[str | None] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(8), nullable=False, default="live")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Sighting(Base):
    __tablename__ = "sightings"
    __table_args__ = (
        Index("ix_sightings_plate_first", "plate_norm", text("first_seen DESC")),
        Index("ix_sightings_camera_first", "camera_id", text("first_seen DESC")),
        Index("ix_sightings_closed", "closed"),
        _check("mode", MODES, "ck_sightings_mode"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    worker_key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    camera_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    plate_norm: Mapped[str] = mapped_column(String(16), nullable=False)
    is_valid_format: Mapped[bool] = mapped_column(Boolean, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    read_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    best_conf: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    best_read_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("plate_reads.id", ondelete="SET NULL", use_alter=True, name="fk_sightings_best_read"))
    best_crop_path: Mapped[str | None] = mapped_column(String(255))
    frame_path: Mapped[str | None] = mapped_column(String(255))
    frame_sha256: Mapped[str | None] = mapped_column(String(64))
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False, default="live")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Watchlist(Base):
    __tablename__ = "watchlist"
    __table_args__ = (
        Index("ix_watchlist_active_expiry", "is_active", "expires_at"),
        Index(
            "uq_watchlist_active_vehicle_plate",
            "plate_norm",
            unique=True,
            postgresql_where=text("entity_type = 'vehicle' AND is_active"),
        ),
        _check("entity_type", ENTITY_TYPES, "ck_watchlist_entity"),
        _check("reason", REASONS, "ck_watchlist_reason"),
        _check("priority", PRIORITIES, "ck_watchlist_priority"),
        _check("source", WATCHLIST_SOURCES, "ck_watchlist_source"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(8), nullable=False)
    plate_norm: Mapped[str | None] = mapped_column(String(16))
    name: Mapped[str | None] = mapped_column(String(160))
    reason: Mapped[str] = mapped_column(String(24), nullable=False)
    priority: Mapped[str] = mapped_column(String(8), nullable=False, default="medium")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    notes: Mapped[str | None] = mapped_column(Text)
    photo_path: Mapped[str | None] = mapped_column(String(255))
    added_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_status_created", "status", text("created_at DESC")),
        Index("ix_alerts_camera_created", "camera_id", text("created_at DESC")),
        Index("ix_alerts_wl_cam_created", "watchlist_id", "camera_id", text("created_at DESC")),
        Index("ix_alerts_type_status", "type", "status"),
        _check("type", ALERT_TYPES, "ck_alerts_type"),
        _check("status", ALERT_STATUSES, "ck_alerts_status"),
        _check("priority", PRIORITIES, "ck_alerts_priority"),
        CheckConstraint("confidence_level IS NULL OR confidence_level IN ('exact','possible')", name="ck_alerts_conf"),
        CheckConstraint("outcome IS NULL OR outcome IN ('resolved','false_positive','duplicate','other')", name="ck_alerts_outcome"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    priority: Mapped[str] = mapped_column(String(8), nullable=False)
    confidence_level: Mapped[str | None] = mapped_column(String(8))
    camera_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    watchlist_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("watchlist.id", ondelete="SET NULL"))
    sighting_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sightings.id", ondelete="SET NULL"))
    read_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("plate_reads.id", ondelete="SET NULL"))
    zone_id: Mapped[int | None] = mapped_column(BigInteger)
    plate_norm: Mapped[str | None] = mapped_column(String(16))
    snapshot_path: Mapped[str | None] = mapped_column(String(255))
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64))
    read_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    acknowledged_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_camera_occurred", "camera_id", text("occurred_at DESC")),
        Index("ix_events_type_occurred", "type", text("occurred_at DESC")),
        # Worker events are replay-safe (CONTRACT §7.2/§7.6): one loop_reset/intrusion per camera/instant/note.
        Index(
            "uq_events_worker_dedup", "camera_id", "type", "occurred_at", text("coalesce(note, '')"), unique=True,
            postgresql_where=text("is_auto AND type IN ('loop_reset', 'intrusion')"),
        ),
        _check("type", EVENT_TYPES, "ck_events_type"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    sighting_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sightings.id", ondelete="SET NULL"))
    read_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("plate_reads.id", ondelete="SET NULL"))
    alert_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("alerts.id", ondelete="SET NULL"))
    frame_path: Mapped[str | None] = mapped_column(String(255))
    frame_sha256: Mapped[str | None] = mapped_column(String(64))
    is_auto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class RouteConfirmation(Base):
    __tablename__ = "route_confirmations"
    __table_args__ = (
        UniqueConstraint("query_plate", "sighting_id", name="uq_route_conf"),
        _check("decision", DECISIONS, "ck_route_decision"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    query_plate: Mapped[str] = mapped_column(String(16), nullable=False)
    sighting_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sightings.id", ondelete="CASCADE"), nullable=False)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ObjectCount(Base):
    __tablename__ = "object_counts"
    __table_args__ = (
        UniqueConstraint("camera_id", "minute", "class", name="uq_object_counts"),
        Index("ix_object_counts_minute", "minute"),
        _check("class", OBJECT_CLASSES, "ck_object_class"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    class_: Mapped[str] = mapped_column("class", String(16), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)


class Zone(Base):
    __tablename__ = "zones"
    __table_args__ = (_check("priority", PRIORITIES, "ck_zones_priority"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    polygon_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    active_from: Mapped[time | None] = mapped_column(Time)
    active_to: Mapped[time | None] = mapped_column(Time)
    classes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    dwell_s: Mapped[float] = mapped_column(Float, nullable=False, default=2.0)
    priority: Mapped[str] = mapped_column(String(8), nullable=False, default="medium")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Clip(Base):
    __tablename__ = "clips"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    alert_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("alerts.id", ondelete="SET NULL"))
    sighting_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sightings.id", ondelete="SET NULL"))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_s: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class QaLabel(Base):
    __tablename__ = "qa_labels"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    read_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("plate_reads.id", ondelete="CASCADE"), unique=True, nullable=False)
    true_plate: Mapped[str] = mapped_column(String(16), nullable=False)
    is_match: Mapped[bool] = mapped_column(Boolean, nullable=False)
    char_errors: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    labelled_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Webhook(Base):
    __tablename__ = "webhooks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    secret: Mapped[str | None] = mapped_column(String(128))
    event_types: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_status: Mapped[int | None] = mapped_column(SmallInteger)
    last_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ReportFile(Base):
    __tablename__ = "report_files"
    __table_args__ = (_check("type", REPORT_TYPES, "ck_report_type"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    row_count: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AnprWorker(Base):
    __tablename__ = "anpr_workers"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    gpu: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cameras: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    detector: Mapped[str | None] = mapped_column(String(16))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Free-form heartbeat block (CONTRACT §7.5 ): detector requested/active/degraded/providers/weights, object weights.
    extra: Mapped[dict | None] = mapped_column(JSONB)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_ts", text("ts DESC")),
        Index("ix_audit_user_ts", "user_id", text("ts DESC")),
        Index("ix_audit_action_ts", "action", text("ts DESC")),
        Index("ix_audit_entity", "entity", "entity_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    entity: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(255))
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


# Raw SQL executed after create_all: trigram indexes, the audit trigger (§4 general rules).
POST_CREATE_SQL = [
    "CREATE INDEX IF NOT EXISTS ix_cameras_name_trgm ON cameras USING gin (name gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_reads_plate_raw_trgm ON plate_reads USING gin (plate_raw gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_reads_plate_norm_trgm ON plate_reads USING gin (plate_norm gin_trgm_ops)",
    # Idempotent ingestion (CONTRACT §7.2 worker retries): one accepted read per camera/instant/plate.
    # Existing duplicates from earlier replays are collapsed (lowest id kept) before the index is built.
    """
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'uq_reads_camera_captured_plate') THEN
        DELETE FROM plate_reads a USING plate_reads b
          WHERE a.id > b.id AND a.camera_id = b.camera_id AND a.captured_at = b.captured_at AND a.plate_norm = b.plate_norm;
        CREATE UNIQUE INDEX uq_reads_camera_captured_plate ON plate_reads (camera_id, captured_at, plate_norm);
      END IF;
    END $$
    """,
    # Worker-event idempotency (see uq_events_worker_dedup): collapse duplicates left by earlier replays, then index.
    """
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'uq_events_worker_dedup') THEN
        DELETE FROM events a USING events b
          WHERE a.id > b.id AND a.camera_id = b.camera_id AND a.type = b.type AND a.occurred_at = b.occurred_at
            AND coalesce(a.note, '') = coalesce(b.note, '') AND a.is_auto AND b.is_auto AND a.type IN ('loop_reset', 'intrusion');
        CREATE UNIQUE INDEX uq_events_worker_dedup ON events (camera_id, type, occurred_at, coalesce(note, ''))
          WHERE is_auto AND type IN ('loop_reset', 'intrusion');
      END IF;
    END $$
    """,
    # Additive columns for databases created before these fields existed (create_all never alters tables).
    "ALTER TABLE anpr_workers ADD COLUMN IF NOT EXISTS extra JSONB",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_not_before TIMESTAMPTZ",
    # Organiser-sandbox enrichment columns (CONTRACT Amendments 2026-09-05, real sandbox adapter).
    "ALTER TABLE cameras ADD COLUMN IF NOT EXISTS location_confidence VARCHAR(8)",
    "ALTER TABLE cameras ADD COLUMN IF NOT EXISTS metadata JSONB",
    """
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_cameras_loc_conf') THEN
        ALTER TABLE cameras ADD CONSTRAINT ck_cameras_loc_conf CHECK (location_confidence IS NULL OR location_confidence IN ('exact','approx','guess'));
      END IF;
    END $$
    """,
    # cameras.status gains 'not_streaming' (never-online cameras, CONTRACT Amendments 2026-09-05 §5.6). Existing
    # offline-but-never-seen rows are migrated once and their auto-raised camera_offline alerts closed
    # (outcome 'other', note 'catalogue reports not live'); the count is written to the audit log.
    "ALTER TABLE cameras DROP CONSTRAINT IF EXISTS ck_cameras_status",
    "ALTER TABLE cameras ADD CONSTRAINT ck_cameras_status CHECK (status IN ('unknown','online','degraded','offline','not_streaming','retired'))",
    """
    DO $$
    DECLARE n integer;
    BEGIN
      UPDATE alerts a SET status = 'closed', outcome = 'other', note = 'catalogue reports not live',
        acknowledged_at = coalesce(a.acknowledged_at, now()), closed_at = now(), closed_by = NULL, updated_at = now()
        FROM cameras c
        WHERE a.camera_id = c.id AND a.type = 'camera_offline' AND a.status IN ('new', 'acknowledged')
          AND c.status = 'offline' AND c.last_seen_at IS NULL;
      GET DIAGNOSTICS n = ROW_COUNT;
      UPDATE cameras SET status = 'not_streaming' WHERE status = 'offline' AND last_seen_at IS NULL;
      IF n > 0 THEN
        INSERT INTO audit_log (ts, actor, role, action, entity, after)
          VALUES (now(), 'system', 'system', 'alert.auto_close', 'alert', jsonb_build_object('closed', n, 'note', 'catalogue reports not live'));
      END IF;
    END $$
    """,
    """
    CREATE OR REPLACE FUNCTION audit_log_immutable_fn() RETURNS trigger AS $$
    BEGIN
      RAISE EXCEPTION 'audit_log is append-only';
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS audit_log_immutable ON audit_log",
    """
    CREATE TRIGGER audit_log_immutable BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_immutable_fn()
    """,
]
