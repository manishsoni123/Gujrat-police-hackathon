"""Watchlist CRUD + CSV import (CONTRACT §5.11)."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, DbDep, require_permission
from app.core.errors import ApiError, conflict, not_found, validation_error
from app.core.hashing import media_url
from app.core.tz import parse_iso, utcnow
from app.db.models import PRIORITIES, REASONS, WATCHLIST_SOURCES, Alert, Watchlist
from app.schemas.analytics import WatchlistCreate, WatchlistUpdate
from app.schemas.cameras import parse_bool
from app.schemas.common import PageParams
from app.services import lookups, serializers
from app.services.audit import set_audit
from app.services.csv_importer import decode_csv_bytes, error_report_csv, parse_watchlist_csv, watchlist_template_csv
from app.services.matcher import matcher
from app.services.plates import normalise
from app.services.report_builder import store_report

router = APIRouter(tags=["watchlist"])

SORTS: dict[str, Any] = {"created_at": Watchlist.created_at, "plate_norm": Watchlist.plate_norm, "priority": Watchlist.priority, "hit_count": Watchlist.hit_count, "last_hit_at": Watchlist.last_hit_at, "name": Watchlist.name}
PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _resolve_plate(entity_type: str, plate: str | None) -> str | None:
    if entity_type != "vehicle":
        return normalise(plate).plate_norm or None if plate else None
    n = normalise(plate or "")
    if not n.is_valid_format:
        raise validation_error("Invalid plate", [{"field": "plate", "message": "not a valid Indian registration format"}])
    return n.plate_norm


async def _active_duplicate(db, plate_norm: str | None, exclude_id: int | None = None) -> Watchlist | None:
    if not plate_norm:
        return None
    stmt = select(Watchlist).where(Watchlist.entity_type == "vehicle", Watchlist.plate_norm == plate_norm, Watchlist.is_active.is_(True))
    if exclude_id:
        stmt = stmt.where(Watchlist.id != exclude_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _alerts_24h(db, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    since = utcnow() - timedelta(hours=24)
    rows = (await db.execute(select(Alert.watchlist_id, func.count()).where(Alert.watchlist_id.in_(ids), Alert.created_at >= since).group_by(Alert.watchlist_id))).all()
    return {r[0]: int(r[1]) for r in rows}


@router.get("/watchlist", dependencies=[Depends(require_permission("analytics.read"))])
async def list_watchlist(
    user: CurrentUser, db: DbDep, page: PageParams = Depends(), q: str | None = None, entity_type: str | None = None, reason: str | None = None,
    priority: str | None = None, source: str | None = None, is_active: str = "true", expired: bool | None = None,
):
    sort, order = page.resolve(SORTS, "created_at", "desc")
    stmt = select(Watchlist)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Watchlist.plate_norm.ilike(like.replace(" ", "")), Watchlist.name.ilike(like)))
    if entity_type:
        stmt = stmt.where(Watchlist.entity_type == entity_type)
    if reason:
        stmt = stmt.where(Watchlist.reason == reason)
    if priority:
        stmt = stmt.where(Watchlist.priority == priority)
    if source:
        stmt = stmt.where(Watchlist.source == source)
    if is_active != "all":
        stmt = stmt.where(Watchlist.is_active.is_(is_active.lower() in ("true", "1", "yes")))
    if expired is not None:
        now = utcnow()
        stmt = stmt.where((Watchlist.expires_at.isnot(None)) & (Watchlist.expires_at <= now)) if expired else stmt.where((Watchlist.expires_at.is_(None)) | (Watchlist.expires_at > now))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    if sort == "priority":
        from sqlalchemy import case

        rank = case({p: i for i, p in enumerate(("critical", "high", "medium", "low"))}, value=Watchlist.priority, else_=9)
        stmt = stmt.order_by(rank.desc() if order == "desc" else rank.asc(), Watchlist.created_at.desc())
    else:
        col = SORTS[sort]
        stmt = stmt.order_by(col.desc().nulls_last() if order == "desc" else col.asc().nulls_last(), Watchlist.id.desc())
    rows = (await db.execute(stmt.offset(page.offset).limit(page.page_size))).scalars().all()
    await lookups.usernames(db)
    a24 = await _alerts_24h(db, [w.id for w in rows])
    return {"items": [serializers.watchlist_item(w, a24.get(w.id, 0)) for w in rows], "total": int(total), "page": page.page, "page_size": page.page_size}


@router.get("/watchlist/import/template", dependencies=[Depends(require_permission("watchlist.write"))])
async def template():
    return Response(content=watchlist_template_csv(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="watchlist_template.csv"'})


@router.post("/watchlist", status_code=201, dependencies=[Depends(require_permission("watchlist.write"))])
async def create_entry(body: WatchlistCreate, user: CurrentUser, db: DbDep, request: Request):
    plate_norm = _resolve_plate(body.entity_type, body.plate)
    if body.is_active:
        dup = await _active_duplicate(db, plate_norm)
        if dup is not None:
            raise conflict("An active watchlist entry for this plate already exists", existing_id=dup.id)
    try:
        expires = parse_iso(body.expires_at) if body.expires_at else None
    except ValueError:
        raise validation_error("Invalid expires_at", [{"field": "expires_at", "message": "must be ISO-8601"}])
    w = Watchlist(entity_type=body.entity_type, plate_norm=plate_norm, name=body.name, reason=body.reason, priority=body.priority, source=body.source, notes=body.notes, added_by=user.id, is_active=body.is_active, expires_at=expires, hit_count=0, created_at=utcnow(), updated_at=utcnow())
    db.add(w)
    await db.commit()
    await db.refresh(w)
    await matcher.reload(db)
    await lookups.usernames(db)
    item = serializers.watchlist_item(w)
    set_audit(request, entity="watchlist", entity_id=w.id, after=item)
    return item


@router.put("/watchlist/{entry_id}", dependencies=[Depends(require_permission("watchlist.write"))])
async def update_entry(entry_id: int, body: WatchlistUpdate, user: CurrentUser, db: DbDep, request: Request):
    w = await db.get(Watchlist, entry_id)
    if w is None:
        raise not_found("Watchlist entry not found")
    await lookups.usernames(db)
    before = serializers.watchlist_item(w)
    data = body.model_dump(exclude_unset=True)
    entity_type = data.get("entity_type", w.entity_type)
    if "plate" in data or "entity_type" in data:
        w.plate_norm = _resolve_plate(entity_type, data.get("plate", w.plate_norm))
    w.entity_type = entity_type
    for k in ("name", "reason", "priority", "source", "notes", "is_active"):
        if k in data and data[k] is not None:
            setattr(w, k, data[k])
    if "expires_at" in data:
        try:
            w.expires_at = parse_iso(data["expires_at"]) if data["expires_at"] else None
        except ValueError:
            raise validation_error("Invalid expires_at", [{"field": "expires_at", "message": "must be ISO-8601"}])
    if w.is_active and w.entity_type == "vehicle":
        dup = await _active_duplicate(db, w.plate_norm, exclude_id=w.id)
        if dup is not None:
            raise conflict("An active watchlist entry for this plate already exists", existing_id=dup.id)
    w.updated_at = utcnow()
    await db.commit()
    await db.refresh(w)
    await matcher.reload(db)
    item = serializers.watchlist_item(w)
    set_audit(request, entity="watchlist", entity_id=w.id, before={k: v for k, v in before.items() if item.get(k) != v}, after={k: v for k, v in item.items() if before.get(k) != v})
    return item


@router.delete("/watchlist/{entry_id}", status_code=204, dependencies=[Depends(require_permission("watchlist.write"))])
async def delete_entry(entry_id: int, user: CurrentUser, db: DbDep, request: Request):
    w = await db.get(Watchlist, entry_id)
    if w is None:
        raise not_found("Watchlist entry not found")
    await lookups.usernames(db)
    before = serializers.watchlist_item(w)
    await db.delete(w)
    await db.commit()
    await matcher.reload(db)
    set_audit(request, entity="watchlist", entity_id=entry_id, before=before)
    return Response(status_code=204)


@router.post("/watchlist/import/csv", dependencies=[Depends(require_permission("watchlist.write"))])
async def import_csv(user: CurrentUser, db: DbDep, request: Request, file: UploadFile = File(...), dry_run: str = Form("false")):
    started = time.perf_counter()
    data = await file.read()
    if len(data) > 200 * 1024 * 1024:
        raise ApiError(413, "CSV larger than 200 MB")
    parsed = parse_watchlist_csv(decode_csv_bytes(data))
    if parsed.header_error and not parsed.rows:
        raise validation_error(f"Unusable CSV header: {parsed.header_error}", [{"field": "file", "message": parsed.header_error}])
    is_dry = str(dry_run).strip().lower() in ("true", "1", "yes")
    errors: list[dict[str, Any]] = []
    added = updated = 0
    seen: set[str] = set()
    now = utcnow()
    for row_no, raw in zip(parsed.row_numbers, parsed.rows):
        row_errors: list[dict[str, Any]] = []
        entity_type = (raw.get("entity_type") or "vehicle").strip().lower()
        if entity_type not in ("vehicle", "person"):
            row_errors.append({"row": row_no, "field": "entity_type", "message": "must be vehicle or person"})
        reason = (raw.get("reason") or "").strip().lower()
        if reason not in REASONS:
            row_errors.append({"row": row_no, "field": "reason", "message": f"must be one of {', '.join(REASONS)}"})
        priority = (raw.get("priority") or "medium").strip().lower()
        if priority not in PRIORITIES:
            row_errors.append({"row": row_no, "field": "priority", "message": f"must be one of {', '.join(PRIORITIES)}"})
        source = (raw.get("source") or "import").strip().lower()
        if source not in WATCHLIST_SOURCES:
            row_errors.append({"row": row_no, "field": "source", "message": f"must be one of {', '.join(WATCHLIST_SOURCES)}"})
        plate_norm = None
        if entity_type == "vehicle":
            n = normalise(raw.get("plate") or "")
            if not n.is_valid_format:
                row_errors.append({"row": row_no, "field": "plate", "message": "not a valid Indian registration format"})
            else:
                plate_norm = n.plate_norm
                if plate_norm in seen:
                    row_errors.append({"row": row_no, "field": "plate", "message": "duplicate plate in file"})
                seen.add(plate_norm)
        expires = None
        if raw.get("expires_at"):
            try:
                expires = parse_iso(raw["expires_at"])
            except ValueError:
                row_errors.append({"row": row_no, "field": "expires_at", "message": "must be ISO-8601"})
        try:
            active = parse_bool(raw.get("is_active"))
        except ValueError:
            row_errors.append({"row": row_no, "field": "is_active", "message": "must be true/false"})
            active = None
        if active is None:
            active = True
        if row_errors:
            errors.extend(row_errors)
            continue
        if is_dry:
            added += 1
            continue
        existing = None
        if plate_norm:
            existing = (await db.execute(select(Watchlist).where(Watchlist.entity_type == "vehicle", Watchlist.plate_norm == plate_norm).order_by(Watchlist.is_active.desc(), Watchlist.id.desc()).limit(1))).scalar_one_or_none()
        if existing is not None:
            existing.reason, existing.priority, existing.source = reason, priority, source
            existing.name = raw.get("name") or existing.name
            existing.notes = raw.get("notes") or existing.notes
            existing.expires_at = expires
            existing.is_active = active
            existing.updated_at = now
            updated += 1
        else:
            db.add(Watchlist(entity_type=entity_type, plate_norm=plate_norm, name=raw.get("name") or None, reason=reason, priority=priority, source=source, notes=raw.get("notes") or None, added_by=user.id, is_active=active, expires_at=expires, hit_count=0, created_at=now, updated_at=now))
            added += 1
    if not is_dry:
        await db.commit()
        await matcher.reload(db)
    error_url = None
    if errors:
        rf = await store_report(db, "import_errors_csv", error_report_csv(errors, dict(zip(parsed.row_numbers, parsed.original_lines))).encode("utf-8"), "csv", user.username, user.id, {"file": file.filename, "kind": "watchlist"}, len(errors), subdir="exports", basename=f"import_errors_watchlist_{now.strftime('%Y-%m-%dT%H-%M-%S')}.csv")
        error_url = media_url(rf.path)
    result = {"rows_total": len(parsed.rows), "added": added, "updated": updated, "errors": errors, "error_report_url": error_url, "dry_run": is_dry, "duration_ms": int((time.perf_counter() - started) * 1000)}
    set_audit(request, entity="watchlist_import", after={k: v for k, v in result.items() if k != "errors"} | {"errors": len(errors), "file": file.filename})
    return result
