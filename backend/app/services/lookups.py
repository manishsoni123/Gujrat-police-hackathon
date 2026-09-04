"""Small cached lookups: departments by id/code, usernames by id."""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Department, User

_TTL = 60.0


@dataclass
class DeptInfo:
    id: int
    code: str
    name: str
    aliases: list[str]


class _Cache:
    depts_by_id: dict[int, DeptInfo] = {}
    depts_by_code: dict[str, DeptInfo] = {}
    alias_to_code: dict[str, str] = {}
    users: dict[int, str] = {}
    loaded_at: float = 0.0
    users_loaded_at: float = 0.0


async def refresh_departments(db: AsyncSession) -> None:
    rows = (await db.execute(select(Department))).scalars().all()
    by_id: dict[int, DeptInfo] = {}
    by_code: dict[str, DeptInfo] = {}
    aliases: dict[str, str] = {}
    for d in rows:
        info = DeptInfo(d.id, d.code, d.name, list(d.aliases or []))
        by_id[d.id] = info
        by_code[d.code.upper()] = info
        for a in info.aliases:
            aliases[a.strip().lower()] = d.code
    _Cache.depts_by_id = by_id
    _Cache.depts_by_code = by_code
    _Cache.alias_to_code = aliases
    _Cache.loaded_at = time.monotonic()


async def departments(db: AsyncSession) -> dict[int, DeptInfo]:
    if not _Cache.depts_by_id or time.monotonic() - _Cache.loaded_at > _TTL:
        await refresh_departments(db)
    return _Cache.depts_by_id


async def department_by_code(db: AsyncSession, code: str) -> DeptInfo | None:
    await departments(db)
    return _Cache.depts_by_code.get(code.upper())


async def resolve_department_code(db: AsyncSession, raw: str | None, extra_aliases: dict[str, str] | None = None) -> str | None:
    """Return a department code for a catalogue/CSV string, or None when unknown."""
    if raw is None or str(raw).strip() == "":
        return None
    await departments(db)
    s = str(raw).strip()
    if s.upper() in _Cache.depts_by_code:
        return s.upper()
    low = s.lower()
    if extra_aliases and low in extra_aliases:
        code = extra_aliases[low].upper()
        if code in _Cache.depts_by_code:
            return code
    if low in _Cache.alias_to_code:
        return _Cache.alias_to_code[low]
    # loose match: alias contained in the string (e.g. "Gujarat Police HQ")
    for alias, code in sorted(_Cache.alias_to_code.items(), key=lambda kv: -len(kv[0])):
        if len(alias) >= 4 and alias in low:
            return code
    return None


def dept_sync(dept_id: int | None) -> DeptInfo | None:
    return _Cache.depts_by_id.get(dept_id) if dept_id is not None else None


async def unassigned_id(db: AsyncSession) -> int:
    d = await department_by_code(db, "UNASSIGNED")
    if d is None:
        raise RuntimeError("UNASSIGNED department missing – run the seed")
    return d.id


async def usernames(db: AsyncSession) -> dict[int, str]:
    if not _Cache.users or time.monotonic() - _Cache.users_loaded_at > _TTL:
        rows = (await db.execute(select(User.id, User.username))).all()
        _Cache.users = {r[0]: r[1] for r in rows}
        _Cache.users_loaded_at = time.monotonic()
    return _Cache.users


def username_sync(user_id: int | None) -> str | None:
    return _Cache.users.get(user_id) if user_id is not None else None


def invalidate_users() -> None:
    _Cache.users_loaded_at = 0.0
