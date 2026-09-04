"""API key administration (CONTRACT §2.6, §5.20)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from app.api.deps import DbDep, require_permission
from app.core.errors import conflict, not_found
from app.core.security import api_key_prefix, generate_api_key, hash_api_key
from app.core.tz import iso_z, utcnow
from app.db.models import ApiKey
from app.schemas.auth import ApiKeyCreate
from app.services import lookups, serializers
from app.services.audit import set_audit

router = APIRouter(tags=["api-keys"], dependencies=[Depends(require_permission("admin.apikeys"))])


@router.get("/api-keys")
async def list_keys(db: DbDep):
    rows = (await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))).scalars().all()
    await lookups.usernames(db)
    return {"items": [serializers.api_key_item(k) for k in rows]}


@router.post("/api-keys", status_code=201)
async def create_key(body: ApiKeyCreate, db: DbDep, request: Request):
    if (await db.execute(select(ApiKey).where(ApiKey.name == body.name))).scalar_one_or_none():
        raise conflict("An API key with this name already exists")
    key = generate_api_key()
    row = ApiKey(name=body.name, key_hash=hash_api_key(key), key_prefix=api_key_prefix(key), scope=body.scope, created_by=request.state.user.id, is_active=True, created_at=utcnow())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    set_audit(request, entity="api_key", entity_id=row.id, after={"name": row.name, "scope": row.scope, "key_prefix": row.key_prefix})
    return {"id": row.id, "name": row.name, "scope": row.scope, "key": key, "key_prefix": row.key_prefix, "created_at": iso_z(row.created_at)}


@router.delete("/api-keys/{key_id}", status_code=204)
async def delete_key(key_id: int, db: DbDep, request: Request):
    row = await db.get(ApiKey, key_id)
    if row is None:
        raise not_found("API key not found")
    row.is_active = False
    await db.commit()
    set_audit(request, entity="api_key", entity_id=row.id, after={"name": row.name, "is_active": False})
    return Response(status_code=204)
