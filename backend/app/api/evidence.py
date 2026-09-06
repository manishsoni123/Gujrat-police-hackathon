"""Evidence verification (S7, CONTRACT §5.18): recompute a stored file's SHA-256 and compare."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from app.api.deps import CurrentUser, DbDep, require_permission, user_scope
from app.core.errors import not_found, validation_error
from app.core.hashing import abs_path, safe_relative, sha256_file
from app.core.tz import iso_z, utcnow
from app.db.models import Alert, Clip, Event, PlateRead, ReportFile, Sighting
from app.services.audit import set_audit
from app.services.scope import in_scope_condition

router = APIRouter(tags=["evidence"], dependencies=[Depends(require_permission("analytics.read"))])


async def _lookup(db, scope, rel: str) -> tuple[str | None, int | None, str | None]:
    """First table whose path column carries `rel` → (entity, entity_id, stored_sha256)."""
    probes = (
        ("plate_read", PlateRead, PlateRead.crop_path, PlateRead.crop_sha256, PlateRead.camera_id),
        ("sighting", Sighting, Sighting.frame_path, Sighting.frame_sha256, Sighting.camera_id),
        ("alert", Alert, Alert.snapshot_path, Alert.snapshot_sha256, Alert.camera_id),
        ("event", Event, Event.frame_path, Event.frame_sha256, Event.camera_id),
        ("clip", Clip, Clip.path, Clip.sha256, Clip.camera_id),
        ("report_file", ReportFile, ReportFile.path, ReportFile.sha256, None),
    )
    for name, model, path_col, sha_col, cam_col in probes:
        q = select(model.id, sha_col).where(path_col == rel)
        if cam_col is not None:
            cond = in_scope_condition(scope, cam_col)
            if cond is not None:
                q = q.where(cond)
        row = (await db.execute(q.limit(1))).first()
        if row is not None:
            return name, int(row[0]), row[1]
    return None, None, None


@router.get("/evidence/verify")
async def verify(user: CurrentUser, db: DbDep, request: Request, path: str = Query(..., min_length=1), any_file: bool = Query(False, alias="any", description="admin only: verify a file that is not in the evidence ledger")):
    rel = safe_relative(path)
    if rel is None:
        raise validation_error("Invalid path", [{"field": "path", "message": "must be a relative path under the data directory"}])
    entity, entity_id, stored = await _lookup(db, user_scope(user), rel)
    if entity is None and not (any_file and user.role == "admin"):
        # same answer as /media for a path outside the caller's scope or not in the ledger: no existence/size/hash oracle
        set_audit(request, action="evidence.verify", entity="file", entity_id=rel[:64], after={"path": rel, "found": False})
        raise not_found("Not found")
    p = abs_path(rel)
    exists = p.is_file()
    computed = sha256_file(p) if exists else None
    size = p.stat().st_size if exists else None
    match = (computed == stored) if (exists and stored) else None
    result = {
        "path": rel, "exists": exists, "entity": entity, "entity_id": entity_id, "stored_sha256": stored,
        "computed_sha256": computed, "match": match, "size_bytes": size, "checked_at": iso_z(utcnow()),
    }
    set_audit(request, action="evidence.verify", entity=entity or "file", entity_id=entity_id if entity_id is not None else rel[:64], after={"path": rel, "match": match, "exists": exists})
    return result
