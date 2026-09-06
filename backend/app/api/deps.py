"""FastAPI dependencies: current user (JWT), permissions, API keys, scope (CONTRACT §2)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, WebSocket
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import forbidden, unauthorized
from app.core.rbac import Scope, has_permission, scope_for
from app.core.security import decode_token, hash_api_key, token_issued_after_reset, valid_api_key_format
from app.core.tz import utcnow
from app.db.models import ApiKey, User
from app.db.session import SessionLocal, get_db

bearer_scheme = HTTPBearer(auto_error=False, scheme_name="bearerAuth", description="JWT from POST /api/auth/login")
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False, scheme_name="apiKeyAuth", description="API key (scope bulk or internal)")

DbDep = Annotated[AsyncSession, Depends(get_db)]


QUERY_TOKEN_PREFIXES = ("/ws/", "/media/")


def query_token_allowed(path: str) -> bool:
    """`?token=` is honoured on WebSocket and download/media links only (CONTRACT §2.1): on REST
    routes it would land in Caddy's access log with every pasted link. `/api/auth/verify` reads the
    forwarded URI's token itself (`media_auth.token_from_uri`)."""
    return path.startswith(QUERY_TOKEN_PREFIXES)


def extract_token(request: Request | WebSocket) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    cookie = request.cookies.get("sg_session")
    if cookie:
        return cookie
    token = request.query_params.get("token")
    if token and query_token_allowed(request.url.path):
        return token
    return None


async def user_from_token(db: AsyncSession, token: str | None) -> User | None:
    if not token:
        return None
    claims = decode_token(token)
    if not claims or "sub" not in claims:
        return None
    try:
        user_id = int(claims["sub"])
    except (TypeError, ValueError):
        return None
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    if not token_issued_after_reset(claims, user.token_not_before):
        return None  # revoked by a password reset/change, deactivation or role change
    return user


def remember_actor(request: Request, user: User) -> None:
    """Store the authenticated user on the request: the ORM row plus plain copies of the audit
    fields, so the audit middleware never depends on the ORM session state after the handler."""
    request.state.user = user
    request.state.actor = {"id": user.id, "username": user.username, "role": user.role}


async def get_current_user(
    request: Request,
    db: DbDep,
    _creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User:
    user = await user_from_token(db, extract_token(request))
    if user is None:
        raise unauthorized("Missing or invalid credentials")
    remember_actor(request, user)
    return user


async def get_optional_user(request: Request, db: DbDep) -> User | None:
    user = await user_from_token(db, extract_token(request))
    if user is not None:
        remember_actor(request, user)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def user_scope(user: User) -> Scope:
    return scope_for(user.role, user.department_id, user.district)


def require_permission(permission: str):
    async def dep(user: CurrentUser) -> User:
        if not has_permission(user.role, permission):
            raise forbidden()
        return user

    return dep


def require_role(*roles: str):
    async def dep(user: CurrentUser) -> User:
        if user.role not in roles:
            raise forbidden()
        return user

    return dep


def require_api_key(scope: str):
    async def dep(request: Request, db: DbDep, key: str | None = Depends(api_key_scheme)) -> ApiKey:
        if not key or not valid_api_key_format(key):
            raise unauthorized("Missing or invalid API key")
        row = (await db.execute(select(ApiKey).where(ApiKey.key_hash == hash_api_key(key)))).scalar_one_or_none()
        if row is None or not row.is_active:
            raise unauthorized("Missing or invalid API key")
        if row.scope != scope:
            raise forbidden("API key scope does not allow this endpoint")
        row.last_used_at = utcnow()
        await db.commit()
        request.state.api_key = row
        return row

    return dep


async def ws_user(ws: WebSocket) -> User | None:
    async with SessionLocal() as db:
        return await user_from_token(db, extract_token(ws))
