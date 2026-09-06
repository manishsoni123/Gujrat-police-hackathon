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
DEFAULT_INTERNAL_API_KEY = "sk_internal0000000000000000000000000000000000"
DEFAULT_BULK_API_KEY = "sk_bulk00000000000000000000000000000000000000"
# Seeded jury passwords (CONTRACT §2.7); published in README/CONTRACT, so they count as default secrets.
DEFAULT_SEED_PASSWORDS = {
    "JURY_ADMIN_PASSWORD": "Sentinel@Admin2026",
    "JURY_OPERATOR_PASSWORD": "Sentinel@Ops2026",
    "JURY_VIEWER_PASSWORD": "Sentinel@View2026",
    "DEPT_ADMIN_PASSWORD": "Sentinel@Police2026",
}
_DEFAULT_KEYS = {"INTERNAL_API_KEY": DEFAULT_INTERNAL_API_KEY, "BULK_API_KEY": DEFAULT_BULK_API_KEY}


def default_secret_problems(jwt_secret: str, database_url: str) -> list[str]:
    """Fatal-on-public defaults (pure; shared by the startup guard and `tests/test_config.py`)."""
    problems: list[str] = []
    if jwt_secret == DEFAULT_JWT_SECRET:
        problems.append("JWT_SECRET is the repository default")
    elif len(jwt_secret) < MIN_JWT_SECRET_LEN:
        problems.append(f"JWT_SECRET is shorter than {MIN_JWT_SECRET_LEN} characters")
    if f":{DEFAULT_DB_PASSWORD}@" in database_url:
        problems.append("POSTGRES_PASSWORD is the repository default")
    return problems


def default_secret_warnings(values: dict[str, str]) -> list[str]:
    """Env names among the seeded API keys / jury passwords that still equal the repository default.

    These do not stop the API (the laptop demo runs on them by design) but on a public
    deployment (`COOKIE_SECURE=1` / https) they are logged loudly at startup, reported as
    `default_secrets_in_use` on `/healthz` and shown as an admin banner in the UI
    (CONTRACT Amendments 2026-09-05).
    """
    out: list[str] = []
    for name, default in {**_DEFAULT_KEYS, **DEFAULT_SEED_PASSWORDS}.items():
        if values.get(name) == default:
            out.append(name)
    return out


