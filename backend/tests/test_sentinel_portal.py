"""Organiser ("Sentinel") sandbox adapter (CONTRACT Amendments 2026-09-05): cameras.json parsing, enrichment
merge (incl. a missing row), stream-URL building (real format, password containing '@'), credential masking
everywhere, probe helpers (fps sanity, error classes, scrubbing) and the health-poller probe budget. DB-free."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.adapters.base import ProbeResult, SourceCamera
from app.adapters.rtsp import classify_probe_error, sane_fps
from app.adapters.sentinel_portal import discover_login_form
from app.db.models import Camera
from app.schemas.cameras import CameraImportRow
from app.services import serializers
from app.services import settings_service as cfg
from app.services.camera_importer import merged_metadata, validate_row
from app.services.health_poller import probe_target, select_probe_candidates
from app.services.mediamtx_client import needs_transcode, play_path, whep_supported
from app.services.sentinel_catalogue import (
    CatalogueParseError,
    StreamConfig,
    build_hls_url,
    build_rtsp_url,
    build_whep_url,
    camera_type_from_guess,
    merge_catalogue,
    parse_cameras_json,
    parse_enrichment_csv,
    source_camera_row,
)
from app.services.sentinel_import import apply_probe

REPO_MEDIA = Path(__file__).resolve().parents[2] / "media"
CAMERAS_JSON = '[\n{"id":"cam01","name":"01 Chiman bhai Bridge"}\n,{"id":"cam02","name":"02 Janpath"}\n,{"id":"cam14","name":"14 Delight RLVD"}\n,{"id":"cam99","name":"99 Unknown place"}\n]'
ENRICHMENT_CSV = """external_id,name,lat,lon,district,city,police_station,department_code,camera_type_guess,location_confidence,location_source,notes
cam01,"01 Chiman bhai Bridge",23.07112,72.58722,Ahmedabad,Ahmedabad,Sabarmati,POLICE,bullet,exact,osm-overpass,"OSM way 37722606"
cam02,"02 Janpath",23.08826,72.59155,Ahmedabad,Ahmedabad (Motera/Sabarmati),Sabarmati,,ptz,approx,tripadvisor-listing,"no department known"
cam14,"14 Delight RLVD",23.03778,72.58795,Ahmedabad,Ahmedabad (Dariyapur),Dariyapur,POLICE,rlvd,guess,osm-nominatim,"read as Delhi Gate"
"""
STREAM = StreamConfig(host="103.250.160.189", rtsp_port=8554, whep_port=8889, hls_base="https://cctv.corp8.cloud", email="team@example.in", password="s3cret-pass")


# ---- cameras.json ------------------------------------------------------------------------------------------------

def test_parse_cameras_json_keeps_ids_and_names_verbatim() -> None:
    entries = parse_cameras_json(CAMERAS_JSON.encode())
    assert [e.external_id for e in entries] == ["cam01", "cam02", "cam14", "cam99"]
    assert entries[0].name == "01 Chiman bhai Bridge"  # numeric prefix kept, nothing stripped
    # wrapper object and BOM are tolerated; ids are never upper-cased (MediaMTX paths are case-sensitive)
    wrapped = ("﻿" + json.dumps({"cameras": [{"id": "cam03", "name": "x"}]})).encode("utf-8")
    assert parse_cameras_json(wrapped)[0].external_id == "cam03"


def test_parse_cameras_json_rejects_bad_input() -> None:
    with pytest.raises(CatalogueParseError):
        parse_cameras_json(b"")
    with pytest.raises(CatalogueParseError):
        parse_cameras_json(b"[{")
    with pytest.raises(CatalogueParseError):
        parse_cameras_json(b'[{"name": "no id"}]')
    with pytest.raises(CatalogueParseError):
        parse_cameras_json(b'[{"id": "cam01"}, {"id": "cam01"}]')
    with pytest.raises(CatalogueParseError):
        parse_cameras_json(b'[{"id": "cam 01/x"}]')


@pytest.mark.skipif(not (REPO_MEDIA / "cameras.json").is_file(), reason="repo media/cameras.json not mounted")
def test_saved_organiser_catalogue_parses() -> None:
    entries = parse_cameras_json((REPO_MEDIA / "cameras.json").read_bytes())
    assert len(entries) == 30 and entries[0].external_id == "cam01" and entries[-1].external_id == "cam30"
    table = parse_enrichment_csv((REPO_MEDIA / "cameras_enrichment.csv").read_text(encoding="utf-8-sig"))
    assert set(table.rows) == {e.external_id for e in entries}
    assert table.confidence_counts == {"exact": 7, "approx": 16, "guess": 7}
    merged = merge_catalogue(entries, table, STREAM, "file")
    assert merged.missing_enrichment == [] and all(c.lat is not None for c in merged.cameras)


# ---- enrichment ----------------------------------------------------------------------------------------------------

def test_enrichment_merge_including_missing_row() -> None:
    entries = parse_cameras_json(CAMERAS_JSON)
    table = parse_enrichment_csv(ENRICHMENT_CSV)
    assert table.warnings == [] and table.unknown_columns == []
    merged = merge_catalogue(entries, table, STREAM, "upload")
    by_id = {c.external_id: c for c in merged.cameras}
    c1 = by_id["cam01"]
    assert (c1.lat, c1.lon, c1.district, c1.department_code, c1.type) == (23.07112, 72.58722, "Ahmedabad", "POLICE", "bullet")
    assert c1.extra["police_station"] == "Sabarmati" and c1.extra["location_confidence"] == "exact"
    assert c1.extra["metadata"]["enrichment"]["source"] == "osm-overpass" and c1.extra["metadata"]["catalogue"]["mode"] == "upload"
    # blank department → no department_code in the row → importer maps it to UNASSIGNED
    assert by_id["cam02"].department_code is None and "department_code" not in source_camera_row(by_id["cam02"])
    assert by_id["cam02"].address == "Ahmedabad (Motera/Sabarmati)"  # city already names the district
    # RLVD guess maps to the ANPR camera type; guess confidence survives
    assert by_id["cam14"].type == "anpr" and by_id["cam14"].extra["location_confidence"] == "guess"
    # missing enrichment row: no coordinates, no department, a warning, metadata.enrichment = None
    c99 = by_id["cam99"]
    assert c99.lat is None and c99.department_code is None and c99.extra.get("location_confidence") is None
    assert merged.missing_enrichment == ["cam99"]
    assert any(w["external_id"] == "cam99" and w["field"] == "enrichment" for w in merged.warnings)
    # every row validates as a CameraImportRow (district canonicalised, confidence enum, metadata object)
    for c in merged.cameras:
        parsed, issues = validate_row(source_camera_row(c), 1, 0)
        assert issues == [] and parsed is not None
        assert parsed.metadata["catalogue"]["source"] == "sentinel_portal"
    assert CameraImportRow.model_validate(source_camera_row(c1)).location_confidence == "exact"


def test_enrichment_csv_tolerates_bad_rows() -> None:
    text = "external_id,lat,lon,location_confidence,extra\ncam01,abc,72.5,exact,x\ncam02,23.0,,approx,y\ncam03,23.0,72.0,maybe,z\n,1,2,exact,\ncam01,1,2,exact,dup\n"
    table = parse_enrichment_csv(text)
    assert table.unknown_columns == ["extra"]
    assert table.rows["cam01"].lat is None and table.rows["cam02"].lat is None
    assert table.rows["cam03"].location_confidence == "guess"
    assert len(table.rows) == 3 and len(table.warnings) == 5
    with pytest.raises(CatalogueParseError):
        parse_enrichment_csv("id,name\ncam01,x\n")


def test_camera_type_guess_mapping() -> None:
    assert camera_type_from_guess("rlvd") == "anpr" and camera_type_from_guess("PTZ") == "ptz"
    assert camera_type_from_guess("thermal") is None and camera_type_from_guess(None) is None


# ---- stream URLs ---------------------------------------------------------------------------------------------------

def test_url_building_real_format_and_password_with_at() -> None:
    assert build_rtsp_url(STREAM, "cam01") == "rtsp://team%40example.in:s3cret-pass@103.250.160.189:8554/stream/cam01"
    assert build_whep_url(STREAM, "cam01") == "http://team%40example.in:s3cret-pass@103.250.160.189:8889/stream/cam01/whep"
    assert build_hls_url(STREAM, "cam01") == "https://cctv.corp8.cloud/cam01/index.m3u8"
    tricky = StreamConfig(host="h", email="a.b@c.in", password="p@ss:w/rd#1 x")
    url = build_rtsp_url(tricky, "cam07")
    assert url == "rtsp://a.b%40c.in:p%40ss%3Aw%2Frd%231%20x@h:8554/stream/cam07"
    from urllib.parse import unquote, urlsplit

    parts = urlsplit(url)
    assert unquote(parts.username) == "a.b@c.in" and unquote(parts.password) == "p@ss:w/rd#1 x" and parts.hostname == "h" and parts.path == "/stream/cam07"
    # ids are used verbatim (never upper-cased): /stream/CAM03 would be "path not configured"
    assert build_rtsp_url(STREAM, "cam03").endswith("/stream/cam03")
    assert not StreamConfig(host="h", email="e").configured and STREAM.configured


# ---- masking -------------------------------------------------------------------------------------------------------

def test_mask_url_covers_every_scheme_and_odd_passwords() -> None:
    m = serializers.mask_url
    assert m("rtsp://team%40example.in:s3cret-pass@103.250.160.189:8554/stream/cam01") == "rtsp://team%40example.in:***@103.250.160.189:8554/stream/cam01"
    assert m("http://team%40example.in:s3cret-pass@103.250.160.189:8889/stream/cam01/whep") == "http://team%40example.in:***@103.250.160.189:8889/stream/cam01/whep"
    assert m("rtsp://user:p@ss@host/x") == "rtsp://user:***@host/x"  # unencoded '@' inside the password
    assert m("rtsps://u:p%40ss@h:8554/a?b=c") == "rtsps://u:***@h:8554/a?b=c"
    assert m("https://cctv.corp8.cloud/cam01/index.m3u8") == "https://cctv.corp8.cloud/cam01/index.m3u8"
    assert m("rtsp://mediamtx:8554/stream/1") == "rtsp://mediamtx:8554/stream/1"
    assert m("rtsp://user@host/x") == "rtsp://user@host/x" and m(None) is None and m("") == ""
    # the old signature masks for admins too now
    assert serializers.mask_rtsp("rtsp://u:p@h/x", True) == "rtsp://u:***@h/x"
    assert serializers.is_masked_form("rtsp://u:***@h/x", "rtsp://u:p@h/x") and not serializers.is_masked_form("rtsp://u:p@h/x", "rtsp://u:p@h/x")
    assert not serializers.is_masked_form("rtsp://u:***@h/y", "rtsp://u:p@h/x")


def test_mask_secrets_in_free_text() -> None:
    # plain, URL-encoded (`@` → %40, as ffmpeg echoes the URL it was given) and fully encoded spellings
    err = "rtsp://team%40example.in:s3cret%40pass@103.250.160.189:8554/stream/cam03: Server returned 404 Not Found (pw s3cret@pass / s3cret%40pass / %73%33%63%72%65%74%40%70%61%73%73)"
    out = serializers.mask_secrets_in_text(err, ("s3cret@pass",))
    assert "s3cret@pass" not in out and "s3cret%40pass" not in out and "%73%33" not in out and "404 Not Found" in out and "***" in out
    assert out.startswith("rtsp://team%40example.in:***@103.250.160.189:8554/stream/cam03")
    # a MediaMTX error body echoing the path source is scrubbed too (mediamtx_client._scrub)
    from app.services.mediamtx_client import _scrub

    assert _scrub("MediaMTX: 400 invalid source 'rtsp://u:p%40w@h:8554/stream/cam01'") == "MediaMTX: 400 invalid source 'rtsp://u:***@h:8554/stream/cam01'"


def test_csv_source_cameras_are_masked_for_admins_too() -> None:
    """Regression: rows loaded through the plain CSV import (source='csv') with credentials embedded in the URL
    were returned unmasked to admins by the list/detail/export paths; every source and every role is masked now."""
    url = "rtsp://team%40example.in:Very%40Secret-1@103.250.160.189:8554/stream/cam07"
    cam = Camera(
        id=41, source="csv", external_id="cam07", name="07 hero-showroom-gir-somnath", department_id=1, type="ip", ownership="govt_dept",
        rtsp_url=url, whep_url="http://team%40example.in:Very%40Secret-1@103.250.160.189:8889/stream/cam07/whep", hls_url="https://cctv.corp8.cloud/cam07/index.m3u8",
        codec="H264", status="online", maintenance_status="ok", anpr_enabled=True, record_enabled=True, created_via="csv",
        created_at=datetime.now(tz=timezone.utc), updated_at=datetime.now(tz=timezone.utc),
    )
    for is_admin in (True, False):
        blob = json.dumps(serializers.camera_full(cam, is_admin))
        assert "Very%40Secret-1" not in blob and "Very@Secret-1" not in blob
        assert serializers.mask_rtsp(cam.rtsp_url, is_admin) == "rtsp://team%40example.in:***@103.250.160.189:8554/stream/cam07"  # CSV export column
    assert "Very%40Secret-1" not in json.dumps(serializers.camera_diff(cam))  # audit before/after
    assert "Very%40Secret-1" not in json.dumps(serializers.camera_summary(cam))  # WS / alert payloads carry the summary only
    # the relay still receives the real URL (MediaMTX path creation) — the only consumer of the raw value
    from app.services.mediamtx_client import source_path_body

    assert source_path_body(cam.rtsp_url, False)["source"] == url and source_path_body(cam.rtsp_url, False)["rtspTransport"] == "tcp"


def test_pick_existing_matches_across_sources_without_duplicates() -> None:
    from app.services.camera_importer import pick_existing

    csv_row = Camera(id=40, source="csv", external_id="cam01", name="a", department_id=1, status="online")
    api_row = Camera(id=55, source="api", external_id="cam01", name="a", department_id=1, status="unknown")
    sandbox_row = Camera(id=70, source="sandbox", external_id="cam02", name="b", department_id=1, status="unknown")
    retired_row = Camera(id=12, source="manual", external_id="cam03", name="c", department_id=1, status="retired")
    live_row = Camera(id=90, source="manual", external_id="cam03", name="c", department_id=1, status="online")
    picked = pick_existing([api_row, csv_row, sandbox_row, retired_row, live_row], "sandbox")
    assert picked["cam01"] is csv_row  # lowest id among the non-sandbox candidates: the CSV row keeps its relay path
    assert picked["cam02"] is sandbox_row  # a row already of the importing source always wins
    assert picked["cam03"] is live_row  # a retired duplicate never shadows a live one
    assert pick_existing([], "sandbox") == {}


def test_camera_serializers_mask_urls_for_every_role_and_in_audit_diffs() -> None:
    cam = Camera(
        id=5, source="sandbox", external_id="cam01", name="01 Chiman bhai Bridge", department_id=1, type="bullet", ownership="govt_dept",
        rtsp_url=build_rtsp_url(STREAM, "cam01"), whep_url=build_whep_url(STREAM, "cam01"), hls_url=build_hls_url(STREAM, "cam01"),
        codec="H264", status="unknown", maintenance_status="ok", anpr_enabled=False, record_enabled=False, created_via="sandbox",
        created_at=datetime.now(tz=timezone.utc), updated_at=datetime.now(tz=timezone.utc), location_confidence="exact",
        meta={"enrichment": {"confidence": "exact", "notes": "OSM way"}},
    )
    for is_admin in (True, False):
        full = serializers.camera_full(cam, is_admin)
        blob = json.dumps(full)
        assert "s3cret-pass" not in blob
        assert full["rtsp_url"].startswith("rtsp://team%40example.in:***@") and full["whep_url"].startswith("http://team%40example.in:***@")
        assert full["hls_url"] == "https://cctv.corp8.cloud/cam01/index.m3u8"
        assert full["location_confidence"] == "exact" and full["metadata"]["enrichment"]["notes"] == "OSM way"
    diff = serializers.camera_diff(cam)
    assert "s3cret-pass" not in json.dumps(diff) and diff["rtsp_url"].startswith("rtsp://team%40example.in:***@") and diff["location_confidence"] == "exact"


# ---- probes --------------------------------------------------------------------------------------------------------

def test_sane_fps_drops_bogus_rates() -> None:
    assert sane_fps("25/1") == 25.0 and sane_fps("30000/1001") == 29.97 and sane_fps(50) == 50.0
    assert sane_fps("90000/1") is None and sane_fps("0/0") is None and sane_fps("abc") is None and sane_fps(None) is None and sane_fps(0.5) is None


def test_probe_error_classes() -> None:
    assert classify_probe_error("rtsp://h/stream/CAM03: Server returned 400 Bad Request") == "definitive"
    assert classify_probe_error("401 Unauthorized") == "definitive"
    assert classify_probe_error("timeout") == "timeout" and classify_probe_error("Connection timed out") == "timeout"
    assert classify_probe_error("Connection refused") == "network" and classify_probe_error(None) == "network"


def test_apply_probe_fills_stream_fields_and_metadata() -> None:
    ok = SourceCamera(external_id="cam06", name="06", rtsp_url="rtsp://x", extra={"location_confidence": "approx"})
    row = apply_probe(ok, ProbeResult(True, codec="H265", width=1920, height=1080, fps=None, profile="Main", duration_ms=10455), "2026-09-05T10:00:00.000Z")
    assert (ok.codec, ok.resolution, ok.fps, ok.live) == ("H265", "1920x1080", None, True) and row["transcode"] is True and row["live"] is True
    assert ok.extra["metadata"]["probe"]["kind"] == "ok" and source_camera_row(ok)["codec"] == "H265"
    gone = SourceCamera(external_id="cam31", name="31", rtsp_url="rtsp://x")
    apply_probe(gone, ProbeResult(False, error="Server returned 400 Bad Request"), "t")
    assert gone.live is False and "codec" not in source_camera_row(gone)
    slow = SourceCamera(external_id="cam09", name="09", rtsp_url="rtsp://x")
    apply_probe(slow, ProbeResult(False, error="timeout"), "t")
    assert slow.live is None and "live" not in source_camera_row(slow)  # unknown → the health poller keeps probing it


def test_merged_metadata_is_a_shallow_merge() -> None:
    cur = {"enrichment": {"confidence": "exact"}, "probe": {"ok": False}}
    assert merged_metadata(cur, {"probe": {"ok": True}}) == {"enrichment": {"confidence": "exact"}, "probe": {"ok": True}}
    assert merged_metadata(cur, {"probe": None}) == {"enrichment": {"confidence": "exact"}}
    assert merged_metadata(None, None) is None and merged_metadata(cur, None) == cur


# ---- health-poller probe budget ------------------------------------------------------------------------------------

def test_select_probe_candidates_paces_the_load() -> None:
    now = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)
    cands = [(1, None), (2, now - timedelta(seconds=60)), (3, now - timedelta(seconds=400)), (4, now - timedelta(seconds=10)), (5, now - timedelta(seconds=900)), (6, None)]
    probe_now, reuse = select_probe_candidates(cands, now, min_interval_s=300, max_per_tick=2)
    assert probe_now == [1, 6] and sorted(reuse) == [2, 3, 4, 5]  # never-probed first, then least recent
    probe_now, reuse = select_probe_candidates(cands, now, min_interval_s=300, max_per_tick=20)
    assert probe_now == [1, 6, 5, 3] and sorted(reuse) == [2, 4]  # inside the 5-minute window → reuse the last result
    assert select_probe_candidates([], now, 300, 6) == ([], [])


# ---- settings / portal ---------------------------------------------------------------------------------------------

def test_settings_registry_declares_sandbox_keys_with_secrets_masked() -> None:
    for key in ("catalogue.source", "catalogue.portal_url", "catalogue.portal_email", "catalogue.portal_password", "catalogue.enrichment_path", "sandbox.stream_host", "sandbox.rtsp_port", "sandbox.whep_port", "sandbox.stream_email", "sandbox.stream_password"):
        assert key in cfg.REGISTRY, key
    assert cfg.REGISTRY["sandbox.stream_password"].is_secret and cfg.REGISTRY["catalogue.portal_password"].is_secret
    assert cfg.REGISTRY["catalogue.source"].public and cfg.REGISTRY["catalogue.source"].validator("Sentinel_Portal") == "sentinel_portal"
    with pytest.raises(ValueError):
        cfg.REGISTRY["catalogue.source"].validator("real")
    with pytest.raises(ValueError):
        cfg.REGISTRY["sandbox.stream_host"].validator("rtsp://user:pw@host")
    assert cfg.REGISTRY["sandbox.stream_host"].validator(" 103.250.160.189 ") == "103.250.160.189"
    assert cfg.validate_values({"sandbox.stream_password": cfg.MASK}) == {}  # "unchanged" semantics


def test_discover_login_form_reads_field_names_and_csrf() -> None:
    page = """<html><body><form method="post" action="/auth/login"><input type="hidden" name="_token" value="abc123">
    <input type="email" name="email" placeholder="E-mail"><input type="password" name="password"><button>Login</button></form></body></html>"""
    form = discover_login_form(page, "https://cctv.corp8.cloud/auth/login")
    assert form == {"action": "https://cctv.corp8.cloud/auth/login", "method": "post", "fields": {"_token": "abc123"}, "user_field": "email", "password_field": "password"}
    assert discover_login_form("<html><form><input name='q'></form></html>", "https://x/login") is None


# ---- health-poller probe target / relay start timeout (real sandbox, 5 Sept) ------------------------------------

def test_probe_target_probes_external_sources_directly_with_the_long_cap() -> None:
    from app.core.config import settings as env

    sandbox = build_rtsp_url(STREAM, "cam13")
    url, t = probe_target(sandbox, 73, local_timeout_s=6, external_timeout_s=30)
    assert url == sandbox and t == 30.0  # direct, one short connection, 30 s cap (the sandbox answers in 4-38 s)
    # relay policy (Amendments 2026-09-05): HEALTH_PROBE_VIA=relay probes through cam_<id> with cap + 15 s instead
    url, t = probe_target(sandbox, 73, 6, 30, source="sandbox", via="relay")
    assert url == f"{env.MEDIAMTX_RTSP_URL}/cam_73" and t == 45.0
    url, t = probe_target(f"{env.MEDIAMTX_RTSP_URL}/stream/3", 3, 6, 30)
    assert url == f"{env.MEDIAMTX_RTSP_URL}/cam_3" and t == 6.0  # local loop: through the relay, short cap
    url, t = probe_target(f"{env.MEDIAMTX_RTSP_LOCAL_URL}/own_gate", 91, 6, 30)
    assert url.endswith("/cam_91") and t == 6.0
    assert probe_target(None, 5, 6, 30) == (f"{env.MEDIAMTX_RTSP_URL}/cam_5", 6.0)


def test_relay_start_timeout_grows_for_external_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings as env
    from app.services import mediamtx_client as mc

    monkeypatch.setattr(cfg, "get", lambda key: 30 if key == "sandbox.probe_timeout_s" else None)
    assert mc.is_external_source(build_rtsp_url(STREAM, "cam01")) and not mc.is_external_source(f"{env.MEDIAMTX_RTSP_URL}/stream/1") and not mc.is_external_source(None)
    assert mc.source_path_body(build_rtsp_url(STREAM, "cam01"), False)["sourceOnDemandStartTimeout"] == "40s"
    assert mc.source_path_body(f"{env.MEDIAMTX_RTSP_URL}/stream/1", False)["sourceOnDemandStartTimeout"] == "15s"
    monkeypatch.setattr(cfg, "get", lambda key: 3 if key == "sandbox.probe_timeout_s" else None)
    assert mc.on_demand_start_timeout_s(build_rtsp_url(STREAM, "cam01")) == 15  # never below the contract's 15 s


# ---- secrets never leave the API: import summary, audit row, probe errors -----------------------------------------

def test_import_audit_summary_carries_counts_only() -> None:
    from app.services.sentinel_import import audit_summary

    summary = {
        "source": "sentinel_portal", "fetched": 30, "added": 0, "updated": 30, "unchanged": 0, "source_url": "/app/media/cameras.json",
        "errors": [{"row": 1, "message": "x"}], "warnings": [{"row": 0, "message": "y"}], "missing_enrichment": ["cam99"],
        "probes": [{"external_id": "cam01", "error": "rtsp://u:***@h/stream/cam01: timeout"}], "probe_ok": 29,
    }
    out = audit_summary(summary)
    assert out["errors"] == 1 and out["warnings"] == 1 and out["missing_enrichment"] == 1 and out["probe_ok"] == 29
    assert "probes" not in out and "cam01" not in json.dumps(out)


@pytest.mark.skipif(__import__("shutil").which("ffprobe") is None, reason="ffprobe not installed")
@pytest.mark.asyncio
async def test_ffprobe_error_text_is_scrubbed_of_the_password() -> None:
    """ffmpeg echoes the input URL (credentials included) in its error line; the probe result must not."""
    from app.adapters.rtsp import ffprobe_details

    secret = "Very@Secret-1"
    url = build_rtsp_url(StreamConfig(host="127.0.0.1", rtsp_port=1, email="team@example.in", password=secret), "cam01")
    res = await ffprobe_details(url, timeout_s=3, secrets=(secret,))
    assert res.ok is False and res.error
    blob = json.dumps(res.as_dict())
    assert secret not in blob and "Very%40Secret-1" not in blob and "Very%40Secret%2D1" not in blob
    # and the same scrub protects the import row (metadata.probe.error) and the per-camera probe table
    cam = SourceCamera(external_id="cam01", name="01", rtsp_url=url)
    row = apply_probe(cam, res, "t")
    assert secret not in json.dumps(row) and secret not in json.dumps(source_camera_row(cam))


def test_camera_import_row_masking_survives_the_update_path() -> None:
    """PUT /cameras/{id} echoing the masked URL keeps the stored secret; a new plain URL replaces it."""
    stored = build_rtsp_url(STREAM, "cam01")
    assert serializers.is_masked_form(serializers.mask_url(stored), stored)
    assert not serializers.is_masked_form("rtsp://team%40example.in:new-pass@103.250.160.189:8554/stream/cam01", stored)


# ---- browser start mode (B-frame H.264 cannot go over WebRTC) ------------------------------------------------------

def test_probe_records_b_frames_and_streams_prefer_hls_for_them() -> None:
    cam = SourceCamera(external_id="cam24", name="24", rtsp_url="rtsp://x")
    row = apply_probe(cam, ProbeResult(True, codec="H264", width=960, height=576, fps=12.0, profile="High", b_frames=2), "t")
    assert row["b_frames"] == 2 and cam.extra["metadata"]["probe"]["b_frames"] == 2
    assert row["transcode"] is True  # B-frame H.264 gets the _h264 re-encode like H.265
    # the browser path is the zerolatency re-encode, which WebRTC can carry
    assert needs_transcode("H264", cam.extra["metadata"]) is True and play_path(84, "H264", cam.extra["metadata"]) == "cam_84_h264"
    assert whep_supported("H264", cam.extra["metadata"]) is True
    assert needs_transcode("H264", {"probe": {"b_frames": 0}}) is False and play_path(61, "H264", {"probe": {"b_frames": 0}}) == "cam_61"
    assert whep_supported("H264", {"probe": {"b_frames": 0}}) is True
    assert whep_supported("H264", {"probe": {"b_frames": None}}) is True and whep_supported("H264", None) is True
    assert needs_transcode("H265", None) is True and play_path(66, "H265") == "cam_66_h264"
    assert whep_supported("H265", {"probe": {"b_frames": 3}}) is True
    assert needs_transcode("H264", {"probe": {"b_frames": "garbage"}}) is False and whep_supported("H264", {"probe": {"b_frames": "garbage"}}) is True
