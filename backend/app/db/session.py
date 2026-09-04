"""Async engine/session plus startup DDL (extensions, create_all, audit trigger)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import POST_CREATE_SQL, Base

log = logging.getLogger("sentinel.db")

engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


EXTENSIONS = ("postgis", "pg_trgm", "fuzzystrmatch")


async def init_db() -> None:
    """CREATE EXTENSION …; create_all; post-create SQL. Idempotent."""
    async with engine.begin() as conn:
        for ext in EXTENSIONS:
            await conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {ext}"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.begin() as conn:
        for stmt in POST_CREATE_SQL:
            await conn.execute(text(stmt))
    log.info("database schema ready")


async def db_ping() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


async def dispose() -> None:
    await engine.dispose()
