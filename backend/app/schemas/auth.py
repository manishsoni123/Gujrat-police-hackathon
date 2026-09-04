"""Auth / user request bodies (CONTRACT §2, §5.20)."""

from __future__ import annotations

from pydantic import Field, field_validator

from app.db.models import ROLES
from app.schemas.common import ApiModel


class LoginRequest(ApiModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(ApiModel):
    current_password: str
    new_password: str = Field(min_length=1, max_length=256)


class UserCreate(ApiModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=1, max_length=256)
    full_name: str = Field(min_length=1, max_length=120)
    role: str = "viewer"
    department_id: int | None = None
    district: str | None = None

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        if v not in ROLES:
            raise ValueError(f"must be one of {', '.join(ROLES)}")
        return v


class UserUpdate(ApiModel):
    full_name: str | None = Field(None, min_length=1, max_length=120)
    role: str | None = None
    department_id: int | None = None
    district: str | None = None
    is_active: bool | None = None

    @field_validator("role")
    @classmethod
    def _role(cls, v: str | None) -> str | None:
        if v is not None and v not in ROLES:
            raise ValueError(f"must be one of {', '.join(ROLES)}")
        return v


class ResetPasswordRequest(ApiModel):
    password: str = Field(min_length=1, max_length=256)


class WallTile(ApiModel):
    slot: int = Field(ge=0, le=15)
    camera_id: int | None = None


class WallLayout(ApiModel):
    grid: int = Field(4, ge=1, le=16)
    tiles: list[WallTile] = Field(default_factory=list)

    @field_validator("grid")
    @classmethod
    def _grid(cls, v: int) -> int:
        if v not in (1, 4, 9, 16):
            raise ValueError("grid must be 1, 4, 9 or 16")
        return v


class ApiKeyCreate(ApiModel):
    name: str = Field(min_length=1, max_length=64)
    scope: str

    @field_validator("scope")
    @classmethod
    def _scope(cls, v: str) -> str:
        if v not in ("bulk", "internal"):
            raise ValueError("must be 'bulk' or 'internal'")
        return v
