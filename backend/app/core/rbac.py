"""Roles, permission matrix and department scoping (CONTRACT §2.3, §2.4).

The pure parts (`PERMISSIONS`, `has_permission`, `Scope`, `scope_for`) have no DB or
FastAPI dependency so `tests/test_rbac.py` can exercise them directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ROLES = ("admin", "dept_admin", "operator", "viewer")

PERMISSIONS: dict[str, tuple[str, ...]] = {
    "cameras.read": ("admin", "dept_admin", "operator", "viewer"),
    "cameras.write": ("admin", "dept_admin"),
    "cameras.export": ("admin", "dept_admin", "operator"),
    "analytics.read": ("admin", "dept_admin", "operator", "viewer"),
    "watchlist.write": ("admin", "dept_admin", "operator"),
    "alerts.ack": ("admin", "dept_admin", "operator"),
    "route.confirm": ("admin", "dept_admin", "operator"),
    "events.write": ("admin", "dept_admin", "operator"),
    "reports.export": ("admin", "dept_admin", "operator"),
    "external.lookup": ("admin", "dept_admin", "operator"),
    "zones.write": ("admin", "dept_admin"),
    "admin.users": ("admin",),
    "admin.audit": ("admin",),
    "admin.apikeys": ("admin",),
    "admin.settings": ("admin",),
    "settings.read_public": ("admin", "dept_admin", "operator", "viewer"),
}


def has_permission(role: str, permission: str) -> bool:
    return role in PERMISSIONS.get(permission, ())


def permissions_for(role: str) -> list[str]:
    return [p for p, roles in PERMISSIONS.items() if role in roles]


@dataclass(frozen=True)
class Scope:
    """Camera visibility scope. `unrestricted` = statewide."""

    department_id: int | None = None
    district: str | None = None

    @property
    def unrestricted(self) -> bool:
        return self.department_id is None

    def allows(self, camera_department_id: int | None, camera_district: str | None) -> bool:
        if self.unrestricted:
            return True
        if camera_department_id != self.department_id:
            return False
        if self.district is not None and camera_district != self.district:
            return False
        return True

    def as_dict(self) -> dict[str, Any]:
        return {"department_id": self.department_id, "district": self.district}


def scope_for(role: str, department_id: int | None, district: str | None) -> Scope:
    """Only `dept_admin` is scoped; every other role is statewide (§2.3)."""
    if role == "dept_admin" and department_id is not None:
        return Scope(department_id=department_id, district=district)
    return Scope()


def can_change_department(role: str) -> bool:
    return role == "admin"
