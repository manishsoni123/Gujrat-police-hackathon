"""Application settings (CONTRACT §1.3, API block). Every value has a laptop-safe default."""

from __future__ import annotations

import logging
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_JWT_SECRET = "change-me-sentinel-gujarat-2026-please"
DEFAULT_DB_PASSWORD = "sentinel"
MIN_JWT_SECRET_LEN = 32


def default_secret_problems(jwt_secret: str, database_url: str) -> list[str]:
    """Pure check shared by the startup guard and `tests/test_config.py`."""
    problems: list[str] = []
    if jwt_secret == DEFAULT_JWT_SECRET:
        problems.append("JWT_SECRET is the repository default")
    elif len(jwt_secret) < MIN_JWT_SECRET_LEN:
        problems.append(f"JWT_SECRET is shorter than {MIN_JWT_SECRET_LEN} characters")
    if f":{DEFAULT_DB_PASSWORD}@" in database_url:
        problems.append("POSTGRES_PASSWORD is the repository default")
    return problems


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    APP_VERSION: str = "1.0.0-phase1"
    PRODUCT_NAME: str = "Sentinel Gujarat"
    TEAM_NAME: str = "Dynatech Consultancy"

    DATABASE_URL: str = "postgresql+asyncpg://sentinel:sentinel@postgres:5432/sentinel"

    JWT_SECRET: str = "change-me-sentinel-gujarat-2026-please"
    JWT_EXPIRE_HOURS: int = 8
    PUBLIC_BASE_URL: str = "http://localhost"
    CORS_ORIGINS: str = "http://localhost,http://localhost:5173,http://127.0.0.1:5173"
    COOKIE_SECURE: bool = False

    DATA_DIR: str = "/data"
    MEDIA_DIR: str = "/media"
    RECORDINGS_DIR: str = "/recordings"
    SEEDS_DIR: str = ""

    MEDIAMTX_API_URL: str = "http://mediamtx:9997"
    MEDIAMTX_RTSP_URL: str = "rtsp://mediamtx:8554"
    MEDIAMTX_RTSP_LOCAL_URL: str = "rtsp://localhost:8554"
    MEDIAMTX_HLS_URL: str = "http://mediamtx:8888"
    MEDIAMTX_PLAYBACK_URL: str = "http://mediamtx:9996"
    MEDIAMTX_TRANSCODE: str = "cpu"

    MOCK_SANDBOX: bool = True
    SANDBOX_BASE_URL: str = "http://api:8000/mock-sandbox"
    SANDBOX_AUTH_TYPE: str = "none"
    SANDBOX_USERNAME: str = ""
    SANDBOX_PASSWORD: str = ""
    SANDBOX_AUTH_HEADER: str = ""
    SANDBOX_TIMEOUT_S: int = 30

    INTERNAL_API_KEY: str = "sk_internal0000000000000000000000000000000000"
    BULK_API_KEY: str = "sk_bulk00000000000000000000000000000000000000"

    JURY_ADMIN_PASSWORD: str = "Sentinel@Admin2026"
    JURY_OPERATOR_PASSWORD: str = "Sentinel@Ops2026"
    JURY_VIEWER_PASSWORD: str = "Sentinel@View2026"
    DEPT_ADMIN_PASSWORD: str = "Sentinel@Police2026"

    SEED_ON_START: bool = True
    HEALTH_POLL_SECONDS: int = 60
    HEALTH_OFFLINE_AFTER: int = 3
    HEALTH_PROBE_MAX: int = 20
    HEALTH_PROBE_TIMEOUT_S: int = 6
    ANPR_AUTO_ENABLE_MAX: int = 12
    ALERT_SUPPRESSION_SECONDS: int = 60
    ALERT_RE_ALERT_MINUTES: int = 60
    FUZZY_ALERT_MIN_CONF: float = 0.8
    ALERT_ESCALATE_MINUTES: int = 5
    ROUTE_SPEED_FLAG_KMH: float = 150
    ROUTE_DEFAULT_WINDOW_HOURS: int = 24
    GAP_COVERAGE_RADIUS_M: int = 150
    GAP_POI_RADIUS_M: int = 300
    GAP_GRID_M: int = 500
    AGEING_YEARS: int = 5
    GAP_CACHE_SECONDS: int = 300
    RETENTION_DAYS_FRAMES: int = 7
    RETENTION_DAYS_READS: int = 30
    RETENTION_DAYS_CLIPS: int = 90
    RETENTION_JOB_HOUR_UTC: int = 21
    SNAPSHOT_STALE_SECONDS: int = 10
    WS_STATS_INTERVAL_S: int = 10
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    LOGIN_RATE_LIMIT_PER_MIN: int = 10
    LOG_LEVEL: str = "INFO"
    TZ_DISPLAY: str = "Asia/Kolkata"

    # Worker-facing settings echoed by /internal/anpr-config
    ANPR_FPS: int = 5
    PREINDEX_FPS: int = 1
    ANPR_DET_CONF: float = 0.4
    ANPR_MIN_PLATE_W: int = 60
    ANPR_VOTE_WINDOW_S: float = 3
    SIGHTING_CLOSE_S: float = 15
    OBJECT_DETECT: bool = True
    OBJECT_EVERY_N: int = 5
    SNAPSHOT_INTERVAL_S: float = 1
    HEARTBEAT_S: int = 15

    SCHEDULER_ENABLED: bool = True

    @field_validator("MEDIAMTX_TRANSCODE")
    @classmethod
    def _transcode(cls, v: str) -> str:
        v = (v or "cpu").lower()
        return v if v in ("cpu", "nvenc") else "cpu"

    @model_validator(mode="after")
    def _guard_default_secrets(self) -> "Settings":
        """Refuse to start a public (HTTPS / secure-cookie) deployment on the repository defaults.

        A JWT signed with the published default secret is accepted as any user, so an operator
        who forgot the one line in `deploy/.env` would expose an unauthenticated admin. On the
        plain-HTTP laptop demo the defaults are merely logged as warnings.
        """
        problems = default_secret_problems(self.JWT_SECRET, self.DATABASE_URL)
        if not problems:
            return self
        public = bool(self.COOKIE_SECURE) or self.PUBLIC_BASE_URL.lower().startswith("https:")
        logger = logging.getLogger("sentinel.config")
        for p in problems:
            (logger.error if public else logger.warning)("insecure configuration: %s", p)
        if public:
            sys.stderr.write(
                "FATAL: refusing to start a public deployment with default secrets: "
                + "; ".join(problems)
                + ". Set JWT_SECRET (openssl rand -hex 32) and POSTGRES_PASSWORD in deploy/.env.\n"
            )
            sys.exit(1)
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def data_dir(self) -> Path:
        return Path(self.DATA_DIR)

    @property
    def seeds_dir(self) -> Path:
        if self.SEEDS_DIR:
            return Path(self.SEEDS_DIR)
        here = Path(__file__).resolve().parent.parent.parent
        return here / "seeds"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
