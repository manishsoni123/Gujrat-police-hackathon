"""Organiser ("Sentinel") sandbox catalogue helpers — pure functions, no I/O (CONTRACT Amendments 2026-09-05).

The real sandbox (verified 5 Sept 2026) publishes only `cameras.json`, an array of `{"id": "cam01", "name": "01 …"}`
behind the portal login, and MediaMTX streams at `rtsp://<email>:<access_password>@<host>:8554/stream/<id>` (TCP).
Coordinates, district, department and type come from the team's enrichment CSV (`media/cameras_enrichment.csv`,
confidence-flagged, documented as team-inferred). Everything here is exercised by `tests/test_sentinel_portal.py`.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from app.adapters.base import SourceCamera
from app.db.models import CAMERA_TYPES, LOCATION_CONFIDENCE

ENRICHMENT_COLUMNS = (
    "external_id", "name", "lat", "lon", "district", "city", "police_station", "department_code",
    "camera_type_guess", "location_confidence", "location_source", "notes",
)
ENRICHMENT_REQUIRED = ("external_id",)
# camera_type_guess → CameraImportRow.type (RLVD = red-light violation detection: an ANPR-class camera)
TYPE_GUESS_MAP = {"rlvd": "anpr", "ip": "ip", "analog": "analog", "ptz": "ptz", "dome": "dome", "bullet": "bullet", "anpr": "anpr", "other": "other"}
MAX_CATALOGUE_ITEMS = 5000


class CatalogueParseError(ValueError):
    pass


@dataclass
class CatalogueEntry:
    external_id: str
    name: str


@dataclass
class EnrichmentRow:
    external_id: str
    name: str | None = None
    lat: float | None = None
    lon: float | None = None
    district: str | None = None
    city: str | None = None
    police_station: str | None = None
    department_code: str | None = None
    camera_type_guess: str | None = None
    location_confidence: str | None = None
    location_source: str | None = None
    notes: str | None = None


@dataclass
class EnrichmentTable:
    rows: dict[str, EnrichmentRow] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)

    @property
    def confidence_counts(self) -> dict[str, int]:
        out = {c: 0 for c in LOCATION_CONFIDENCE}
        for r in self.rows.values():
            if r.location_confidence in out:
                out[r.location_confidence] += 1
        return out


@dataclass(frozen=True)
class StreamConfig:
    """Where the sandbox relay lives and how to authenticate (HTTP Basic embedded in the URL)."""

    host: str
    rtsp_port: int = 8554
    whep_port: int = 8889
    hls_base: str = "https://cctv.corp8.cloud"
    email: str = ""
    password: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.host and self.email and self.password)


# ---- cameras.json ----------------------------------------------------------------------------------------------


def parse_cameras_json(raw: bytes | str | Any) -> list[CatalogueEntry]:
    """Accept the organiser's bare array (or a wrapper object) of `{id, name}`; ids are kept **verbatim**
    (MediaMTX path names are case-sensitive: `/stream/CAM03` is 'path not configured'). Names are kept
    byte-for-byte too — cameras.json is the only source of names (the RTSP SDP says "No Name" for most)."""
    if isinstance(raw, (bytes, bytearray)):
        text = bytes(raw).decode("utf-8-sig", "replace")
    elif isinstance(raw, str):
        text = raw
    else:
        text = None
    if text is not None:
        if not text.strip():
            raise CatalogueParseError("cameras.json is empty")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CatalogueParseError(f"cameras.json is not valid JSON: {exc.msg} at line {exc.lineno}") from exc
    else:
        data = raw
    from app.services.sandbox_catalogue import CatalogueError, unwrap

    try:
        items = unwrap(data)
    except CatalogueError as exc:
        raise CatalogueParseError(str(exc)) from exc
    if len(items) > MAX_CATALOGUE_ITEMS:
        raise CatalogueParseError(f"cameras.json lists {len(items)} cameras; the limit is {MAX_CATALOGUE_ITEMS}")
    out: list[CatalogueEntry] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        cid = item.get("id", item.get("camera_id", item.get("stream_id")))
        name = item.get("name", item.get("camera_name", item.get("title")))
        if cid is None or str(cid).strip() == "":
            raise CatalogueParseError(f"cameras.json item {i} has no id")
        cid_s = str(cid).strip()
        if "/" in cid_s or " " in cid_s or len(cid_s) > 64:
            raise CatalogueParseError(f"cameras.json item {i} has an unusable id {cid_s!r}")
        if cid_s in seen:
            raise CatalogueParseError(f"cameras.json lists id {cid_s!r} twice")
        seen.add(cid_s)
        name_s = str(name) if name is not None and str(name).strip() else cid_s
        out.append(CatalogueEntry(external_id=cid_s, name=name_s[:160]))
    if not out:
        raise CatalogueParseError("cameras.json lists no cameras")
    return out


# ---- enrichment CSV --------------------------------------------------------------------------------------------


def _float_or_none(v: str | None) -> float | None:
    if v is None or str(v).strip() == "":
        return None
    return float(str(v).strip())


def parse_enrichment_csv(text: str) -> EnrichmentTable:
    """Parse the team's enrichment CSV; rows with a bad coordinate are kept without coordinates (warning)."""
    if text and ord(text[0]) == 0xFEFF:  # UTF-8 BOM from Excel
        text = text[1:]
    reader = csv.DictReader(io.StringIO(text))
    table = EnrichmentTable()
    header = [h.strip() for h in (reader.fieldnames or [])]
    if "external_id" not in header:
        raise CatalogueParseError("enrichment CSV needs an 'external_id' column (join key to cameras.json)")
    table.unknown_columns = [h for h in header if h and h not in ENRICHMENT_COLUMNS]
    for n, raw in enumerate(reader, start=1):
        row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if k}
        ext = (row.get("external_id") or "").strip()
        if not ext:
            table.warnings.append(f"row {n}: blank external_id ignored")
            continue
        if ext in table.rows:
            table.warnings.append(f"row {n}: duplicate external_id {ext!r} ignored (first row kept)")
            continue
        try:
            lat, lon = _float_or_none(row.get("lat")), _float_or_none(row.get("lon"))
        except ValueError:
            table.warnings.append(f"row {n} ({ext}): lat/lon not numeric; coordinates dropped")
            lat = lon = None
        if (lat is None) != (lon is None):
            table.warnings.append(f"row {n} ({ext}): lat and lon must both be present; coordinates dropped")
            lat = lon = None
        if lat is not None and lon is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):
            table.warnings.append(f"row {n} ({ext}): lat/lon out of range; coordinates dropped")
            lat = lon = None
        conf = (row.get("location_confidence") or "").strip().lower() or None
        if conf is not None and conf not in LOCATION_CONFIDENCE:
            table.warnings.append(f"row {n} ({ext}): unknown location_confidence {conf!r} treated as 'guess'")
            conf = "guess"
        table.rows[ext] = EnrichmentRow(
            external_id=ext,
            name=row.get("name") or None,
            lat=lat,
            lon=lon,
            district=row.get("district") or None,
            city=row.get("city") or None,
            police_station=row.get("police_station") or None,
            department_code=(row.get("department_code") or "").strip().upper() or None,
            camera_type_guess=(row.get("camera_type_guess") or "").strip().lower() or None,
            location_confidence=conf,
            location_source=row.get("location_source") or None,
            notes=row.get("notes") or None,
        )
    return table


