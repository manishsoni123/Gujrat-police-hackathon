"""Sentinel Gujarat API – application factory (CONTRACT §1, §5, §9, §10).

Single uvicorn worker: REST + WebSocket fan-out + APScheduler (health poller, WS stats,
retention purge, matcher refresh) + the in-process watchlist matcher.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import (
    alerts,
    api_keys,
    audit,
    auth,
    cameras,
    clips,
    dashboard,
    detections,
    events,
    evidence,
    external,
    gap,
    geo,
    health,
    healthz,
    imports,
    internal,
    media,
    mock_sandbox,
    object_counts,
    qa,
    recordings,
    reports,
    settings as settings_api,
    streams,
    users,
    vehicles,
    watchlist,
    webhooks,
    ws,
    zones,
)
from app.core.audit_middleware import AuditMiddleware
from app.core.config import settings
from app.core.errors import install_exception_handlers
from app.core.hashing import ensure_layout
from app.core.logging import setup_logging
from app.db.session import SessionLocal, dispose, init_db
from app.services import settings_service as cfg
from app.services.health_poller import ensure_all_relay_paths, poller
from app.services.matcher import matcher
from app.services.retention_job import run_retention
from app.services.ws_stats import broadcast_stats

log = logging.getLogger("sentinel.main")

DESCRIPTION = """
**Sentinel Gujarat** – unified CCTV registry, viewing and analytics platform for Gujarat Police
(Dynatech Consultancy, Gujarat Police Innovation Challenge 2026).

* Registry API for departmental systems: `POST /api/v1/cameras/bulk` with an `X-API-Key` (scope `bulk`).
* Operator/jury API: JWT from `POST /api/auth/login` (roles `admin`, `dept_admin`, `operator`, `viewer`).
* ANPR worker ingestion: `/api/internal/*` with an `X-API-Key` (scope `internal`).
* Real-time channels: `/ws/alerts`, `/ws/reads/{camera_id}`, `/ws/health` (`?token=<jwt>`).

All timestamps are UTC ISO-8601 (`...Z`); the UI and every export render IST (Asia/Kolkata).
"""

STARTED_AT = time.monotonic()
_background: set[asyncio.Task] = set()


def _spawn(coro, name: str) -> None:
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _startup_relay_paths() -> None:
    """Re-add every non-retired camera's MediaMTX path (runtime paths are not persisted, §8.4)."""
    try:
        n = await ensure_all_relay_paths()
        log.info("relay paths ensured on startup: %d", n)
    except Exception:  # noqa: BLE001
        log.exception("startup relay path creation failed (the health poller will retry)")


async def _job_health() -> None:
    await poller.run_once()


async def _job_stats() -> None:
    try:
        await broadcast_stats()
    except Exception:  # noqa: BLE001
        log.exception("ws stats broadcast failed")


async def _job_retention() -> None:
    try:
        await run_retention(dry_run=False)
    except Exception:  # noqa: BLE001
        log.exception("retention purge failed")


async def _job_matcher() -> None:
    try:
        async with SessionLocal() as db:
            await matcher.maybe_reload(db)
    except Exception:  # noqa: BLE001
        log.exception("matcher reload failed")


def build_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(_job_health, IntervalTrigger(seconds=settings.HEALTH_POLL_SECONDS), id="health_poll", max_instances=1, coalesce=True, next_run_time=None)
    sched.add_job(_job_stats, IntervalTrigger(seconds=settings.WS_STATS_INTERVAL_S), id="ws_stats", max_instances=1, coalesce=True)
    sched.add_job(_job_matcher, IntervalTrigger(seconds=60), id="matcher_reload", max_instances=1, coalesce=True)
    sched.add_job(_job_retention, CronTrigger(hour=settings.RETENTION_JOB_HOUR_UTC, minute=0, timezone="UTC"), id="retention", max_instances=1, coalesce=True)
    return sched


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging(settings.LOG_LEVEL)
    log.info("starting %s %s", settings.PRODUCT_NAME, __version__)
    ensure_layout()
    await init_db()
    if settings.SEED_ON_START:
        from app.seed import run_seed

        await run_seed()
    async with SessionLocal() as db:
        await cfg.load(db)
        await matcher.reload(db)
    _spawn(_startup_relay_paths(), "relay-paths")
    scheduler: AsyncIOScheduler | None = None
    if settings.SCHEDULER_ENABLED:
        scheduler = build_scheduler()
        scheduler.start()
        # first health poll shortly after start (MediaMTX may still be coming up)
        scheduler.modify_job("health_poll", next_run_time=datetime.now(timezone.utc) + timedelta(seconds=20))
        app.state.scheduler = scheduler
    log.info("startup complete")
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        for t in list(_background):
            t.cancel()
        await dispose()
        log.info("shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PRODUCT_NAME,
        version=__version__,
        description=DESCRIPTION,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
        contact={"name": "Dynatech Consultancy – Sentinel Gujarat team"},
        license_info={"name": "Open source (see docs/LICENCES.md)"},
    )
    install_exception_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "X-Sentinel-Sha256", "X-Sentinel-Report-Id", "X-Request-Id"],
    )
    app.add_middleware(AuditMiddleware, routes=lambda: app.routes)

    api_routers = [
        auth.router, users.router, api_keys.router, cameras.router, imports.router, health.router, gap.router,
        geo.router, streams.router, detections.router, vehicles.router, watchlist.router, alerts.router,
        events.router, dashboard.router, reports.router, qa.router, audit.router, settings_api.router,
        webhooks.router, recordings.router, clips.router, object_counts.router, zones.router, evidence.router,
        external.router, internal.router, mock_sandbox.router,
    ]
    for r in api_routers:
        app.include_router(r, prefix="/api")
    app.include_router(healthz.router, prefix="/api")
    # the organiser-shaped catalogue is also reachable at the root so that SANDBOX_BASE_URL=http://api:8000/mock-sandbox works inside the compose network
    app.include_router(mock_sandbox.router, include_in_schema=False)
    app.include_router(healthz.router)
    app.include_router(ws.router)
    app.include_router(media.router)
    return app


app = create_app()
