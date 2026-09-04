"""User administration (CONTRACT §5.20) – admin.users."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, or_, select

from app.api.deps import DbDep, require_permission
from app.core.errors import conflict, not_found, validation_error
from app.core.security import hash_password, password_policy_ok
from app.core.tz import utcnow
from app.db.models import Department, User
from app.schemas.auth import ResetPasswordRequest, UserCreate, UserUpdate
from app.schemas.common import PageParams
from app.services import lookups, serializers
from app.services.audit import set_audit
from app.services.districts import canonical_district

router = APIRouter(tags=["users"], dependencies=[Depends(require_permission("admin.users"))])

SORTS = {"username": User.username, "full_name": User.full_name, "role": User.role, "created_at": User.created_at, "last_login_at": User.last_login_at}


async def _check_department(db, department_id: int | None) -> None:
    if department_id is not None and await db.get(Department, department_id) is None:
        raise validation_error("Unknown department", [{"field": "department_id", "message": "department does not exist"}])


@router.get("/users")
async def list_users(db: DbDep, page: PageParams = Depends(), q: str | None = None, role: str | None = None, is_active: bool | None = None):
    sort, order = page.resolve(SORTS, "username", "asc")
    stmt = select(User)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(User.username.ilike(like), User.full_name.ilike(like)))
    if role:
        stmt = stmt.where(User.role == role)
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    col = SORTS[sort]
    stmt = stmt.order_by(col.desc() if order == "desc" else col.asc()).offset(page.offset).limit(page.page_size)
    rows = (await db.execute(stmt)).scalars().all()
    await lookups.departments(db)
    return {"items": [serializers.user_item(u) for u in rows], "total": int(total), "page": page.page, "page_size": page.page_size}


@router.post("/users", status_code=201)
async def create_user(body: UserCreate, db: DbDep, request: Request):
    username = body.username.lower()
    if (await db.execute(select(User).where(User.username == username))).scalar_one_or_none():
        raise conflict("Username already exists")
    if not password_policy_ok(body.password):
        raise validation_error("Weak password", [{"field": "password", "message": "at least 10 characters with a letter and a digit"}])
    await _check_department(db, body.department_id)
    u = User(
        username=username, password_hash=hash_password(body.password), full_name=body.full_name, role=body.role,
        department_id=body.department_id, district=canonical_district(body.district) if body.district else None,
        is_active=True, created_at=utcnow(), updated_at=utcnow(),
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    lookups.invalidate_users()
    await lookups.usernames(db)
    await lookups.departments(db)
    set_audit(request, entity="user", entity_id=u.id, after=serializers.user_item(u))
    return serializers.user_item(u)


@router.put("/users/{user_id}")
async def update_user(user_id: int, body: UserUpdate, db: DbDep, request: Request):
    u = await db.get(User, user_id)
    if u is None:
        raise not_found("User not found")
    before = serializers.user_item(u)
    data = body.model_dump(exclude_unset=True)
    if "department_id" in data:
        await _check_department(db, data["department_id"])
    if "district" in data and data["district"]:
        data["district"] = canonical_district(data["district"])
    for k, v in data.items():
        setattr(u, k, v)
    u.updated_at = utcnow()
    await db.commit()
    await db.refresh(u)
    lookups.invalidate_users()
    await lookups.usernames(db)
    set_audit(request, entity="user", entity_id=u.id, before=before, after=serializers.user_item(u))
    return serializers.user_item(u)


@router.post("/users/{user_id}/reset-password", status_code=204)
async def reset_password(user_id: int, body: ResetPasswordRequest, db: DbDep, request: Request):
    u = await db.get(User, user_id)
    if u is None:
        raise not_found("User not found")
    if not password_policy_ok(body.password):
        raise validation_error("Weak password", [{"field": "password", "message": "at least 10 characters with a letter and a digit"}])
    u.password_hash = hash_password(body.password)
    u.updated_at = utcnow()
    await db.commit()
    set_audit(request, entity="user", entity_id=u.id)
    return Response(status_code=204)


@router.delete("/users/{user_id}", status_code=204)
async def deactivate_user(user_id: int, db: DbDep, request: Request):
    me = request.state.user
    if me.id == user_id:
        raise conflict("You cannot deactivate your own account")
    u = await db.get(User, user_id)
    if u is None:
        raise not_found("User not found")
    before = serializers.user_item(u)
    u.is_active = False
    u.updated_at = utcnow()
    await db.commit()
    set_audit(request, entity="user", entity_id=u.id, before=before, after={"is_active": False})
    return Response(status_code=204)