# ---- stream URLs -----------------------------------------------------------------------------------------------


def _userinfo(cfg: StreamConfig) -> str:
    # The "@" in the e-mail must be percent-encoded in the URL; encoding the password is harmless (verified) and
    # keeps a password containing "@", ":" or "/" unambiguous. ffmpeg and MediaMTX decode userinfo.
    return f"{quote(cfg.email, safe='')}:{quote(cfg.password, safe='')}"


def build_rtsp_url(cfg: StreamConfig, camera_id: str) -> str:
    return f"rtsp://{_userinfo(cfg)}@{cfg.host}:{cfg.rtsp_port}/stream/{camera_id}"


def build_whep_url(cfg: StreamConfig, camera_id: str) -> str:
    return f"http://{_userinfo(cfg)}@{cfg.host}:{cfg.whep_port}/stream/{camera_id}/whep"


def build_hls_url(cfg: StreamConfig, camera_id: str) -> str:
    return f"{cfg.hls_base.rstrip('/')}/{camera_id}/index.m3u8"


# ---- merge -----------------------------------------------------------------------------------------------------


def camera_type_from_guess(guess: str | None) -> str | None:
    if not guess:
        return None
    t = TYPE_GUESS_MAP.get(guess.strip().lower())
    return t if t in CAMERA_TYPES else None


