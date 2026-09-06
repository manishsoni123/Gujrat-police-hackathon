"""Shared camera upsert used by sandbox, CSV, bulk and manual paths (CONTRACT §5 import rules)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rbac import Scope
from app.core.tz import utcnow
from app.db.models import Camera
from app.schemas.cameras import CameraImportRow
from app.services import lookups
from app.services.mediamtx_client import client as mtx
from app.services.mediamtx_client import relay_path

log = logging.getLogger("sentinel.import")


@dataclass
class RowIssue:
    row: int | None
    external_id: str | None
    field: str | None
    message: str
    index: int | None = None

    def as_dict(self, bulk: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if bulk:
            d["index"] = self.index
        else:
            d["row"] = self.row
        d["external_id"] = self.external_id
        d["field"] = self.field
        d["message"] = self.message
        return d


@dataclass
class ImportOutcome:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list[RowIssue] = field(default_factory=list)
    warnings: list[RowIssue] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    touched: list[Camera] = field(default_factory=list)
    relay_paths_created: int = 0
    relay_paths_failed: int = 0
    anpr_enabled: int = 0
    duration_ms: int = 0


def validation_issues(exc: ValidationError, row: int | None, index: int | None, external_id: str | None) -> list[RowIssue]:
    out: list[RowIssue] = []
    for e in exc.errors():
        loc = [str(p) for p in e.get("loc", ())]
        fld = ".".join(loc) if loc else None
        msg = e.get("msg", "invalid")
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]
        if e.get("type") == "missing" or (fld in ("external_id", "name") and e.get("type") == "string_too_short"):
            msg = "field required"
        if fld is None and "lat and lon" in msg:
            fld = "lat"
        out.append(RowIssue(row, external_id, fld, msg, index))
    return out


def validate_row(raw: dict[str, Any], row: int | None = None, index: int | None = None) -> tuple[CameraImportRow | None, list[RowIssue]]:
    ext = str(raw.get("external_id") or "").strip() or None
    try:
        return CameraImportRow.model_validate(raw), []
    except ValidationError as exc:
        return None, validation_issues(exc, row, index, ext)


# Columns copied from CameraImportRow onto Camera (present-only semantics on update)
_COPY_FIELDS = (
    "name", "type", "ownership", "lat", "lon", "address", "district", "police_station", "ward", "rtsp_url",
    "whep_url", "hls_url", "codec", "resolution", "fps", "live", "storage_location", "retention_days",
    "install_date", "vendor", "model", "heading_deg", "fov_deg", "connectivity_type", "bandwidth_kbps",
    "vms_platform", "nvr_id", "onvif_host", "amc_vendor", "amc_expiry", "maintenance_status", "location_confidence",
)

_RELAY_TRIGGER_FIELDS = ("rtsp_url", "codec", "record_enabled", "anpr_enabled")


def merged_metadata(current: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, Any] | None:
    """Shallow merge of the JSONB bag: top-level keys of `incoming` replace the stored ones, others survive
    (a re-import refreshes `enrichment`/`probe`/`catalogue` without wiping keys another path wrote)."""
    if incoming is None:
        return current
    out = dict(current or {})
    for k, v in incoming.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out or None


def pick_existing(rows: list[Camera], source: str) -> dict[str, Camera]:
    """external_id → the row an import under `source` must update when it may match **any** source.

    Preference per external_id: a row already of `source`, else a non-retired row, else a retired one; ties by
    lowest id. Used by the organiser-sandbox importer so cameras first loaded through the CSV path (or the bulk
    API) are converted in place — same `id`, relay path and ANPR assignment — instead of duplicated
    (CONTRACT Amendments 2026-09-05).
    """
    best: dict[str, Camera] = {}

    def rank(c: Camera) -> tuple[int, int, int]:
        return (0 if c.source == source else 1, 0 if c.status != "retired" else 1, c.id or 0)

    for c in sorted(rows, key=rank):
        best.setdefault(c.external_id, c)
    return best


async def _resolve_department(db: AsyncSession, code: str | None, aliases: dict[str, str] | None) -> tuple[int, str | None]:
    """Return (department_id, warning or None)."""
    if code is None:
        return await lookups.unassigned_id(db), None
    resolved = await lookups.resolve_department_code(db, code, aliases)
    if resolved is None:
        return await lookups.unassigned_id(db), f"unknown department '{code}' mapped to UNASSIGNED"
    dept = await lookups.department_by_code(db, resolved)
    return dept.id, None


async def upsert_cameras(
    db: AsyncSession,
    rows: list[tuple[int | None, int | None, dict[str, Any]]],
    source: str,
    created_by: int | None,
    created_via: str,
    scope: Scope | None = None,
    dry_run: bool = False,
    department_aliases: dict[str, str] | None = None,
    catalogue_defaults: bool = False,
    present_fields_per_row: list[set[str]] | None = None,
    allow_department_change: bool = True,
    match_any_source: bool = False,
) -> ImportOutcome:
    """Validate and upsert rows `(row_no, index, raw_dict)` on `(source, external_id)`.

    - `present_fields_per_row`: for CSV, the set of non-blank columns per row so that blanks
      mean "leave unchanged" on update. When None every key of the dict counts as present.
    - `catalogue_defaults`: apply the sandbox auto-ANPR rule to new cameras.
    - `scope`: a restricted scope (dept_admin) may only create cameras in its own department
      and only update cameras already in it; anything else is a row error (CONTRACT §2.3).
    - `allow_department_change`: whether an update may move an existing camera to another
      department (`rbac.can_change_department`, admin only). Otherwise the row's
      `department_code` is validated but the stored department is kept, with a warning.
    - `dry_run`: validate and count only; nothing is added to or changed in the session, so
      the caller's session (and its loaded `User`) stays intact.
    - `match_any_source`: look existing rows up by `external_id` across **every** source (see
      `pick_existing`) and convert a match to `source` in place; the relay path (`cam_<id>`) and
      the ANPR/recording flags survive and no duplicate is created.
    """
    started = time.perf_counter()
    out = ImportOutcome()
    seen: dict[str, int] = {}  # external_id → first row/index
    restricted = scope is not None and not scope.unrestricted

    enabled_count = 0
    if catalogue_defaults:
        enabled_count = int(
            (await db.execute(select(func.count()).select_from(Camera).where(Camera.anpr_enabled.is_(True), Camera.status != "retired"))).scalar() or 0
        )

    if match_any_source:
        wanted = {str(raw.get("external_id") or "").strip() for _r, _i, raw in rows}
        wanted.discard("")
        existing_rows = (await db.execute(select(Camera).where(Camera.external_id.in_(wanted)))).scalars().all() if wanted else []
        existing = pick_existing(list(existing_rows), source)
    else:
        existing_rows = (await db.execute(select(Camera).where(Camera.source == source))).scalars().all()
        existing = {c.external_id: c for c in existing_rows}

    for i, (row_no, index, raw) in enumerate(rows):
        label = row_no if row_no is not None else index
        ext_raw = str(raw.get("external_id") or "").strip()
        result_entry = {"index": index, "row": row_no, "external_id": ext_raw or None, "id": None, "action": "error"}
        parsed, issues = validate_row(raw, row_no, index)
        if ext_raw:
            if ext_raw in seen:
                first = seen[ext_raw]
                kind = "row" if row_no is not None else "index"
                issues.insert(0, RowIssue(row_no, ext_raw, "external_id", f"duplicate external_id in file (first seen at {kind} {first})", index))
            else:
                seen[ext_raw] = label if label is not None else 0
        if issues:
            out.errors.extend(issues)
            out.results.append(result_entry)
            continue
        assert parsed is not None

        present = set(raw.keys()) if present_fields_per_row is None else present_fields_per_row[i]
        cam = existing.get(parsed.external_id)

        # --- department resolution and scope (CONTRACT §2.3: a scoped miss is a row error)
        if cam is not None and restricted and not scope.allows(cam.department_id, cam.district):  # type: ignore[union-attr]
            out.errors.append(RowIssue(row_no, parsed.external_id, "external_id", "camera belongs to another department", index))
            out.results.append(result_entry)
            continue
        warn: str | None = None
        if parsed.department_code is None and cam is not None:
            dept_id = cam.department_id  # blank on update = keep
        elif parsed.department_code is None and restricted:
            dept_id = int(scope.department_id)  # type: ignore[union-attr,arg-type]
        else:
            dept_id, warn = await _resolve_department(db, parsed.department_code, department_aliases)
        if warn:
            out.warnings.append(RowIssue(row_no, parsed.external_id, "department_code", warn, index))
        if restricted and (dept_id != scope.department_id or (scope.district and parsed.district and parsed.district != scope.district)):  # type: ignore[union-attr]
            out.errors.append(RowIssue(row_no, parsed.external_id, "department_code", "outside your department scope", index))
            out.results.append(result_entry)
            continue
        if parsed.outside_gujarat():
            out.warnings.append(RowIssue(row_no, parsed.external_id, "lat", "coordinates are outside the Gujarat bounding box", index))

        if cam is None:
            cam = _new_camera(parsed, source, dept_id, created_by, created_via)
            if catalogue_defaults:
                auto = bool(parsed.live) and enabled_count < settings.ANPR_AUTO_ENABLE_MAX
                if parsed.anpr_enabled is not None:
                    auto = parsed.anpr_enabled
                cam.anpr_enabled = auto
                cam.record_enabled = parsed.record_enabled if parsed.record_enabled is not None else auto
                if auto:
                    enabled_count += 1
            else:
                cam.anpr_enabled = bool(parsed.anpr_enabled)
                cam.record_enabled = bool(parsed.record_enabled)
            if cam.anpr_enabled:
                out.anpr_enabled += 1
            if not dry_run:
                db.add(cam)
                await db.flush()
                cam.relay_path = relay_path(cam.id) if cam.rtsp_url else None
                _set_geog(cam)
                existing[cam.external_id] = cam
                out.touched.append(cam)
            out.added += 1
            result_entry.update({"id": cam.id if not dry_run else None, "action": "added"})
        else:
            changes = _pending_changes(cam, parsed, present)
            if "department_code" in present and cam.department_id != dept_id:
                if allow_department_change:
                    changes["department_id"] = dept_id
                else:
                    out.warnings.append(RowIssue(row_no, parsed.external_id, "department_code", "department not changed (only an admin can move a camera to another department)", index))
            if cam.status == "retired":
                changes["status"] = "unknown"
                changes["retired_at"] = None
            if cam.source != source:
                # cross-source match (organiser sandbox re-importing rows first loaded via CSV/API): convert in place
                changes["source"] = source
                out.warnings.append(RowIssue(row_no, parsed.external_id, "source", f"existing {cam.source} camera #{cam.id} converted to {source} (same id, relay path and ANPR assignment kept)", index))
            if changes:
                if not dry_run:
                    for f, v in changes.items():
                        setattr(cam, f, v)
                    cam.updated_at = utcnow()
                    cam.relay_path = relay_path(cam.id) if cam.rtsp_url else None
                    _set_geog(cam)
                    out.touched.append(cam)
                out.updated += 1
                result_entry.update({"id": cam.id, "action": "updated"})
            else:
                out.unchanged += 1
                result_entry.update({"id": cam.id, "action": "unchanged"})
        out.results.append(result_entry)

    if not dry_run:
        await db.commit()
        await ensure_relay_paths(out)
    out.duration_ms = int((time.perf_counter() - started) * 1000)
    return out


def _new_camera(parsed: CameraImportRow, source: str, dept_id: int, created_by: int | None, created_via: str) -> Camera:
    """Build (but do not add) a Camera from a validated row; the caller decides about the session."""
    cam = Camera(
        source=source,
        external_id=parsed.external_id,
        name=parsed.name,
        department_id=dept_id,
        type=parsed.type or "ip",
        ownership=parsed.ownership or "govt_dept",
        codec=parsed.codec or "UNKNOWN",
        maintenance_status=parsed.maintenance_status or "ok",
        created_by=created_by,
        created_via=created_via,
        status="unknown",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    for f in _COPY_FIELDS:
        if f in ("name", "type", "ownership", "codec", "maintenance_status"):
            continue
        setattr(cam, f, getattr(parsed, f))
    cam.meta = merged_metadata(None, parsed.metadata)
    return cam


def _pending_changes(cam: Camera, parsed: CameraImportRow, present: set[str]) -> dict[str, Any]:
    """Field → new value for every present column whose value differs (nothing is applied here)."""
    changes: dict[str, Any] = {}
    for f in _COPY_FIELDS:
        if f not in present:
            continue
        new = getattr(parsed, f)
        if f in ("type", "ownership", "codec", "maintenance_status") and new is None:
            continue
        if getattr(cam, f) != new:
            changes[f] = new
    for f in ("anpr_enabled", "record_enabled"):
        new = getattr(parsed, f)
        if f in present and new is not None and getattr(cam, f) != new:
            changes[f] = new
    if "metadata" in present and parsed.metadata is not None:
        merged = merged_metadata(cam.meta, parsed.metadata)
        if merged != cam.meta:
            changes["meta"] = merged
    return changes


def _set_geog(cam: Camera) -> None:
    if cam.lat is not None and cam.lon is not None:
        cam.geog = f"SRID=4326;POINT({cam.lon} {cam.lat})"
    else:
        cam.geog = None


async def ensure_relay_paths(out: ImportOutcome, recreate: bool = False) -> None:
    for cam in out.touched:
        if not cam.rtsp_url or cam.status == "retired":
            continue
        results = await mtx.ensure_camera_paths(cam, recreate=recreate)
        for r in results:
            if r.ok:
                out.relay_paths_created += 1
            else:
                out.relay_paths_failed += 1
                out.warnings.append(RowIssue(None, cam.external_id, "relay", r.detail))
