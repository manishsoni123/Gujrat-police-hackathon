"""Settings endpoints (CONTRACT §5.20, §5.22): masked GET, validated PUT, catalogue test, public subset."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Request

from app import __version__
from app.api.deps import CurrentUser, DbDep, require_permission
from app.core.config import settings as env
from app.core.tz import iso_z
from app.schemas.admin import CatalogueTestRequest, SettingsUpdate
from app.schemas.cameras import CameraImportRow
from app.services import gap_analysis, lookups
from app.services import settings_service as cfg
from app.services.audit import set_audit
from app.services.matcher import matcher
from app.services.sandbox_catalogue import CatalogueConfig, CatalogueError, fetch_catalogue, map_item

router = APIRouter(tags=["settings"])


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


@router.post("/settings/catalogue/test", dependencies=[Depends(require_permission("admin.settings"))])
async def test_catalogue(db: DbDep, request: Request, body: CatalogueTestRequest | None = None):
    overrides = {k: v for k, v in (body.model_dump(exclude_unset=True) if body else {}).items() if v is not None and v != cfg.MASK}
    config = CatalogueConfig.from_settings(overrides)
    started = time.perf_counter()
    try:
        items, status, duration = await fetch_catalogue(config)
    except CatalogueError as exc:
        result = {"ok": False, "error": str(exc), "status": exc.status, "url": config.ingest_url, "duration_ms": int((time.perf_counter() - started) * 1000)}
        set_audit(request, entity="catalogue", after={"url": config.ingest_url, "ok": False})
        return result
    sample = items[0] if items else None
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
    return {**cfg.public_values(), "mock_sandbox": bool(env.MOCK_SANDBOX), "version": __version__, "product_name": env.PRODUCT_NAME}