@dataclass
class MergedCatalogue:
    cameras: list[SourceCamera]
    warnings: list[dict[str, Any]] = field(default_factory=list)  # RowIssue-shaped dicts
    missing_enrichment: list[str] = field(default_factory=list)


def merge_catalogue(entries: list[CatalogueEntry], enrichment: EnrichmentTable | None, stream: StreamConfig, mode: str) -> MergedCatalogue:
    """cameras.json entries + enrichment rows + stream credentials → `SourceCamera`s ready for the importer.

    - `external_id` = id verbatim, `name` = name verbatim (numeric prefix kept).
    - Department/lat/lon/district/police station/type from the enrichment row keyed by external_id; a camera
      without a row gets no coordinates, `location_confidence=None`, department UNASSIGNED and a warning.
    - `metadata.enrichment` carries confidence, source and notes for the drawer/map; `metadata.catalogue` says
      which catalogue mode produced the row.
    """
    out = MergedCatalogue(cameras=[])
    rows = enrichment.rows if enrichment else {}
    for i, e in enumerate(entries, start=1):
        r = rows.get(e.external_id)
        meta: dict[str, Any] = {"catalogue": {"source": "sentinel_portal", "mode": mode}}
        cam = SourceCamera(
            external_id=e.external_id,
            name=e.name,
            rtsp_url=build_rtsp_url(stream, e.external_id) if stream.configured else None,
            whep_url=build_whep_url(stream, e.external_id) if stream.configured else None,
            hls_url=build_hls_url(stream, e.external_id) if stream.hls_base else None,
        )
        if r is None:
            out.missing_enrichment.append(e.external_id)
            out.warnings.append({"row": i, "external_id": e.external_id, "field": "enrichment", "message": "no enrichment row: no coordinates, department UNASSIGNED"})
            meta["enrichment"] = None
        else:
            cam.lat, cam.lon = r.lat, r.lon
            cam.district = r.district
            cam.department_code = r.department_code
            cam.type = camera_type_from_guess(r.camera_type_guess)
            if r.camera_type_guess and cam.type is None:
                out.warnings.append({"row": i, "external_id": e.external_id, "field": "type", "message": f"unknown camera_type_guess {r.camera_type_guess!r}; type left as ip"})
            if r.city:
                cam.address = f"{r.city}, {r.district}" if r.district and r.district.lower() not in r.city.lower() else r.city
            cam.extra["police_station"] = r.police_station
            cam.extra["location_confidence"] = r.location_confidence
            if r.lat is None:
                out.warnings.append({"row": i, "external_id": e.external_id, "field": "lat", "message": "enrichment row has no coordinates"})
            if r.name and r.name != e.name:
                out.warnings.append({"row": i, "external_id": e.external_id, "field": "name", "message": "enrichment name differs from cameras.json (catalogue name kept)"})
            meta["enrichment"] = {
                "confidence": r.location_confidence,
                "source": r.location_source,
                "notes": r.notes,
                "city": r.city,
                "type_guess": r.camera_type_guess,
                "department_guess": r.department_code,
                "name_matches_catalogue": (r.name == e.name) if r.name else None,
            }
        cam.extra["metadata"] = meta
        out.cameras.append(cam)
    return out


def source_camera_row(cam: SourceCamera) -> dict[str, Any]:
    """`SourceCamera` → importer row dict (only present keys, so an update leaves untouched fields alone)."""
    row = cam.as_row()
    for k, v in cam.extra.items():
        if v is not None:
            row[k] = v
    return row
