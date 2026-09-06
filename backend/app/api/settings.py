"""Settings endpoints (CONTRACT §5.20, §5.22): masked GET, validated PUT, catalogue test, public subset."""

from __future__ import annotations

import re
import time
from typing import Any

from fastapi import APIRouter, Depends, File, Request, UploadFile

from app import __version__
from app.api.deps import CurrentUser, DbDep, require_permission
from app.core.config import settings as env
from app.core.errors import ApiError, bad_request, validation_error
from app.core.tz import iso_z
from app.schemas.admin import CatalogueTestRequest, SettingsUpdate
from app.schemas.cameras import CameraImportRow
from app.services import gap_analysis, lookups
from app.services import settings_service as cfg
from app.services.audit import set_audit
from app.services.matcher import matcher
from app.services.sandbox_catalogue import CatalogueConfig, CatalogueError, fetch_catalogue, map_item

router = APIRouter(tags=["settings"])

CATALOGUE_SOURCE_LABELS = {
    "mock": "Built-in mock sandbox (50 synthetic cameras)",
    "generic_json": "Generic /api/ingest catalogue host",
    "sentinel_portal": "Organiser Sentinel sandbox (cctv.corp8.cloud, 30 real cameras)",
}
_CAMERA_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
MAX_ENRICHMENT_BYTES = 5 * 1024 * 1024


def catalogue_source() -> str:
    v = str(cfg.get("catalogue.source") or "mock")
    return v if v in CATALOGUE_SOURCE_LABELS else "mock"


def mock_badge_active() -> bool:
    """The MOCK SANDBOX badge shows only while the mock is served **and** selected as the catalogue source."""
    return bool(env.MOCK_SANDBOX) and catalogue_source() == "mock"


def _items() -> dict[str, Any]:
    out = []
    for key in cfg.REGISTRY:
        m = cfg.meta(key)
        out.append({
            "key": key,
            "value": cfg.masked(key),
            "is_secret": m["is_secret"],
            "updated_by_username": lookups.username_sync(m.get("updated_by")),
            "updated_at": iso_z(m.get("updated_at")),
        })
    return {"items": out}


@router.get("/settings", dependencies=[Depends(require_permission("admin.settings"))])
async def get_settings(db: DbDep):
    await lookups.usernames(db)
    return _items()


@router.put("/settings", dependencies=[Depends(require_permission("admin.settings"))])
async def put_settings(body: SettingsUpdate, user: CurrentUser, db: DbDep, request: Request):
    clean = cfg.validate_values(body.values)
    before, after = await cfg.set_many(db, clean, user.id)
    if any(k.startswith("alerts.") for k in clean):
        await matcher.reload(db)
    if any(k.startswith("gap.") for k in clean):
        gap_analysis._cache.clear()
    await lookups.usernames(db)
    set_audit(request, entity="settings", entity_id=",".join(sorted(clean))[:64], before=before, after=after)
    return _items()


MAX_SAMPLE_BYTES = 8 * 1024


def _truncate_sample(sample: Any) -> Any:
    """The first catalogue object, bounded: a hostile/huge upstream cannot echo megabytes into the admin UI or the audit row."""
    import json

    if not isinstance(sample, dict):
        return sample
    if len(json.dumps(sample, default=str)) <= MAX_SAMPLE_BYTES:
        return sample
    out: dict[str, Any] = {}
    for k, v in list(sample.items())[:60]:
        out[str(k)[:64]] = v if isinstance(v, (int, float, bool)) or v is None else str(v)[:200]
    out["_truncated"] = True
    return out


async def _test_sentinel(request: Request, body: CatalogueTestRequest | None) -> dict[str, Any]:
    """Probe one sandbox camera (cam01 by default) with the stored/supplied stream credentials and check the
    catalogue path (portal login when a portal password exists, else the file). Secrets are never echoed."""
    from app.adapters.sentinel_portal import SentinelPortalAdapter, fetch_portal_cameras_json, portal_config_from_settings, resolve_cameras_json_path, resolve_enrichment_path, stream_config_from_settings
    from app.services.sentinel_catalogue import CatalogueParseError, parse_cameras_json

    o = body.model_dump(exclude_unset=True) if body else {}
    camera_id = str(o.get("camera_id") or "cam01").strip()
    if not _CAMERA_ID_RE.match(camera_id):
        raise validation_error("Invalid camera id", [{"field": "camera_id", "message": "letters, digits, '_', '-' and '.' only"}])
    stream = stream_config_from_settings(o)
    portal = portal_config_from_settings(o)
    started = time.perf_counter()
    adapter = SentinelPortalAdapter(stream=stream, portal=portal)
    res = await adapter.probe_one(camera_id)
    probe = {"external_id": camera_id, **res.as_dict()}
    probe["summary"] = f"{camera_id}: {(res.codec or 'UNKNOWN').replace('H26', 'H.26')} {res.resolution or '?'}" + (f" @ {res.fps:g} fps" if res.fps else "") if res.ok else f"{camera_id}: {res.error}"
    catalogue: dict[str, Any] = {"mode": "portal" if portal.configured else "file", "ok": False, "count": None, "source": None, "error": None, "notes": []}
    if portal.configured and (body is None or body.check_portal):
        try:
            data, notes = await fetch_portal_cameras_json(portal)
            entries = parse_cameras_json(data)
            catalogue.update({"ok": True, "count": len(entries), "source": f"{portal.url}/cameras.json", "notes": notes})
        except (CatalogueError, CatalogueParseError) as exc:
            catalogue.update({"error": str(exc)})
    if not catalogue["ok"]:
        p = resolve_cameras_json_path()
        if p is not None:
            try:
                entries = parse_cameras_json(p.read_bytes())
                fallback = {"ok": True, "count": len(entries), "source": str(p)}
                if catalogue["mode"] == "portal":
                    catalogue["fallback"] = fallback
                else:
                    catalogue.update(fallback)
            except (OSError, CatalogueParseError) as exc:
                catalogue["error"] = catalogue["error"] or f"{p}: {exc}"
        elif catalogue["error"] is None:
            catalogue["error"] = "no cameras.json found: upload one on the Import page or set catalogue.cameras_json_path"
    enrichment_path = resolve_enrichment_path()
    result = {
        "ok": bool(res.ok),
        "source": "sentinel_portal",
        "stream": {"host": stream.host, "rtsp_port": stream.rtsp_port, "whep_port": stream.whep_port, "email": stream.email, "configured": stream.configured},
        "probe": probe,
        "catalogue": catalogue,
        "enrichment_path": str(enrichment_path) if enrichment_path else None,
        "portal_configured": portal.configured,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "error": None if res.ok else probe["summary"],
    }
    set_audit(request, entity="catalogue", after={"source": "sentinel_portal", "camera_id": camera_id, "ok": res.ok, "codec": res.codec, "resolution": res.resolution, "catalogue_ok": catalogue["ok"], "catalogue_mode": catalogue["mode"]})
    return result


