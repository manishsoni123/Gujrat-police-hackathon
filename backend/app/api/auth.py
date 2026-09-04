"""Auth endpoints (CONTRACT §2.1, §2.5, §5.7)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from app.api.deps import CurrentUser, DbDep, extract_token, remember_actor, user_from_token, user_scope
from app.core.config import settings
from app.core.errors import ApiError, conflict, not_found, unauthorized, validation_error
from app.core.ratelimit import RateLimiter
from app.core.rbac import permissions_for
from app.core.security import create_access_token, hash_password, password_policy_ok, verify_password
from app.core.tz import iso_z, utcnow
from app.db.models import Camera, User
from app.schemas.auth import ChangePasswordRequest, LoginRequest, WallLayout
from app.services import lookups, serializers
from app.services.audit import client_ip, set_audit
from app.services.media_auth import camera_allowed, token_from_uri

router = APIRouter(tags=["auth"])
_limiter = RateLimiter(settings.LOGIN_RATE_LIMIT_PER_MIN)

COOKIE = "sg_session"
# Unknown usernames are verified against this hash so a login attempt costs the same bcrypt work
# whether or not the account exists (no username enumeration by response time).
_DUMMY_HASH = hash_password("sentinel-timing-equaliser-not-a-real-password")


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE, token, max_age=settings.JWT_EXPIRE_HOURS * 3600, path="/", httponly=True,
        samesite="lax", secure=settings.COOKIE_SECURE,
    )


@router.post("/auth/login")
async def login(body: LoginRequest, request: Request, response: Response, db: DbDep):
    ip = client_ip(request) or "unknown"
    if not _limiter.allow(ip):
        set_audit(request, action="auth.login_failed", actor=body.username.lower(), after={"reason": "rate_limited"})
        raise ApiError(429, "Too many login attempts; try again in a minute")
    username = body.username.strip().lower()
    user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if user is None or not user.is_active:
        verify_password(body.password, _DUMMY_HASH)
        ok = False
    else:
        ok = verify_password(body.password, user.password_hash)
    if not ok:
        set_audit(request, action="auth.login_failed", actor=username, after={"username": username})
        raise ApiError(401, "Invalid username or password")
    token, exp = create_access_token(user)
    user.last_login_at = utcnow()
    await db.commit()
    _limiter.reset(ip)
    await lookups.departments(db)
    remember_actor(request, user)
    set_audit(request, action="auth.login", entity="user", entity_id=user.id)
    _set_cookie(response, token)
    return {"access_token": token, "token_type": "bearer", "expires_at": iso_z(exp), "user": serializers.user_brief(user)}


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response, db: DbDep):
    user = await user_from_token(db, extract_token(request))
    if user is not None:
        remember_actor(request, user)
    set_audit(request, action="auth.logout")
    resp = Response(status_code=204)
    resp.delete_cookie(COOKIE, path="/")  # Max-Age=0; the injected `response` is discarded when a Response is returned
    return resp


@router.get("/auth/me")
async def me(user: CurrentUser, db: DbDep):
    await lookups.departments(db)
    return {**serializers.user_brief(user), "permissions": permissions_for(user.role)}


@router.post("/auth/change-password", status_code=204)
async def change_password(body: ChangePasswordRequest, user: CurrentUser, db: DbDep, request: Request):
    if not verify_password(body.current_password, user.password_hash):
        raise unauthorized("Current password is incorrect")
    if not password_policy_ok(body.new_password):
        raise validation_error("Weak password", [{"field": "new_password", "message": "at least 10 characters with a letter and a digit"}])
    user.password_hash = hash_password(body.new_password)
    user.updated_at = utcnow()
    await db.commit()
    set_audit(request, action="user.change_password", entity="user", entity_id=user.id)
    return Response(status_code=204)


@router.get("/auth/verify", include_in_schema=False)
async def verify(request: Request, db: DbDep):
    """Caddy forward_auth target for /mtx/* and /playback/* (not audited).

    Authenticates (header, cookie, or `?token=` of the forwarded URI) **and** authorises: a
    `dept_admin` may only reach `cam_<id>` paths of cameras in their scope (`404` otherwise,
    like the REST API); statewide roles may reach every path.
    """
    set_audit(request, skip=True)
    forwarded_uri = request.headers.get("x-forwarded-uri") or ""
    user = await user_from_token(db, extract_token(request) or token_from_uri(forwarded_uri))
    if user is None:
        raise unauthorized()
    if not await camera_allowed(db, user.id, user_scope(user), forwarded_uri):
        raise not_found("Camera not found")
    return Response(status_code=204, headers={"X-Sentinel-User": user.username, "X-Sentinel-Role": user.role})


@router.get("/me/wall-layout")
async def get_wall_layout(user: CurrentUser, db: DbDep):
    if user.wall_layout:
        return user.wall_layout
    from app.services.scope import camera_conditions

    q = select(Camera.id).where(Camera.status != "retired", Camera.rtsp_url.isnot(None), *camera_conditions(user_scope(user))).order_by(Camera.anpr_enabled.desc(), Camera.id.asc()).limit(4)
    ids = [r[0] for r in (await db.execute(q)).all()]
    return {"grid": 4, "tiles": [{"slot": i, "camera_id": cid} for i, cid in enumerate(ids)]}


@router.put("/me/wall-layout")
async def put_wall_layout(body: WallLayout, user: CurrentUser, db: DbDep, request: Request):
    layout = body.model_dump()
    user.wall_layout = layout
    user.updated_at = utcnow()
    await db.commit()
    set_audit(request, action="user.wall_layout", entity="user", entity_id=user.id, after=layout)
    return layout
