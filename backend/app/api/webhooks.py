"""Outbound webhook administration (CONTRACT §5.20) – admin.settings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from app.api.deps import CurrentUser, DbDep, require_permission
from app.core.errors import not_found
from app.core.tz import iso_z, utcnow
from app.db.models import Webhook
from app.schemas.admin import WebhookCreate, WebhookUpdate
from app.services import lookups, serializers
from app.services.audit import set_audit
from app.services.notifications import deliver

router = APIRouter(tags=["webhooks"], dependencies=[Depends(require_permission("admin.settings"))])


async def _get(db, webhook_id: int) -> Webhook:
    w = await db.get(Webhook, webhook_id)
    if w is None:
        raise not_found("Webhook not found")
    return w


@router.get("/webhooks")
async def list_webhooks(db: DbDep):
    rows = (await db.execute(select(Webhook).order_by(Webhook.id))).scalars().all()
    await lookups.usernames(db)
    return {"items": [serializers.webhook_item(w) for w in rows], "total": len(rows)}


@router.post("/webhooks", status_code=201)
async def create_webhook(body: WebhookCreate, user: CurrentUser, db: DbDep, request: Request):
    w = Webhook(name=body.name, url=body.url, secret=body.secret or None, event_types=body.event_types, is_active=body.is_active, created_by=user.id, created_at=utcnow())
    db.add(w)
    await db.commit()
    await db.refresh(w)
    await lookups.usernames(db)
    item = serializers.webhook_item(w)
    set_audit(request, entity="webhook", entity_id=w.id, after=item)
    return item


@router.put("/webhooks/{webhook_id}")
async def update_webhook(webhook_id: int, body: WebhookUpdate, db: DbDep, request: Request):
    w = await _get(db, webhook_id)
    await lookups.usernames(db)
    before = serializers.webhook_item(w)
    data = body.model_dump(exclude_unset=True)
    if "secret" in data and data["secret"] == "********":
        data.pop("secret")
    for k, v in data.items():
        setattr(w, k, v if k != "secret" else (v or None))
    await db.commit()
    await db.refresh(w)
    item = serializers.webhook_item(w)
    set_audit(request, entity="webhook", entity_id=w.id, before=before, after=item)
    return item


@router.delete("/webhooks/{webhook_id}", status_code=204)
async def delete_webhook(webhook_id: int, db: DbDep, request: Request):
    w = await _get(db, webhook_id)
    await lookups.usernames(db)
    before = serializers.webhook_item(w)
    await db.delete(w)
    await db.commit()
    set_audit(request, entity="webhook", entity_id=webhook_id, before=before)
    return Response(status_code=204)


@router.post("/webhooks/{webhook_id}/test")
async def test_webhook(webhook_id: int, user: CurrentUser, db: DbDep, request: Request):
    w = await _get(db, webhook_id)
    status, duration_ms, error = await deliver(w.id, "ping", {"message": "Sentinel Gujarat webhook test", "webhook_id": w.id, "by": user.username, "at": iso_z(utcnow())}, attempts=1)
    set_audit(request, action="webhook.test", entity="webhook", entity_id=w.id, after={"status": status, "error": error})
    return {"status": status, "duration_ms": duration_ms, "error": error}
