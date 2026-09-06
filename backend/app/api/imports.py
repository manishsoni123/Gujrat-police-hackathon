"""Camera onboarding: sandbox catalogue, CSV upload, bulk API (CONTRACT §5.2)."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.adapters.sandbox import SandboxCatalogueAdapter
from app.api.deps import CurrentUser, DbDep, require_api_key, require_permission, require_role, user_scope
from app.core.rbac import can_change_department
from app.core.errors import ApiError, bad_request, upstream_error, validation_error
from app.core.hashing import media_url
from app.core.tz import iso_z, utc_date_dir, utcnow
from app.db.models import Camera
from app.schemas.cameras import BulkImportRequest, SandboxImportRequest
from app.services import settings_service as cfg
from app.services.audit import set_audit
from app.services.camera_importer import upsert_cameras
from app.services.csv_importer import decode_csv_bytes, error_report_csv, parse_camera_csv
from app.services.mediamtx_client import client as mtx
from app.services.mediamtx_client import play_path
from app.services.report_builder import store_report
from app.services.sandbox_catalogue import CatalogueError
from app.services.sentinel_import import audit_summary, run_sentinel_import

log = logging.getLogger("sentinel.import")
router = APIRouter(tags=["imports"])

MAX_CSV_BYTES = 200 * 1024 * 1024
MAX_CATALOGUE_UPLOAD_BYTES = 5 * 1024 * 1024


def _truthy(v: str | bool | None, default: bool) -> bool:
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes", "on")


async def _sentinel_import(request: Request, db: DbDep, *, cameras_json: bytes | None, enrichment_csv: str | None, dry_run: bool, measure_first: bool, probe: bool):
    try:
        summary = await run_sentinel_import(db, cameras_json=cameras_json, enrichment_csv=enrichment_csv, dry_run=dry_run, measure_first=measure_first, probe=probe)
    except CatalogueError as exc:
        set_audit(request, entity="catalogue", after={"source": "sentinel_portal", "error": str(exc)})
        raise upstream_error(str(exc))
    set_audit(request, entity="catalogue", after=audit_summary(summary))
    return summary


@router.post("/cameras/import/sandbox", dependencies=[Depends(require_permission("cameras.write")), Depends(require_role("admin"))])
async def import_sandbox(user: CurrentUser, db: DbDep, request: Request, body: SandboxImportRequest | None = None):
    """Catalogue import. `catalogue.source = sentinel_portal` → organiser sandbox (portal login or the last
    uploaded / server-side cameras.json + enrichment CSV + probes); `mock` / `generic_json` → `{base_url}/api/ingest`."""
    body = body or SandboxImportRequest()
    if cfg.get("catalogue.source") == "sentinel_portal":
        return await _sentinel_import(request, db, cameras_json=None, enrichment_csv=None, dry_run=body.dry_run, measure_first=body.measure_first_stream, probe=body.probe)
    started = utcnow()
    adapter = SandboxCatalogueAdapter()
    try:
        cams = await adapter.list_cameras()
    except CatalogueError as exc:
        set_audit(request, entity="catalogue", after={"source_url": adapter.config.ingest_url, "error": str(exc)})
        raise upstream_error(str(exc))
    rows = []
    for i, c in enumerate(cams):
        row = c.as_row()
        row.update(c.extra)
        rows.append((i + 1, i, row))
    outcome = await upsert_cameras(
        db, rows, source="sandbox", created_by=None, created_via="sandbox", scope=None, dry_run=body.dry_run,
        department_aliases=cfg.get("catalogue.department_aliases") or {}, catalogue_defaults=True,
    )
    first_stream_ms = None
    if body.measure_first_stream and not body.dry_run:
        first_live = next((c for c in outcome.touched if c.live and c.rtsp_url), None)
        if first_live is None:
            from sqlalchemy import select

            first_live = (await db.execute(select(Camera).where(Camera.source == "sandbox", Camera.live.is_(True), Camera.rtsp_url.isnot(None)).order_by(Camera.id).limit(1))).scalar_one_or_none()
        if first_live is not None:
            path = play_path(first_live.id, first_live.codec, getattr(first_live, "meta", None))
            t_first = time.perf_counter()
            await mtx.trigger_hls(path)  # blocks until the HLS muxer has data (the on-demand pull itself)
            elapsed = await mtx.wait_ready(path, timeout_s=15.0)
            first_stream_ms = int((time.perf_counter() - t_first) * 1000) if elapsed is not None else None
    finished = utcnow()
    summary = {
        "source_url": adapter.config.ingest_url,
        "started_at": iso_z(started),
        "finished_at": iso_z(finished),
        "duration_ms": int((finished - started).total_seconds() * 1000),
        "fetched": len(cams),
        "added": outcome.added,
        "updated": outcome.updated,
        "unchanged": outcome.unchanged,
        "errors": [e.as_dict() for e in outcome.errors],
        "warnings": [w.as_dict() for w in outcome.warnings],
        "relay_paths_created": outcome.relay_paths_created,
        "relay_paths_failed": outcome.relay_paths_failed,
        "anpr_enabled": outcome.anpr_enabled,
        "first_stream_ready_ms": first_stream_ms,
        "unmapped_fields": adapter.unmapped_fields,
        "dry_run": body.dry_run,
    }
    set_audit(request, entity="catalogue", after={k: v for k, v in summary.items() if k not in ("errors", "warnings")} | {"errors": len(summary["errors"]), "warnings": len(summary["warnings"])})
    return summary


@router.post("/cameras/import/sandbox/file", dependencies=[Depends(require_permission("cameras.write")), Depends(require_role("admin"))])
async def import_sandbox_file(
    user: CurrentUser,
    db: DbDep,
    request: Request,
    cameras_json: UploadFile | None = File(None),
    enrichment_csv: UploadFile | None = File(None),
    dry_run: str = Form("false"),
    measure_first_stream: str = Form("true"),
    probe: str = Form("true"),
):
    """Organiser-sandbox import with an uploaded `cameras.json` (the portal catalogue needs a browser session, so the
    saved copy is the primary path) and optionally an enrichment CSV. Both files are kept under `DATA_DIR/catalogue/`
    as the "last uploaded" fallback. Works regardless of `catalogue.source`."""
    cam_bytes: bytes | None = None
    enr_text: str | None = None
    if cameras_json is not None and cameras_json.filename:
        cam_bytes = await cameras_json.read()
        if len(cam_bytes) > MAX_CATALOGUE_UPLOAD_BYTES:
            raise ApiError(413, "cameras.json larger than 5 MB")
        if not cam_bytes.strip():
            cam_bytes = None
    if enrichment_csv is not None and enrichment_csv.filename:
        raw = await enrichment_csv.read()
        if len(raw) > MAX_CATALOGUE_UPLOAD_BYTES:
            raise ApiError(413, "enrichment CSV larger than 5 MB")
        if raw.strip():
            enr_text = decode_csv_bytes(raw)
            from app.adapters.sentinel_portal import UPLOADED_ENRICHMENT_CSV, save_upload

            try:
                save_upload(UPLOADED_ENRICHMENT_CSV, raw)
            except OSError:
                log.warning("could not keep the uploaded enrichment CSV")
    return await _sentinel_import(
        request, db, cameras_json=cam_bytes, enrichment_csv=enr_text,
        dry_run=_truthy(dry_run, False), measure_first=_truthy(measure_first_stream, True), probe=_truthy(probe, True),
    )


@router.post("/cameras/import/csv", dependencies=[Depends(require_permission("cameras.write"))])
async def import_csv(user: CurrentUser, db: DbDep, request: Request, file: UploadFile = File(...), dry_run: str = Form("false")):
    started = time.perf_counter()
    if file.content_type and file.content_type not in ("text/csv", "application/vnd.ms-excel", "application/octet-stream", "text/plain", "application/csv"):
        raise bad_request(f"Unsupported file type {file.content_type}; upload a CSV")
    data = await file.read()
    if len(data) > MAX_CSV_BYTES:
        raise ApiError(413, "CSV larger than 200 MB")
    text = decode_csv_bytes(data)
    parsed = parse_camera_csv(text)
    if parsed.header_error and not parsed.rows:
        raise validation_error(f"Unusable CSV header: {parsed.header_error}", [{"field": "file", "message": parsed.header_error}])
    is_dry = str(dry_run).strip().lower() in ("true", "1", "yes")
    username, user_id = user.username, user.id  # plain copies: never depend on ORM state after the import
    rows = [(rn, None, raw) for rn, raw in zip(parsed.row_numbers, parsed.rows)]
    outcome = await upsert_cameras(
        db, rows, source="csv", created_by=user_id, created_via="csv", scope=user_scope(user), dry_run=is_dry,
        present_fields_per_row=parsed.present_fields, allow_department_change=can_change_department(user.role),
    )
    # CONTRACT §12.1: re-importing the same file reports every matched row as `updated` (unchanged rows included)
    updated_rows = outcome.updated + outcome.unchanged
    warnings = [w.as_dict() for w in outcome.warnings]
    for col in parsed.unknown_columns:
        warnings.append({"row": 0, "external_id": None, "field": col, "message": f"unknown column '{col}' ignored"})
    if parsed.header_error:
        warnings.append({"row": 0, "external_id": None, "field": "file", "message": parsed.header_error})
    job_id = f"{utcnow().strftime('%Y-%m-%dT%H-%M-%S')}_{uuid.uuid4().hex[:4]}"
    errors = [e.as_dict() for e in outcome.errors]
    error_url = None
    if errors:
        lines = dict(zip(parsed.row_numbers, parsed.original_lines))
        csv_bytes = error_report_csv(errors, lines).encode("utf-8")
        rf = await store_report(db, "import_errors_csv", csv_bytes, "csv", username, user_id, {"job_id": job_id, "file": file.filename}, len(errors), subdir="exports", basename=f"import_errors_{job_id}.csv")
        error_url = media_url(rf.path)
    result = {
        "job_id": job_id,
        "dry_run": is_dry,
        "rows_total": len(parsed.rows),
        "added": outcome.added,
        "updated": updated_rows,
        "unchanged": outcome.unchanged,
        "errors": errors,
        "warnings": warnings,
        "error_report_url": error_url,
        "relay_paths_created": outcome.relay_paths_created,
        "relay_paths_failed": outcome.relay_paths_failed,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    set_audit(request, entity="import_job", entity_id=job_id, after={k: v for k, v in result.items() if k not in ("errors", "warnings")} | {"errors": len(errors), "warnings": len(warnings), "file": file.filename})
    return result


@router.post("/v1/cameras/bulk", dependencies=[Depends(require_api_key("bulk"))])
async def bulk_import(request: Request, db: DbDep):
    """Bulk onboarding for departmental systems (X-API-Key with scope `bulk`)."""
    started = time.perf_counter()
    try:
        payload = await request.json()
    except ValueError:
        raise validation_error("Body must be JSON", [{"field": "body", "message": "invalid JSON"}])
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        try:
            items = BulkImportRequest.model_validate(payload).cameras
        except Exception as exc:  # noqa: BLE001
            raise validation_error("Invalid body", [{"field": "cameras", "message": str(exc)[:200]}])
    else:
        raise validation_error("Body must be a list or an object with `cameras`", [{"field": "body", "message": "list or object required"}])
    if len(items) > 1000:
        raise validation_error("Too many cameras", [{"field": "cameras", "message": "max 1000 per request"}])
    rows = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise validation_error("Invalid camera entry", [{"index": i, "field": "camera", "message": "must be an object"}])
        rows.append((None, i, it))
    outcome = await upsert_cameras(db, rows, source="api", created_by=None, created_via="api", scope=None)
    result = {
        "added": outcome.added,
        "updated": outcome.updated,
        "unchanged": outcome.unchanged,
        "errors": [e.as_dict(bulk=True) for e in outcome.errors],
        "warnings": [w.as_dict(bulk=True) for w in outcome.warnings],
        "results": [{"index": r["index"], "external_id": r["external_id"], "id": r["id"], "action": r["action"]} for r in outcome.results],
        "relay_paths_created": outcome.relay_paths_created,
        "relay_paths_failed": outcome.relay_paths_failed,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    set_audit(request, entity="bulk_import", after={"added": result["added"], "updated": result["updated"], "errors": len(result["errors"]), "count": len(items)})
    return result