def is_public_deployment(public_base_url: str, cookie_secure: bool) -> bool:
    return bool(cookie_secure) or (public_base_url or "").lower().startswith("https:")


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
    # Organiser ("Sentinel") sandbox (CONTRACT Amendments 2026-09-05, real sandbox). Initial values of the
    # `catalogue.*` / `sandbox.*` settings; every one is editable at run time in Settings -> Catalogue.
    # CATALOGUE_SOURCE: mock | sentinel_portal | generic_json (empty = mock when MOCK_SANDBOX=1, else generic_json)
    CATALOGUE_SOURCE: str = ""
    SANDBOX_STREAM_HOST: str = "103.250.160.189"
    SANDBOX_RTSP_PORT: int = 8554
    SANDBOX_WHEP_PORT: int = 8889
    SANDBOX_HLS_BASE: str = "https://cctv.corp8.cloud"
    SANDBOX_STREAM_EMAIL: str = ""
    SANDBOX_STREAM_PASSWORD: str = ""  # access password (HTTP Basic in the stream URL) - never logged or returned
    SANDBOX_PORTAL_URL: str = "https://cctv.corp8.cloud"
    SANDBOX_PORTAL_EMAIL: str = ""
    SANDBOX_PORTAL_PASSWORD: str = ""  # portal login (session cookie for /cameras.json); optional
    CATALOGUE_ENRICHMENT_PATH: str = "/app/media/cameras_enrichment.csv"
    CATALOGUE_CAMERAS_JSON_PATH: str = "/app/media/cameras.json"
    SANDBOX_PROBE_TIMEOUT_S: int = 30  # direct ffprobe cap per sandbox camera (the sandbox answers in 4-38 s)
    SANDBOX_PROBE_PARALLEL: int = 6
    # Outbound URL guard (core/urlguard.py): 1 permits private/loopback/compose hosts for the catalogue base URL and
    # webhooks; they are always permitted while MOCK_SANDBOX=1 (the laptop demo targets http://api:8000/...).
    ALLOW_PRIVATE_URLS: bool = False

    INTERNAL_API_KEY: str = "sk_internal0000000000000000000000000000000000"
    BULK_API_KEY: str = "sk_bulk00000000000000000000000000000000000000"

    JURY_ADMIN_PASSWORD: str = "Sentinel@Admin2026"
    JURY_OPERATOR_PASSWORD: str = "Sentinel@Ops2026"
    JURY_VIEWER_PASSWORD: str = "Sentinel@View2026"
    DEPT_ADMIN_PASSWORD: str = "Sentinel@Police2026"

    SEED_ON_START: bool = True
    HEALTH_POLL_SECONDS: int = 60
    HEALTH_OFFLINE_AFTER: int = 3
    HEALTH_PROBE_MAX: int = 2  # active probes per tick (idle on-demand cameras only; persistent ones are never probed)
    HEALTH_PROBE_TIMEOUT_S: int = 6
    # "Pace your load" (organiser rule): at most this many ffprobes at once, 3 s apart, and never more than one
    # probe per idle camera per HEALTH_PROBE_MIN_INTERVAL_S (in between the camera keeps its status: "retry later").
    HEALTH_PROBE_PARALLEL: int = 2
    HEALTH_PROBE_MIN_INTERVAL_S: int = 900
    # How an idle on-demand sandbox camera is probed: `direct` (one short connection to the source) or `relay`
    # (through cam_<id>, which then holds the upstream copy for the 10-minute close-after).
    HEALTH_PROBE_VIA: str = "direct"
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
        public = self.is_public
        logger = logging.getLogger("sentinel.config")
        for p in problems:
            (logger.error if public else logger.warning)("insecure configuration: %s", p)
        if problems and public:
            sys.stderr.write(
                "FATAL: refusing to start a public deployment with default secrets: "
                + "; ".join(problems)
                + ". Set JWT_SECRET (openssl rand -hex 32) and POSTGRES_PASSWORD in deploy/.env.\n"
            )
            sys.exit(1)
        weak = self.default_secrets_in_use
        if weak:
            # Loud, repeated banner: the API still starts (the jury demo needs the documented logins) but the
            # operator, /healthz (`default_secrets_in_use`) and the UI banner all say so.
            msg = (
                "SECURITY WARNING: public deployment still uses the repository defaults for "
                + ", ".join(weak)
                + " (published in README/CONTRACT). Change them in deploy/.env and restart; the default BULK_API_KEY is "
                "seeded inactive on this deployment."
            )
            logger.error(msg)
            sys.stderr.write("\n" + "!" * 100 + "\n" + msg + "\n" + "!" * 100 + "\n\n")
        return self

    @property
    def catalogue_source_default(self) -> str:
        """Initial `catalogue.source`: the env value, else `mock` on the laptop (MOCK_SANDBOX=1), else `generic_json`."""
        v = (self.CATALOGUE_SOURCE or "").strip().lower()
        if v in ("mock", "sentinel_portal", "generic_json"):
            return v
        return "mock" if self.MOCK_SANDBOX else "generic_json"

    @property
    def is_public(self) -> bool:
        """HTTPS / secure-cookie deployment (the hosted demo), as opposed to the plain-HTTP laptop."""
        return is_public_deployment(self.PUBLIC_BASE_URL, self.COOKIE_SECURE)

    @property
    def default_secrets_in_use(self) -> list[str]:
        """Names of seeded keys/passwords still at their published default **on a public deployment** (else [])."""
        if not self.is_public:
            return []
        return default_secret_warnings({k: getattr(self, k) for k in [*_DEFAULT_KEYS, *DEFAULT_SEED_PASSWORDS]})

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