@router.post("/settings/catalogue/enrichment", dependencies=[Depends(require_permission("admin.settings"))])
async def upload_enrichment(user: CurrentUser, db: DbDep, request: Request, file: UploadFile = File(...)):
    """Upload the team's enrichment CSV (external_id,name,lat,lon,district,…); it becomes `catalogue.enrichment_path`."""
    from app.adapters.sentinel_portal import UPLOADED_ENRICHMENT_CSV, save_upload
    from app.services.csv_importer import decode_csv_bytes
    from app.services.sentinel_catalogue import CatalogueParseError, parse_enrichment_csv

    if file.content_type and file.content_type not in ("text/csv", "application/vnd.ms-excel", "application/octet-stream", "text/plain", "application/csv"):
        raise bad_request(f"Unsupported file type {file.content_type}; upload a CSV")
    raw = await file.read()
    if len(raw) > MAX_ENRICHMENT_BYTES:
        raise ApiError(413, "enrichment CSV larger than 5 MB")
    try:
        table = parse_enrichment_csv(decode_csv_bytes(raw))
    except CatalogueParseError as exc:
        raise validation_error(f"Unusable enrichment CSV: {exc}", [{"field": "file", "message": str(exc)}])
    path = save_upload(UPLOADED_ENRICHMENT_CSV, raw)
    before, after = await cfg.set_many(db, {"catalogue.enrichment_path": str(path)}, user.id)
    result = {
        "path": str(path), "rows": len(table.rows), "confidence": table.confidence_counts, "warnings": table.warnings,
        "unknown_columns": table.unknown_columns, "with_coordinates": sum(1 for r in table.rows.values() if r.lat is not None),
        "departments": sorted({r.department_code for r in table.rows.values() if r.department_code}),
    }
    set_audit(request, entity="settings", entity_id="catalogue.enrichment_path", before=before, after={**after, "rows": len(table.rows), "file": file.filename})
    return result


@router.post("/settings/catalogue/test", dependencies=[Depends(require_permission("admin.settings"))])
async def test_catalogue(db: DbDep, request: Request, body: CatalogueTestRequest | None = None):
    source = (body.source if body and body.source else None) or catalogue_source()
    if source == "sentinel_portal":
        return await _test_sentinel(request, body)
    overrides = {k: v for k, v in (body.model_dump(exclude_unset=True) if body else {}).items() if v is not None and v != cfg.MASK and k in ("base_url", "auth_type", "auth_username", "auth_password", "auth_header", "timeout_s", "field_map")}
    config = CatalogueConfig.from_settings(overrides)
    started = time.perf_counter()
    try:
        items, status, duration = await fetch_catalogue(config)
    except CatalogueError as exc:
        result = {"ok": False, "error": str(exc), "status": exc.status, "url": config.ingest_url, "duration_ms": int((time.perf_counter() - started) * 1000)}
        set_audit(request, entity="catalogue", after={"url": config.ingest_url, "ok": False})
        return result
    sample = _truncate_sample(items[0]) if items else None
    mapped_sample: dict[str, Any] | None = None
    unmapped: list[str] = []
    mapping_error: str | None = None
    if sample is not None:
        row, unmapped = map_item(sample, config.field_map)
        try:
            mapped_sample = CameraImportRow.model_validate(row).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            mapped_sample = row
            mapping_error = str(exc)[:300]
    result = {
        "ok": True,
        "source": source,
        "status": status,
        "url": config.ingest_url,
        "count": len(items),
        "sample": sample,
        "mapped_sample": mapped_sample,
        "mapping_error": mapping_error,
        "unmapped_fields": unmapped,
        "duration_ms": duration,
    }
    set_audit(request, entity="catalogue", after={"url": config.ingest_url, "ok": True, "count": len(items)})
    return result


@router.get("/settings/public", dependencies=[Depends(require_permission("settings.read_public"))])
async def public_settings():
    src = catalogue_source()
    return {
        **cfg.public_values(),
        # badge: only while the built-in mock is both served (MOCK_SANDBOX=1) and selected as the catalogue source
        "mock_sandbox": mock_badge_active(),
        "mock_sandbox_served": bool(env.MOCK_SANDBOX),
        "catalogue_source": src,
        "catalogue_source_label": CATALOGUE_SOURCE_LABELS[src],
        "sandbox_stream_host": str(cfg.get("sandbox.stream_host") or "") if src == "sentinel_portal" else None,
        "version": __version__,
        "product_name": env.PRODUCT_NAME,
    }
