"""Startup guard against the repository's default secrets (config.py)."""

from __future__ import annotations

import pytest

from app.core.config import DEFAULT_JWT_SECRET, Settings, default_secret_problems

STRONG = "a" * 40
SAFE_DB = "postgresql+asyncpg://sentinel:s3cr3t-value@postgres:5432/sentinel"
DEFAULT_DB = "postgresql+asyncpg://sentinel:sentinel@postgres:5432/sentinel"


def test_default_secret_problems_detects_every_default() -> None:
    problems = default_secret_problems(DEFAULT_JWT_SECRET, DEFAULT_DB)
    assert any("JWT_SECRET" in p for p in problems)
    assert any("POSTGRES_PASSWORD" in p for p in problems)
    assert default_secret_problems("short", SAFE_DB) == ["JWT_SECRET is shorter than 32 characters"]
    assert default_secret_problems(STRONG, SAFE_DB) == []


def test_laptop_http_deployment_only_warns() -> None:
    # plain HTTP + insecure cookie = local demo: defaults are tolerated (logged), never fatal
    s = Settings(JWT_SECRET=DEFAULT_JWT_SECRET, DATABASE_URL=DEFAULT_DB, PUBLIC_BASE_URL="http://localhost", COOKIE_SECURE=False)
    assert s.JWT_SECRET == DEFAULT_JWT_SECRET


@pytest.mark.parametrize("kw", [{"PUBLIC_BASE_URL": "https://sentinel.example.in"}, {"COOKIE_SECURE": True}])
def test_public_deployment_with_default_jwt_secret_refuses_to_start(kw: dict) -> None:
    with pytest.raises(SystemExit):
        Settings(JWT_SECRET=DEFAULT_JWT_SECRET, DATABASE_URL=SAFE_DB, **kw)


def test_public_deployment_with_default_db_password_refuses_to_start() -> None:
    with pytest.raises(SystemExit):
        Settings(JWT_SECRET=STRONG, DATABASE_URL=DEFAULT_DB, PUBLIC_BASE_URL="https://sentinel.example.in")


def test_public_deployment_with_strong_secrets_starts() -> None:
    s = Settings(JWT_SECRET=STRONG, DATABASE_URL=SAFE_DB, PUBLIC_BASE_URL="https://sentinel.example.in", COOKIE_SECURE=True)
    assert s.COOKIE_SECURE is True
