"""Camera registry (CONTRACT §5.2): CRUD, maintenance, health history, CSV export, template."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.core.config import settings
from app.core.errors import conflict, forbidden, not_found, validation_error
from app.core.hashing import abs_path, media_url
from app.core.rbac import can_change_department
from app.core.tz import fmt_ist, ist_stamp_short, iso_z, parse_iso, utcnow
from app.db.models import Alert, Camera, CameraHealthLog, Department, PlateRead, Sighting, Zone
from app.schemas.cameras import CameraCreate, CameraUpdate, MaintenanceUpdate
from app.schemas.common import PageParams, csv_list
from app.services import lookups, serializers
from app.services.audit import set_audit
from app.services.camera_importer import ImportOutcome, ensure_relay_paths, upsert_cameras, validate_row
from app.services.csv_importer import CAMERA_TEMPLATE_HEADER, camera_template_csv
from app.services.mediamtx_client import client as mtx
from app.services.mediamtx_client import relay_path
from app.services.report_builder import build_csv, rows_hash, store_report
from app.services.scope import camera_conditions, scoped_camera

router = APIRouter(tags=["cameras"])

SORTS: dict[str, Any] = {
    "name": Camera.name, "external_id": Camera.external_id, "status": Camera.status, "district": Camera.district,
    "department_name": Department.name, "last_seen_at": Camera.last_seen_at, "updated_at": Camera.updated_at,
    "created_at": Camera.created_at, "install_date": Camera.install_date, "amc_expiry": Camera.amc_expiry,
}


class CameraFilters:
    def __init__(
        self,
        q: str | None = None,
        department_id: int | None = None,
        district: str | None = None,
        police_station: str | None = None,
        type: str | None = None,
        status: str | None = None,
        source: str | None = None,
        ownership: str | None = None,
        maintenance_status: str | None = None,
        anpr_enabled: bool | None = None,
        include_retired: bool = False,
    ) -> None:
        self.q, self.department_id, self.district, self.police_station = q, department_id, district, police_station
        self.type, self.status, self.source, self.ownership = type, status, source, ownership
        self.maintenance_status, self.anpr_enabled, self.include_retired = maintenance_status, anpr_enabled, include_retired

    def apply(self, stmt, scope):
        stmt = stmt.where(*camera_conditions(scope))
        if not self.include_retired:
            stmt = stmt.where(Camera.status != "retired")
        if self.q:
            like = f"%{self.q}%"
            stmt = stmt.where(or_(Camera.name.ilike(like), Camera.external_id.ilike(like), Camera.address.ilike(like), Camera.police_station.ilike(like)))
        if self.department_id is not None:
            stmt = stmt.where(Camera.department_id == self.department_id)
        if self.district:
            stmt = stmt.where(Camera.district == self.district)
        if self.police_station:
            stmt = stmt.where(Camera.police_station == self.police_station)
        if self.type:
            stmt = stmt.where(Camera.type.in_(csv_list(self.type) or []))
        if self.status:
            stmt = stmt.where(Camera.status.in_(csv_list(self.status) or []))
        if self.source:
            stmt = stmt.where(Camera.source == self.source)
        if self.ownership:
            stmt = stmt.where(Camera.ownership == self.ownership)
        if self.maintenance_status:
            stmt = stmt.where(Camera.maintenance_status == self.maintenance_status)
        if self.anpr_enabled is not None:
            stmt = stmt.where(Camera.anpr_enabled.is_(self.anpr_enabled))
        return stmt

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, False, "")}


async def uptime_map(db: AsyncSession, camera_ids: list[int]) -> dict[int, float]:
    if not camera_ids:
        return {}
    from sqlalchemy import Integer, cast

    since = utcnow() - timedelta(hours=24)
    rows = (
        await db.execute(
            select(CameraHealthLog.camera_id, func.count(), func.sum(cast(CameraHealthLog.is_ready, Integer)))
            .where(CameraHealthLog.checked_at >= since, CameraHealthLog.camera_id.in_(camera_ids))
            .group_by(CameraHealthLog.camera_id)
        )
    ).all()
    return {r[0]: round(100.0 * float(r[2] or 0) / float(r[1]), 1) for r in rows if r[1]}


async def _get_scoped(db: AsyncSession, user, camera_id: int) -> Camera:
    cam = await scoped_camera(db, user_scope(user), camera_id)
    if cam is None:
        raise not_found("Camera not found")
    return cam


@router.get("/cameras", dependencies=[Depends(require_permission("cameras.read"))])
async def list_cameras(user: CurrentUser, db: DbDep, page: PageParams = Depends(), f: CameraFilters = Depends()):
    sort, order = page.resolve(SORTS, "name", "asc")
    await serializers.warm(db)
    stmt = f.apply(select(Camera).join(Department, Department.id == Camera.department_id), user_scope(user))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    col = SORTS[sort]
    stmt = stmt.order_by(col.desc().nulls_last() if order == "desc" else col.asc().nulls_last(), Camera.id.asc()).offset(page.offset).limit(page.page_size)
    cams = (await db.execute(stmt)).scalars().all()
    up = await uptime_map(db, [c.id for c in cams])
    is_admin = user.role == "admin"
    return {"items": [serializers.camera_full(c, is_admin, up.get(c.id)) for c in cams], "total": int(total), "page": page.page, "page_size": page.page_size}


@router.get("/departments", dependencies=[Depends(require_permission("cameras.read"))])
async def list_departments(db: DbDep) -> dict[str, Any]:
    """All departments (code order) so filter dropdowns and forms can list departments without cameras."""
    rows = (await db.execute(select(Department).order_by(Department.code.asc()))).scalars().all()
    return {"items": [{"id": d.id, "code": d.code, "name": d.name} for d in rows]}


@router.get("/cameras/export", dependencies=[Depends(require_permission("cameras.export"))])
async def export_cameras(user: CurrentUser, db: DbDep, request: Request, f: CameraFilters = Depends(), format: str = Query("csv")):
    if format != "csv":
        raise validation_error("Unsupported format", [{"field": "format", "message": "only csv is supported"}])
    await serializers.warm(db)
    stmt = f.apply(select(Camera).join(Department, Department.id == Camera.department_id), user_scope(user)).order_by(Camera.name.asc())
    cams = (await db.execute(stmt)).scalars().all()
    header = CAMERA_TEMPLATE_HEADER + ["id", "source", "status", "last_seen_at_ist", "relay_path", "created_at_ist"]
    rows: list[list[Any]] = []
    is_admin = user.role == "admin"
    for c in cams:
        d = lookups.dept_sync(c.department_id)
        row = [
            c.external_id, c.name, d.code if d else "UNASSIGNED", c.type, c.ownership, c.lat, c.lon, c.address, c.district, c.police_station, c.ward,
            serializers.mask_rtsp(c.rtsp_url, is_admin), c.codec, c.resolution, c.fps, c.storage_location, c.retention_days,
            c.install_date.isoformat() if c.install_date else None, c.vendor, c.model, c.heading_deg, c.fov_deg, c.connectivity_type,
            c.bandwidth_kbps, c.vms_platform, c.nvr_id, c.maintenance_status, c.amc_vendor, c.amc_expiry.isoformat() if c.amc_expiry else None,
            str(c.anpr_enabled).lower(), str(c.record_enabled).lower(),
            c.id, c.source, c.status, fmt_ist(c.last_seen_at), c.relay_path, fmt_ist(c.created_at),
        ]
        rows.append(row)
    h = rows_hash(rows)
    trailer = f"# Exported by {user.username} at {fmt_ist(utcnow())} from {settings.PRODUCT_NAME} {settings.APP_VERSION}; sha256 of rows above: {h}"
    data = build_csv(header, rows, trailer)
    stamp = ist_stamp_short()
    rf = await store_report(db, "cameras_csv", data, "csv", user.username, user.id, {"filters": f.as_dict()}, len(rows), subdir="exports", basename=f"cameras_export_{ist_stamp_short()}IST_{user.username}.csv")
    set_audit(request, action="camera.export", entity="report_file", entity_id=rf.id, after={"rows": len(rows), "sha256": rf.sha256, "path": rf.path, "filters": f.as_dict()})
    return Response(
        content=data, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="cameras_export_{stamp}IST.csv"', "X-Sentinel-Sha256": rf.sha256, "X-Sentinel-Report-Id": str(rf.id)},
    )


@router.get("/cameras/import/template", dependencies=[Depends(require_permission("cameras.write"))])
async def import_template():
    return Response(content=camera_template_csv(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="cameras_template.csv"'})


@router.post("/cameras", status_code=201, dependencies=[Depends(require_permission("cameras.write"))])
async def create_camera(body: CameraCreate, user: CurrentUser, db: DbDep, request: Request):
    scope = user_scope(user)
    source = body.source or "manual"
    raw = body.model_dump(exclude_unset=True, exclude={"source"})
    raw = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in raw.items()}
    existing = (await db.execute(select(Camera).where(Camera.source == source, Camera.external_id == body.external_id))).scalar_one_or_none()
    if existing is not None:
        raise conflict(f"A {source} camera with external_id '{body.external_id}' already exists", existing_id=existing.id)
    outcome = await upsert_cameras(db, [(None, 0, raw)], source=source, created_by=user.id, created_via="manual", scope=scope, allow_department_change=can_change_department(user.role))
    if outcome.errors:
        raise validation_error("Invalid camera", [e.as_dict(bulk=True) for e in outcome.errors])
    cam = outcome.touched[0]
    await db.refresh(cam)
    await serializers.warm(db)
    set_audit(request, entity="camera", entity_id=cam.id, after={**serializers.camera_diff(cam), "relay_paths_created": outcome.relay_paths_created, "warnings": [w.as_dict(True) for w in outcome.warnings]})
    return serializers.camera_full(cam, user.role == "admin")


@router.get("/cameras/{camera_id}", dependencies=[Depends(require_permission("cameras.read"))])
async def get_camera(camera_id: int, user: CurrentUser, db: DbDep):
    cam = await _get_scoped(db, user, camera_id)
    await serializers.warm(db)
    since = utcnow() - timedelta(hours=24)
    up = await uptime_map(db, [cam.id])
    reads_24h = (await db.execute(select(func.count()).select_from(PlateRead).where(PlateRead.camera_id == cam.id, PlateRead.captured_at >= since))).scalar() or 0
    sightings_24h = (await db.execute(select(func.count()).select_from(Sighting).where(Sighting.camera_id == cam.id, Sighting.first_seen >= since))).scalar() or 0
    recent_alerts = (await db.execute(select(func.count()).select_from(Alert).where(Alert.camera_id == cam.id, Alert.created_at >= since))).scalar() or 0
    zones = (await db.execute(select(Zone).where(Zone.camera_id == cam.id).order_by(Zone.id))).scalars().all()
    snap = f"snapshots/cam_{cam.id}.jpg"
    data = serializers.camera_full(cam, user.role == "admin", up.get(cam.id))
    data.update({
        "recent_alerts": int(recent_alerts), "reads_24h": int(reads_24h), "sightings_24h": int(sightings_24h),
        "snapshot_url": media_url(snap) if abs_path(snap).exists() else None,
        "zones": [serializers.zone_item(z) for z in zones],
    })
    return data


@router.put("/cameras/{camera_id}", dependencies=[Depends(require_permission("cameras.write"))])
async def update_camera(camera_id: int, body: CameraUpdate, user: CurrentUser, db: DbDep, request: Request):
    cam = await _get_scoped(db, user, camera_id)
    await serializers.warm(db)
    before = serializers.camera_diff(cam)
    data = body.model_dump(exclude_unset=True)
    new_dept_id: int | None = None
    if "department_id" in data or "department_code" in data:
        if not can_change_department(user.role):
            raise forbidden("Only an admin can move a camera to another department")
        if data.get("department_id") is not None:
            if await db.get(Department, data["department_id"]) is None:
                raise validation_error("Unknown department", [{"field": "department_id", "message": "department does not exist"}])
            new_dept_id = data["department_id"]
        elif data.get("department_code"):
            code = await lookups.resolve_department_code(db, data["department_code"])
            if code is None:
                raise validation_error("Unknown department", [{"field": "department_code", "message": "department does not exist"}])
            new_dept_id = (await lookups.department_by_code(db, code)).id
    # validate the merged row through CameraImportRow rules; lat/lon are seeded from the camera so a
    # single-coordinate update still satisfies the both-or-neither pair rule
    merged: dict[str, Any] = {"external_id": data.get("external_id", cam.external_id), "name": data.get("name", cam.name), "lat": cam.lat, "lon": cam.lon}
    for k, v in data.items():
        if k in ("department_id", "department_code"):
            continue
        merged[k] = v.isoformat() if hasattr(v, "isoformat") else v
    parsed, issues = validate_row(merged, None, 0)
    if issues:
        raise validation_error("Invalid camera", [i.as_dict(bulk=True) for i in issues])
    assert parsed is not None
    relay_changed = False
    for k in data:
        if k in ("department_id", "department_code"):
            continue
        new = getattr(parsed, k, None) if hasattr(parsed, k) else data[k]
        if k in ("type", "ownership", "codec", "maintenance_status") and new is None:
            continue
        if getattr(cam, k) != new:
            if k in ("rtsp_url", "codec", "record_enabled", "anpr_enabled"):
                relay_changed = True
            setattr(cam, k, new)
    if new_dept_id is not None and cam.department_id != new_dept_id:
        cam.department_id = new_dept_id
    if "external_id" in data and data["external_id"] != before["external_id"]:
        dup = (await db.execute(select(Camera).where(Camera.source == cam.source, Camera.external_id == data["external_id"], Camera.id != cam.id))).scalar_one_or_none()
        if dup:
            raise conflict("external_id already used by another camera of the same source", existing_id=dup.id)
    cam.updated_at = utcnow()
    cam.relay_path = relay_path(cam.id) if cam.rtsp_url else None
    cam.geog = f"SRID=4326;POINT({cam.lon} {cam.lat})" if cam.lat is not None and cam.lon is not None else None
    await db.commit()
    await db.refresh(cam)
    relay_info: dict[str, Any] = {}
    if relay_changed:
        if cam.rtsp_url:
            results = await mtx.ensure_camera_paths(cam, recreate=True)
            relay_info = {"relay_paths_created": sum(1 for r in results if r.ok), "relay_paths_failed": sum(1 for r in results if not r.ok)}
        else:
            await mtx.remove_camera_paths(cam.id)
    after = serializers.camera_diff(cam)
    set_audit(request, entity="camera", entity_id=cam.id, before={k: v for k, v in before.items() if after.get(k) != v}, after={**{k: v for k, v in after.items() if before.get(k) != v}, **relay_info})
    return serializers.camera_full(cam, user.role == "admin")


@router.delete("/cameras/{camera_id}", status_code=204, dependencies=[Depends(require_permission("cameras.write"))])
async def retire_camera(camera_id: int, user: CurrentUser, db: DbDep, request: Request):
    cam = await _get_scoped(db, user, camera_id)
    if cam.status == "retired":
        raise conflict("Camera is already retired")
    before = {"status": cam.status}
    cam.status = "retired"
    cam.retired_at = utcnow()
    cam.last_status_change_at = utcnow()
    cam.updated_at = utcnow()
    await db.commit()
    await mtx.remove_camera_paths(cam.id)
    set_audit(request, entity="camera", entity_id=cam.id, before=before, after={"status": "retired", "retired_at": iso_z(cam.retired_at), "name": cam.name})
    return Response(status_code=204)


@router.put("/cameras/{camera_id}/maintenance", dependencies=[Depends(require_permission("cameras.write"))])
async def update_maintenance(camera_id: int, body: MaintenanceUpdate, user: CurrentUser, db: DbDep, request: Request):
    cam = await _get_scoped(db, user, camera_id)
    await serializers.warm(db)
    before = {k: serializers.camera_diff(cam)[k] for k in ("maintenance_status", "maintenance_note", "amc_vendor", "amc_expiry")}
    before["last_maintenance_at"] = iso_z(cam.last_maintenance_at)
    cam.maintenance_status = body.maintenance_status
    if body.last_maintenance_at is not None:
        try:
            cam.last_maintenance_at = parse_iso(body.last_maintenance_at)
        except ValueError:
            raise validation_error("Invalid timestamp", [{"field": "last_maintenance_at", "message": "must be ISO-8601"}])
    elif body.maintenance_status != "ok":
        cam.last_maintenance_at = cam.last_maintenance_at or utcnow()
    if body.maintenance_note is not None:
        cam.maintenance_note = body.maintenance_note
    if body.amc_vendor is not None:
        cam.amc_vendor = body.amc_vendor
    if body.amc_expiry is not None:
        cam.amc_expiry = body.amc_expiry
    cam.updated_at = utcnow()
    await db.commit()
    await db.refresh(cam)
    after = {k: serializers.camera_diff(cam)[k] for k in ("maintenance_status", "maintenance_note", "amc_vendor", "amc_expiry")}
    after["last_maintenance_at"] = iso_z(cam.last_maintenance_at)
    set_audit(request, entity="camera", entity_id=cam.id, before=before, after=after)
    return serializers.camera_full(cam, user.role == "admin")


@router.get("/cameras/{camera_id}/health", dependencies=[Depends(require_permission("cameras.read"))])
async def camera_health(camera_id: int, user: CurrentUser, db: DbDep, hours: int = Query(24, ge=1, le=24 * 30)):
    cam = await _get_scoped(db, user, camera_id)
    since = utcnow() - timedelta(hours=hours)
    rows = (
        await db.execute(select(CameraHealthLog).where(CameraHealthLog.camera_id == cam.id, CameraHealthLog.checked_at >= since).order_by(CameraHealthLog.checked_at.desc()).limit(1500))
    ).scalars().all()
    checks = len(rows)
    ready = sum(1 for r in rows if r.is_ready)
    transitions = []
    prev = None
    for r in reversed(rows):
        if prev is not None and r.status_after != prev:
            transitions.append({"at": iso_z(r.checked_at), "from": prev, "to": r.status_after})
        prev = r.status_after
    return {
        "camera_id": cam.id,
        "status": cam.status,
        "uptime_pct": round(100.0 * ready / checks, 1) if checks else None,
        "last_seen_at": iso_z(cam.last_seen_at),
        "last_status_change_at": iso_z(cam.last_status_change_at),
        "checks": checks,
        "log": [{"checked_at": iso_z(r.checked_at), "is_ready": r.is_ready, "has_video": r.has_video, "bytes_delta": r.bytes_delta, "readers": r.readers, "source_flag": r.source_flag, "status_after": r.status_after} for r in rows],
        "transitions": list(reversed(transitions)),
    }
