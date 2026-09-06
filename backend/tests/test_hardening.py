"""Ops/security hardening helpers (CONTRACT Amendments 2026-09-05): token revocation, query tokens,
login limiter, CSV formula neutralisation, audit skip rules, page_size, outbound-URL guard,
heartbeat validation, default-secret warnings, seeded-key activation."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.deps import query_token_allowed
from app.core.audit_middleware import should_audit
from app.core.config import DEFAULT_BULK_API_KEY, DEFAULT_INTERNAL_API_KEY, DEFAULT_SEED_PASSWORDS, Settings, default_secret_warnings, is_public_deployment
from app.core.errors import ApiError
from app.core.ratelimit import RateLimiter
from app.core.security import create_access_token, decode_token, not_before_epoch, token_issued_after_reset
from app.core.urlguard import check_outbound_url, host_is_private
from app.schemas.common import PageParams
from app.schemas.internal import Heartbeat
from app.seed import seed_key_active
from app.services.csv_importer import neutralise_cell, render_csv
from app.services.report_builder import build_csv, csv_cell, rows_hash

STRONG = "a" * 40
SAFE_DB = "postgresql+asyncpg://sentinel:s3cr3t-value@postgres:5432/sentinel"


# ---- JWT revocation after password reset ---------------------------------------------------------

def _user(**kw):
    base = dict(id=7, username="u", role="viewer", department_id=None, district=None, token_not_before=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_token_survives_without_reset_and_dies_after_reset() -> None:
    token, _exp = create_access_token(_user())
    claims = decode_token(token)
    assert claims and claims["sub"] == "7" and "iat" in claims and "jti" in claims
    assert token_issued_after_reset(claims, None)
    reset_at = datetime.now(tz=timezone.utc) + timedelta(seconds=2)
    assert not token_issued_after_reset(claims, reset_at)


def test_token_minted_right_after_reset_is_valid() -> None:
    reset_at = datetime.now(tz=timezone.utc)  # same second as the login that follows
    token, _ = create_access_token(_user(token_not_before=reset_at))
    claims = decode_token(token)
    assert claims["iat"] >= not_before_epoch(reset_at)
    assert token_issued_after_reset(claims, reset_at)


def test_decode_rejects_garbage_and_missing_claims() -> None:
    assert decode_token("not-a-token") is None
    import jwt

    from app.core.config import settings

    no_iat = jwt.encode({"sub": "1", "exp": int(time.time()) + 60}, settings.JWT_SECRET, algorithm="HS256")
    assert decode_token(no_iat) is None
    wrong_key = jwt.encode({"sub": "1", "iat": int(time.time()), "exp": int(time.time()) + 60}, "x" * 40, algorithm="HS256")
    assert decode_token(wrong_key) is None


# ---- ?token= only on /ws and /media ----------------------------------------------------------------

@pytest.mark.parametrize("path,ok", [("/ws/alerts", True), ("/ws/reads/3", True), ("/media/crops/1/x.jpg", True), ("/api/cameras", False), ("/api/auth/me", False), ("/healthz", False)])
def test_query_token_only_for_ws_and_media(path: str, ok: bool) -> None:
    assert query_token_allowed(path) is ok


# ---- login limiter ---------------------------------------------------------------------------------

def test_user_limiter_counts_failures_only_and_ip_window_is_not_reset() -> None:
    ip = RateLimiter(3, 60)
    assert ip.allow("1.2.3.4") and ip.allow("1.2.3.4") and ip.allow("1.2.3.4")
    assert not ip.allow("1.2.3.4")
    user = RateLimiter(2, 60)
    assert not user.blocked("jury_admin")
    user.record("jury_admin")
    assert not user.blocked("jury_admin")
    user.record("jury_admin")
    assert user.blocked("jury_admin")
    assert not user.blocked("jury_viewer")  # per-username, not per-IP
    user.reset("jury_admin")
    assert not user.blocked("jury_admin")
    assert not ip.allow("1.2.3.4")  # a success elsewhere never clears the IP window


# ---- CSV formula neutralisation --------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("=HYPERLINK(\"http://evil/\")", "'=HYPERLINK(\"http://evil/\")"),
    ("+1+1", "'+1+1"), ("-cmd", "'-cmd"), ("@SUM(A1)", "'@SUM(A1)"), ("\t=1", "'\t=1"), ("\r=1", "'\r=1"),
    ("Sector 7 PS", "Sector 7 PS"), ("GJ01AB1234", "GJ01AB1234"), ("a\x00b\x1fc", "abc"), ("", ""),
])
def test_neutralise_cell(raw: str, expected: str) -> None:
    assert neutralise_cell(raw) == expected


def test_neutralise_leaves_non_strings() -> None:
    assert neutralise_cell(-12.5) == -12.5 and neutralise_cell(3) == 3 and neutralise_cell(None) is None and neutralise_cell(True) is True
    assert csv_cell(-1) == -1 and csv_cell("-1") == "'-1" and csv_cell(True) == "true"


def test_csv_exports_and_hash_use_neutralised_spelling() -> None:
    rows = [["=cmd|' /C calc'!A0", True, -2.5]]
    data = build_csv(["name", "flag", "lat"], rows, "# x").decode("utf-8").splitlines()
    assert data[1].startswith("'=cmd|") and data[1].endswith(",true,-2.5")
    assert rows_hash(rows) == rows_hash([["'=cmd|' /C calc'!A0", "true", -2.5]])
    err = render_csv(["row", "line"], [[1, "=1+1"]])
    assert "'=1+1" in err


# ---- audit middleware skip rules ---------------------------------------------------------------------

@pytest.mark.parametrize("method,path,has_audit,matched,status,expected", [
    ("POST", "/api/internal", False, False, 404, False),      # no synthetic post.api.internal row
    ("POST", "/api/internal/detections", False, True, 200, False),
    ("POST", "/api/mock-sandbox/webhook-sink", False, True, 204, False),
    ("POST", "/api/nope", False, False, 404, False),          # unmatched route, nothing happened
    ("POST", "/api/cameras", False, True, 201, True),
    ("POST", "/api/cameras/999", False, True, 404, True),     # matched route, real 404 is still audited
    ("GET", "/api/cameras", False, True, 200, False),
    ("GET", "/api/vehicles/search", True, True, 200, True),
    ("OPTIONS", "/api/cameras", False, True, 200, False),
    ("POST", "/healthz", False, True, 200, False),
])
def test_should_audit(method, path, has_audit, matched, status, expected) -> None:
    assert should_audit(method, path, has_audit, matched, status) is expected


# ---- page_size --------------------------------------------------------------------------------------

def test_page_size_over_endpoint_max_is_422_not_clamped() -> None:
    p = PageParams(page=1, page_size=300, sort=None, order=None)
    with pytest.raises(ApiError) as exc:
        p.resolve({"name": None}, "name", "asc")
    assert exc.value.status == 422 and exc.value.errors[0]["message"] == "must be <= 200"
    assert p.page_size == 300  # never silently clamped
    assert PageParams(page=1, page_size=300, sort=None, order=None).resolve({"ts": None}, "ts", "desc", max_size=500) == ("ts", "desc")
    with pytest.raises(ApiError) as exc2:
        PageParams(page=1, page_size=999, sort=None, order=None)
    assert exc2.value.errors[0]["message"] == "must be <= 500"


# ---- outbound URL guard (SSRF) -----------------------------------------------------------------------

@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "192.168.1.9", "172.16.0.1", "169.254.169.254", "::1", "fd00::1", "::ffff:10.0.0.1", "localhost", "api", "mediamtx", "postgres", "db.internal", "0.0.0.0"])
def test_private_hosts_detected(host: str) -> None:
    assert host_is_private(host, resolve=False)


@pytest.mark.parametrize("host", ["8.8.8.8", "catalogue.gujarat.gov.in", "example.com", "2001:4860:4860::8888"])
def test_public_hosts_pass(host: str) -> None:
    assert not host_is_private(host, resolve=False)


def test_check_outbound_url_rules() -> None:
    assert check_outbound_url("https://catalogue.example.in/base", allow_private=False, resolve=False) == "https://catalogue.example.in/base"
    with pytest.raises(ValueError):
        check_outbound_url("http://169.254.169.254/latest/meta-data", allow_private=False, resolve=False)
    with pytest.raises(ValueError):
        check_outbound_url("http://mediamtx:9997/v3/config/global", allow_private=False, resolve=False)
    with pytest.raises(ValueError):
        check_outbound_url("ftp://example.com/x", allow_private=False, resolve=False)
    with pytest.raises(ValueError):
        check_outbound_url("http://user:pw@example.com/x", allow_private=False, resolve=False)
    # the laptop demo (MOCK_SANDBOX=1 or ALLOW_PRIVATE_URLS=1) keeps http://api:8000/mock-sandbox working
    assert check_outbound_url("http://api:8000/mock-sandbox", allow_private=True, resolve=False) == "http://api:8000/mock-sandbox"


# ---- heartbeat validation ----------------------------------------------------------------------------

def test_heartbeat_mode_is_validated_and_extra_kept() -> None:
    hb = Heartbeat.model_validate({"worker_id": "live-1", "mode": "preindex", "extra": {"detector": {"requested": "onnx", "active": "contour", "degraded": True}}})
    assert hb.mode == "preindex" and hb.extra["detector"]["degraded"] is True
    with pytest.raises(ValidationError):
        Heartbeat.model_validate({"worker_id": "live-1", "mode": "banana"})
    with pytest.raises(ValidationError):
        Heartbeat.model_validate({"worker_id": "", "mode": "live"})


# ---- default secrets on a public deployment ------------------------------------------------------------

def test_default_secret_warnings_names_every_default() -> None:
    values = {"INTERNAL_API_KEY": DEFAULT_INTERNAL_API_KEY, "BULK_API_KEY": DEFAULT_BULK_API_KEY, **DEFAULT_SEED_PASSWORDS}
    assert set(default_secret_warnings(values)) == set(values)
    rotated = {k: "x" * 43 for k in values}
    assert default_secret_warnings(rotated) == []
    assert is_public_deployment("https://x", False) and is_public_deployment("http://localhost", True) and not is_public_deployment("http://localhost", False)


def test_public_deployment_with_default_keys_starts_but_reports_them(capsys) -> None:
    s = Settings(JWT_SECRET=STRONG, DATABASE_URL=SAFE_DB, PUBLIC_BASE_URL="https://sentinel.example.in", COOKIE_SECURE=True)
    assert s.is_public
    assert "BULK_API_KEY" in s.default_secrets_in_use and "JURY_ADMIN_PASSWORD" in s.default_secrets_in_use
    assert "SECURITY WARNING" in capsys.readouterr().err
    rotated = Settings(JWT_SECRET=STRONG, DATABASE_URL=SAFE_DB, PUBLIC_BASE_URL="https://sentinel.example.in", COOKIE_SECURE=True,
                       INTERNAL_API_KEY="sk_" + "a" * 40, BULK_API_KEY="sk_" + "b" * 40, JURY_ADMIN_PASSWORD="Xy" * 8,
                       JURY_OPERATOR_PASSWORD="Xy" * 8, JURY_VIEWER_PASSWORD="Xy" * 8, DEPT_ADMIN_PASSWORD="Xy" * 8)
    assert rotated.default_secrets_in_use == []


def test_laptop_never_reports_default_secrets() -> None:
    s = Settings(PUBLIC_BASE_URL="http://localhost", COOKIE_SECURE=False)
    assert s.default_secrets_in_use == []


def test_default_bulk_key_is_inactive_only_on_public_deployments() -> None:
    assert seed_key_active("BULK_API_KEY", DEFAULT_BULK_API_KEY, public=False)
    assert not seed_key_active("BULK_API_KEY", DEFAULT_BULK_API_KEY, public=True)
    assert seed_key_active("BULK_API_KEY", "sk_" + "c" * 40, public=True)
    assert seed_key_active("INTERNAL_API_KEY", DEFAULT_INTERNAL_API_KEY, public=True)
