"""Audit log listing and CSV export (CONTRACT §5.20) – admin.audit."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import cast, func, or_, select
from sqlalchemy import String as SAString

from app.api.deps import CurrentUser, DbDep, require_permission
from app.core.config import settings
from app.core.errors import validation_error
from app.core.tz import fmt_ist, ist_stamp_short, utcnow
from app.db.models import AuditLog, User
from app.schemas.common import PageParams, parse_window
from app.services import lookups, serializers
from app.services.audit import set_audit
from app.services.report_builder import build_csv, rows_hash, store_report

router = APIRouter(tags=["audit"], dependencies=[Depends(require_permission("admin.audit"))])

SORTS: dict[str, Any] = {"ts": AuditLog.ts}
EXPORT_MAX = 100_000


async def _filtered(db, user_f: str | None, action: str | None, entity: str | None, entity_id: str | None, from_: str | None, to: str | None, q: str | None):
    t_from, t_to = parse_window(from_, to, 24 * 7)
    stmt = select(AuditLog).where(AuditLog.ts >= t_from, AuditLog.ts <= t_to)
    if user_f:
        if user_f.isdigit():
            stmt = stmt.where(AuditLog.user_id == int(user_f))
        else:
            uid = (await db.execute(select(User.id).where(User.username == user_f.lower()))).scalar_one_or_none()
            stmt = stmt.where(or_(AuditLog.actor == user_f.lower(), AuditLog.user_id == uid) if uid is not None else AuditLog.actor == user_f.lower())
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(AuditLog.entity_id.ilike(like), AuditLog.action.ilike(like), AuditLog.actor.ilike(like), cast(AuditLog.after, SAString).ilike(like)))
    return stmt, t_from, t_to


@router.get("/audit")
async def list_audit(
    db: DbDep, page: PageParams = Depends(), user: str | None = None, action: str | None = None, entity: str | None = None,
    entity_id: str | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None, q: str | None = None,
):
    sort, order = page.resolve(SORTS, "ts", "desc", max_size=500)
    stmt, _f, _t = await _filtered(db, user, action, entity, entity_id, from_, to, q)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await db.execute(stmt.order_by(AuditLog.ts.desc() if order == "desc" else AuditLog.ts.asc(), AuditLog.id.desc()).offset(page.offset).limit(page.page_size))).scalars().all()
    await lookups.usernames(db)
    return {"items": [serializers.audit_item(a) for a in rows], "total": int(total), "page": page.page, "page_size": page.page_size}


@router.get("/audit/export")
async def export_audit(
    me: CurrentUser, db: DbDep, request: Request, format: str = Query("csv"), user: str | None = None, action: str | None = None, entity: str | None = None,
    entity_id: str | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None, q: str | None = None,
):
    if format != "csv":
        raise validation_error("Unsupported format", [{"field": "format", "message": "only csv is supported"}])
    stmt, t_from, t_to = await _filtered(db, user, action, entity, entity_id, from_, to, q)
    rows_db = (await db.execute(stmt.order_by(AuditLog.ts.asc(), AuditLog.id.asc()).limit(EXPORT_MAX))).scalars().all()
    await lookups.usernames(db)
    header = ["id", "ts_ist", "ts_utc", "actor", "role", "action", "entity", "entity_id", "ip", "user_agent", "before", "after", "request_id"]
    rows = []
    for a in rows_db:
        it = serializers.audit_item(a)
        rows.append([a.id, fmt_ist(a.ts), it["ts"], a.actor, a.role, a.action, a.entity, a.entity_id, it["ip"], a.user_agent, json.dumps(a.before, ensure_ascii=False) if a.before is not None else "", json.dumps(a.after, ensure_ascii=False) if a.after is not None else "", it["request_id"]])
    filters = {k: v for k, v in {"user": user, "action": action, "entity": entity, "entity_id": entity_id, "from": t_from.isoformat(), "to": t_to.isoformat(), "q": q}.items() if v}
    trailer = f"# {settings.PRODUCT_NAME} {settings.APP_VERSION} | rows={len(rows)} | generated {fmt_ist(utcnow())} by {me.username} | filters={json.dumps(filters)} | sha256(rows)={rows_hash(rows)}"
    content = build_csv(header, rows, trailer)
    rf = await store_report(db, "audit_csv", content, "csv", me.username, me.id, filters, len(rows), subdir="exports", basename=f"audit_export_{ist_stamp_short()}IST_{me.username}.csv")
    set_audit(request, action="report.download", entity="report_file", entity_id=rf.id, after={"kind": "audit_csv", "rows": len(rows), "sha256": rf.sha256, "path": rf.path})
    return Response(content=content, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="audit_export_{ist_stamp_short()}IST.csv"', "X-Sentinel-Sha256": rf.sha256, "X-Sentinel-Report-Id": str(rf.id)})
