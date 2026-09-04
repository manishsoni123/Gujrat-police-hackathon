"""Apply a `Scope` to SQL queries on cameras and camera-derived tables (CONTRACT §2.3)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.core.rbac import Scope
from app.db.models import Camera


def camera_conditions(scope: Scope, cam=Camera) -> list[ColumnElement]:
    if scope.unrestricted:
        return []
    conds = [cam.department_id == scope.department_id]
    if scope.district is not None:
        conds.append(cam.district == scope.district)
    return conds


def scoped_camera_ids_subquery(scope: Scope):
    """Subquery of camera ids in scope (for filtering derived tables)."""
    q = select(Camera.id)
    for c in camera_conditions(scope):
        q = q.where(c)
    return q


def in_scope_condition(scope: Scope, camera_id_col):
    """`camera_id IN (scoped ids)` – no-op (None) for an unrestricted scope."""
    if scope.unrestricted:
        return None
    return camera_id_col.in_(scoped_camera_ids_subquery(scope))


async def scoped_camera(db: AsyncSession, scope: Scope, camera_id: int, include_retired: bool = True) -> Camera | None:
    cam = await db.get(Camera, camera_id)
    if cam is None:
        return None
    if not scope.allows(cam.department_id, cam.district):
        return None
    if not include_retired and cam.status == "retired":
        return None
    return cam
