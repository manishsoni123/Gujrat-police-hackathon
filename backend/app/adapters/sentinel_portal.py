"""Organiser ("Sentinel") sandbox adapter — `CameraSourceAdapter` for `catalogue.source = sentinel_portal`.

Catalogue modes (CONTRACT Amendments 2026-09-05):

- **upload**: a `cameras.json` sent with the import request (kept as the "last uploaded file" under
  `DATA_DIR/catalogue/`), the primary path on the laptop;
- **portal**: log in to the portal (`catalogue.portal_url`, e-mail + portal password — *not* the stream
  access password) with a cookie jar and `GET /cameras.json`; on any failure, or when the portal password
  is not configured, fall back to the last uploaded / server-side file and say so in the warnings;
- **file**: the last uploaded file, else `catalogue.cameras_json_path` (`/app/media/cameras.json`).

Stream URLs are built from the `sandbox.*` settings (`rtsp://<email>:<access_password>@<host>:8554/stream/<id>`);
the password never appears in logs, responses or audit rows (`serializers.mask_url` / `mask_secrets_in_text`).
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from app.adapters.base import CameraSourceAdapter, ProbeResult, SourceCamera
from app.adapters.rtsp import ffprobe_details
from app.core.config import settings as env
from app.core.urlguard import check_outbound_url
from app.services import settings_service as cfg
from app.services.sandbox_catalogue import CatalogueError
from app.services.sentinel_catalogue import (
    CatalogueEntry,
    CatalogueParseError,
    EnrichmentTable,
    StreamConfig,
    merge_catalogue,
    parse_cameras_json,
    parse_enrichment_csv,
)
from app.services.serializers import mask_secrets_in_text

log = logging.getLogger("sentinel.sandbox")

USER_AGENT = "SentinelGujarat/1.0.0-phase1"
UPLOAD_DIR = "catalogue"  # under DATA_DIR
UPLOADED_CAMERAS_JSON = f"{UPLOAD_DIR}/cameras.json"
UPLOADED_ENRICHMENT_CSV = f"{UPLOAD_DIR}/cameras_enrichment.csv"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

_FORM_RE = re.compile(r"<form\b[^>]*>.*?</form>", re.I | re.S)
_ATTR_RE = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
_INPUT_RE = re.compile(r"<input\b[^>]*>", re.I)
_CSRF_META_RE = re.compile(r"""<meta\b[^>]*name\s*=\s*["'](csrf-token|_csrf|csrf_token)["'][^>]*content\s*=\s*["']([^"']+)["']""", re.I)


@dataclass(frozen=True)
class PortalConfig:
    url: str
    email: str
    password: str
    timeout_s: float = 20.0

    @property
    def configured(self) -> bool:
        return bool(self.url and self.email and self.password)


@dataclass
class CatalogueInfo:
    mode: str  # upload | portal | file
    source: str  # human-readable origin (no secrets)
    count: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0


def stream_config_from_settings(overrides: dict[str, Any] | None = None) -> StreamConfig:
    o = overrides or {}

    def pick(key: str, setting: str) -> Any:
        v = o.get(key)
        return v if v is not None and v != cfg.MASK else cfg.get(setting)

    return StreamConfig(
        host=str(pick("stream_host", "sandbox.stream_host") or "").strip(),
        rtsp_port=int(pick("rtsp_port", "sandbox.rtsp_port") or 8554),
        whep_port=int(pick("whep_port", "sandbox.whep_port") or 8889),
        hls_base=str(pick("hls_base", "sandbox.hls_base") or "").strip(),
        email=str(pick("stream_email", "sandbox.stream_email") or "").strip(),
        password=str(pick("stream_password", "sandbox.stream_password") or ""),
    )


def portal_config_from_settings(overrides: dict[str, Any] | None = None) -> PortalConfig:
    o = overrides or {}

    def pick(key: str, setting: str) -> Any:
        v = o.get(key)
        return v if v is not None and v != cfg.MASK else cfg.get(setting)

    return PortalConfig(
        url=str(pick("portal_url", "catalogue.portal_url") or "").rstrip("/"),
        email=str(pick("portal_email", "catalogue.portal_email") or "").strip(),
        password=str(pick("portal_password", "catalogue.portal_password") or ""),
        timeout_s=float(cfg.get("catalogue.timeout_s") or 30),
    )


# ---- files under DATA_DIR/catalogue --------------------------------------------------------------------------------


def _data_path(rel: str) -> Path:
    return env.data_dir / rel


def save_upload(rel: str, data: bytes) -> Path:
    from app.core.hashing import write_bytes_hashed

    write_bytes_hashed(rel, data)
    return _data_path(rel)


def read_last_uploaded(rel: str) -> bytes | None:
    p = _data_path(rel)
    try:
        return p.read_bytes() if p.is_file() else None
    except OSError:
        return None


def resolve_enrichment_path() -> Path | None:
    """The configured enrichment CSV, else the last uploaded one, else `{MEDIA_DIR}/cameras_enrichment.csv`."""
    candidates = [str(cfg.get("catalogue.enrichment_path") or ""), str(_data_path(UPLOADED_ENRICHMENT_CSV)), f"{env.MEDIA_DIR.rstrip('/')}/cameras_enrichment.csv"]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


def resolve_cameras_json_path() -> Path | None:
    candidates = [str(_data_path(UPLOADED_CAMERAS_JSON)), str(cfg.get("catalogue.cameras_json_path") or ""), f"{env.MEDIA_DIR.rstrip('/')}/cameras.json"]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


def load_enrichment(enrichment_text: str | None = None) -> tuple[EnrichmentTable | None, str | None, list[str]]:
    """(table, origin, warnings). An explicit upload wins over the configured/last-uploaded/media file."""
    warnings: list[str] = []
    if enrichment_text is not None:
        try:
            return parse_enrichment_csv(enrichment_text), "upload", warnings
        except CatalogueParseError as exc:
            warnings.append(f"uploaded enrichment CSV rejected: {exc}")
            return None, None, warnings
    p = resolve_enrichment_path()
    if p is None:
        warnings.append("no enrichment CSV found (catalogue.enrichment_path); cameras get no coordinates or department")
        return None, None, warnings
    try:
        table = parse_enrichment_csv(p.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, CatalogueParseError) as exc:
        warnings.append(f"enrichment CSV {p} unusable: {exc}")
        return None, str(p), warnings
    return table, str(p), warnings


# ---- portal login --------------------------------------------------------------------------------------------------


def _attrs(tag: str) -> dict[str, str]:
    return {m.group(1).lower(): html.unescape(m.group(2) or m.group(3) or m.group(4) or "") for m in _ATTR_RE.finditer(tag)}


def discover_login_form(page_html: str, page_url: str) -> dict[str, Any] | None:
    """Find the first `<form>` with a password input; returns {action, fields(hidden), user_field, password_field}."""
    for form in _FORM_RE.findall(page_html):
        inputs = [_attrs(t) for t in _INPUT_RE.findall(form)]
        pw = next((i for i in inputs if i.get("type", "").lower() == "password"), None)
        if pw is None:
            continue
        fa = _attrs(form.split(">", 1)[0] + ">")
        action = urljoin(page_url, fa.get("action") or page_url)
        hidden = {i["name"]: i.get("value", "") for i in inputs if i.get("type", "").lower() == "hidden" and i.get("name")}
        user = next((i for i in inputs if i.get("type", "text").lower() in ("email", "text") and i.get("name") and re.search(r"mail|user|login|name", i["name"], re.I)), None)
        if user is None:
            user = next((i for i in inputs if i.get("type", "text").lower() in ("email", "text") and i.get("name")), None)
        return {"action": action, "method": (fa.get("method") or "post").lower(), "fields": hidden, "user_field": (user or {}).get("name") or "email", "password_field": pw.get("name") or "password"}
    return None


def _looks_like_json_list(r: httpx.Response) -> bool:
    ct = (r.headers.get("content-type") or "").lower()
    if "json" in ct:
        return True
    body = r.text.lstrip()[:1]
    return body in ("[", "{")


async def fetch_portal_cameras_json(portal: PortalConfig) -> tuple[bytes, list[str]]:
    """Log in (cookie jar) and download `/cameras.json`. Raises CatalogueError; never logs the password."""
    notes: list[str] = []
    try:
        check_outbound_url(portal.url)
    except ValueError as exc:
        raise CatalogueError(f"portal URL rejected: {exc}") from exc
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9, */*;q=0.5"}
    secrets = (portal.password,)
    try:
        async with httpx.AsyncClient(timeout=portal.timeout_s, follow_redirects=True, headers=headers) as client:
            r = await client.get(f"{portal.url}/cameras.json")
            if r.status_code == 200 and _looks_like_json_list(r):
                notes.append("cameras.json readable without login")
                return r.content, notes
            login_page = None
            for path in ("/auth/login", "/login"):
                lp = await client.get(f"{portal.url}{path}")
                if lp.status_code == 200 and "<form" in lp.text.lower():
                    login_page = lp
                    break
            form = discover_login_form(login_page.text, str(login_page.url)) if login_page is not None else None
            if form is not None:
                data = dict(form["fields"])
                data[form["user_field"]] = portal.email
                data[form["password_field"]] = portal.password
                meta = _CSRF_META_RE.search(login_page.text) if login_page is not None else None
                post_headers = {"Referer": str(login_page.url)} if login_page is not None else {}
                if meta:
                    post_headers["X-CSRF-Token"] = meta.group(2)
                lr = await client.post(form["action"], data=data, headers=post_headers)
                notes.append(f"portal form login → HTTP {lr.status_code} (fields {form['user_field']}/{form['password_field']})")
            else:
                # JSON login (SPA portal): try the two common bodies
                done = False
                for body in ({"email": portal.email, "password": portal.password}, {"username": portal.email, "password": portal.password}):
                    lr = await client.post(f"{portal.url}/auth/login", json=body, headers={"Accept": "application/json"})
                    notes.append(f"portal JSON login ({list(body)[0]}) → HTTP {lr.status_code}")
                    if lr.status_code < 400:
                        done = True
                        break
                if not done:
                    raise CatalogueError("portal login failed: no login form found and the JSON login was refused")
            r = await client.get(f"{portal.url}/cameras.json", headers={"Accept": "application/json"})
            if r.status_code != 200:
                raise CatalogueError(f"portal returned HTTP {r.status_code} for /cameras.json after login")
            if not _looks_like_json_list(r):
                raise CatalogueError("portal login did not yield a session: /cameras.json still returns the login page")
            return r.content, notes
    except httpx.HTTPError as exc:
        raise CatalogueError(mask_secrets_in_text(f"portal {portal.url} unreachable: {exc.__class__.__name__}: {exc}", secrets) or "portal unreachable") from exc


# ---- adapter -----------------------------------------------------------------------------------------------------------


class SentinelPortalAdapter(CameraSourceAdapter):
    """`source='sandbox'` cameras from the organiser sandbox (cameras.json + enrichment + stream credentials)."""

    name = "sandbox"

    def __init__(
        self,
        stream: StreamConfig | None = None,
        portal: PortalConfig | None = None,
        cameras_json: bytes | None = None,
        enrichment_csv: str | None = None,
        probe_timeout_s: float | None = None,
        probe_parallel: int | None = None,
    ) -> None:
        self.stream = stream or stream_config_from_settings()
        self.portal = portal or portal_config_from_settings()
        self.cameras_json = cameras_json
        self.enrichment_csv = enrichment_csv
        self.probe_timeout_s = float(probe_timeout_s or cfg.get("sandbox.probe_timeout_s") or env.SANDBOX_PROBE_TIMEOUT_S)
        self.probe_parallel = max(1, min(6, int(probe_parallel or cfg.get("sandbox.probe_parallel") or env.SANDBOX_PROBE_PARALLEL)))
        self.info = CatalogueInfo(mode="file", source="")
        self.enrichment: EnrichmentTable | None = None
        self.enrichment_origin: str | None = None
        self.entries: list[CatalogueEntry] = []
        self.warnings: list[dict[str, Any]] = []
        self.missing_enrichment: list[str] = []

    @property
    def secrets(self) -> tuple[str, ...]:
        return tuple(s for s in (self.stream.password, self.portal.password) if s)

    async def _catalogue_bytes(self) -> bytes:
        started = time.perf_counter()
        if self.cameras_json is not None:
            self.info = CatalogueInfo(mode="upload", source="uploaded cameras.json")
            try:
                save_upload(UPLOADED_CAMERAS_JSON, self.cameras_json)
            except OSError as exc:
                self.info.warnings.append(f"could not keep the uploaded cameras.json for later runs: {exc}")
            return self.cameras_json
        if self.portal.configured:
            try:
                data, notes = await fetch_portal_cameras_json(self.portal)
                self.info = CatalogueInfo(mode="portal", source=f"{self.portal.url}/cameras.json", warnings=notes)
                try:
                    save_upload(UPLOADED_CAMERAS_JSON, data)
                except OSError:
                    pass
                self.info.duration_ms = int((time.perf_counter() - started) * 1000)
                return data
            except CatalogueError as exc:
                self.info = CatalogueInfo(mode="file", source="", warnings=[f"portal fetch failed ({exc}); using the last uploaded / server-side cameras.json"], error=str(exc))
        else:
            self.info = CatalogueInfo(mode="file", source="", warnings=["portal password not configured (Settings → Catalogue); using the last uploaded / server-side cameras.json"])
        p = resolve_cameras_json_path()
        if p is None:
            raise CatalogueError("no cameras.json available: upload one on the Import page or set catalogue.cameras_json_path")
        self.info.source = str(p)
        self.info.duration_ms = int((time.perf_counter() - started) * 1000)
        try:
            return p.read_bytes()
        except OSError as exc:
            raise CatalogueError(f"cannot read {p}: {exc}") from exc

    async def list_cameras(self) -> list[SourceCamera]:
        raw = await self._catalogue_bytes()
        try:
            self.entries = parse_cameras_json(raw)
        except CatalogueParseError as exc:
            raise CatalogueError(f"{self.info.source or 'cameras.json'}: {exc}") from exc
        self.info.count = len(self.entries)
        self.enrichment, self.enrichment_origin, enr_warn = load_enrichment(self.enrichment_csv)
        self.info.warnings.extend(enr_warn)
        if self.enrichment is not None:
            self.info.warnings.extend(f"enrichment: {w}" for w in self.enrichment.warnings)
        if not self.stream.configured:
            self.info.warnings.append("sandbox.stream_email / sandbox.stream_password not configured: cameras are imported without stream URLs")
        merged = merge_catalogue(self.entries, self.enrichment, self.stream, self.info.mode)
        self.warnings = merged.warnings
        self.missing_enrichment = merged.missing_enrichment
        return merged.cameras

    def stream_url(self, camera: SourceCamera) -> str | None:
        return camera.rtsp_url

    async def probe(self, camera: SourceCamera) -> ProbeResult:
        if not camera.rtsp_url:
            return ProbeResult(False, error="no rtsp_url (stream credentials not configured)")
        return await ffprobe_details(camera.rtsp_url, timeout_s=self.probe_timeout_s, secrets=self.secrets)

    async def probe_all(self, cameras: list[SourceCamera]) -> dict[str, ProbeResult]:
        """Probe every camera with at most `probe_parallel` ffprobes at once ("pace your load": each client
        gets its own copy of the stream, so only the cameras being processed are opened, briefly)."""
        sem = asyncio.Semaphore(self.probe_parallel)
        results: dict[str, ProbeResult] = {}

        async def one(c: SourceCamera) -> None:
            async with sem:
                results[c.external_id] = await self.probe(c)

        await asyncio.gather(*(one(c) for c in cameras))
        return results

    async def probe_one(self, camera_id: str) -> ProbeResult:
        """Probe a single sandbox id (Settings → Test) with the current credentials."""
        cam = SourceCamera(external_id=camera_id, name=camera_id)
        from app.services.sentinel_catalogue import build_rtsp_url

        if not self.stream.configured:
            return ProbeResult(False, error="stream host, e-mail and access password must all be set")
        cam.rtsp_url = build_rtsp_url(self.stream, camera_id)
        return await self.probe(cam)
