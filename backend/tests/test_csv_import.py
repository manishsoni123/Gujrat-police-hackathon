"""CSV import validation and error report (CONTRACT §5.2, §12.1) – DB-free layer."""

from __future__ import annotations

from app.schemas.cameras import CameraImportRow, normalise_codec, normalise_resolution, parse_bool
from app.services.camera_importer import validate_row
from app.services.csv_importer import (
    CAMERA_TEMPLATE_HEADER,
    camera_template_csv,
    error_report_csv,
    parse_camera_csv,
    preview_validate_cameras,
    watchlist_template_csv,
)

CONTRACT_HEADER = (
    "external_id,name,department_code,type,ownership,lat,lon,address,district,police_station,ward,rtsp_url,codec,"
    "resolution,fps,storage_location,retention_days,install_date,vendor,model,heading_deg,fov_deg,connectivity_type,"
    "bandwidth_kbps,vms_platform,nvr_id,maintenance_status,amc_vendor,amc_expiry,anpr_enabled,record_enabled"
)


def test_template_header_matches_contract() -> None:
    assert ",".join(CAMERA_TEMPLATE_HEADER) == CONTRACT_HEADER
    tpl = camera_template_csv().splitlines()
    assert tpl[0] == CONTRACT_HEADER
    assert len(tpl) == 3  # header + two example rows
    wl = watchlist_template_csv().splitlines()
    assert wl[0] == "plate,entity_type,name,reason,priority,source,notes,expires_at,is_active"


def test_sample_file_has_exactly_the_contract_errors(sample_cameras_csv: str) -> None:
    result = preview_validate_cameras(sample_cameras_csv)
    assert result["rows_total"] == 10
    assert result["header_error"] is None
    assert result["unknown_columns"] == []
    errors = result["errors"]
    assert [(e["row"], e["field"]) for e in errors] == [(4, "lat"), (9, "external_id"), (9, "name")]
    assert errors[0]["message"].startswith("must be between -90 and 90")
    assert "95.0" in errors[0]["message"]
    assert errors[1]["message"] == "duplicate external_id in file (first seen at row 2)"
    assert errors[2]["message"] == "field required"
    assert result["valid"] == 8


def test_parse_keeps_row_numbers_lines_and_present_fields(sample_cameras_csv: str) -> None:
    parsed = parse_camera_csv(sample_cameras_csv)
    assert parsed.row_numbers == list(range(1, 11))
    assert parsed.rows[0]["external_id"] == "CSV-001"
    assert parsed.original_lines[3].startswith("CSV-004")
    # blank cells are '' and not "present" (leave-unchanged semantics on update)
    assert parsed.rows[0]["ward"] == ""
    assert "ward" not in parsed.present_fields[0]
    assert "rtsp_url" not in parsed.present_fields[0]
    assert "amc_expiry" in parsed.present_fields[0]


def test_bom_any_column_order_and_unknown_columns() -> None:
    text = "﻿name,external_id,colour,lat,lon\nGate,X-1,red,23.2,72.6\n"
    parsed = parse_camera_csv(text)
    assert parsed.header_error is None
    assert parsed.unknown_columns == ["colour"]
    assert parsed.rows[0] == {"name": "Gate", "external_id": "X-1", "lat": "23.2", "lon": "72.6"}
    missing = parse_camera_csv("name,lat\nGate,1\n")
    assert missing.header_error and "external_id" in missing.header_error
    assert parse_camera_csv("").header_error == "empty file"


def test_row_validation_rules() -> None:
    base = {"external_id": "A", "name": "Cam"}
    row, issues = validate_row({**base, "lat": "23.2", "lon": "72.6", "codec": "hevc", "resolution": "1920×1080", "fps": "25", "type": "PTZ"}, 1, None)
    assert issues == [] and row is not None
    assert row.codec == "H265" and row.resolution == "1920x1080" and row.fps == 25 and row.type == "ptz"

    _, issues = validate_row({**base, "lat": "23.2"}, 2, None)
    assert issues and issues[0].field == "lat" and "both" in issues[0].message

    _, issues = validate_row({**base, "rtsp_url": "http://x"}, 3, None)
    assert issues[0].field == "rtsp_url"

    _, issues = validate_row({**base, "type": "thermal"}, 4, None)
    assert issues[0].field == "type"

    _, issues = validate_row({**base, "fps": "0"}, 5, None)
    assert issues[0].field == "fps"

    _, issues = validate_row({**base, "install_date": "10/05/2017"}, 6, None)
    assert issues[0].field == "install_date"

    _, issues = validate_row({"external_id": "", "name": ""}, 7, None)
    assert {i.field for i in issues} == {"external_id", "name"}
    assert all(i.message == "field required" for i in issues)

    row, _ = validate_row({**base, "district": "devbhoomi dwarka", "anpr_enabled": "yes", "record_enabled": "0"}, 8, None)
    assert row.district == "Devbhumi Dwarka" and row.anpr_enabled is True and row.record_enabled is False


def test_gujarat_bbox_warning_helper() -> None:
    inside = CameraImportRow(external_id="a", name="n", lat=23.2, lon=72.6)
    outside = CameraImportRow(external_id="a", name="n", lat=28.6, lon=77.2)
    assert not inside.outside_gujarat()
    assert outside.outside_gujarat()
    assert not CameraImportRow(external_id="a", name="n").outside_gujarat()


def test_codec_resolution_bool_helpers() -> None:
    assert normalise_codec("h.264") == "H264"
    assert normalise_codec("HEVC") == "H265"
    assert normalise_codec("mjpg") == "MJPEG"
    assert normalise_codec("") == "UNKNOWN"
    assert normalise_codec("vp9") == "UNKNOWN"
    assert normalise_resolution({"width": 1280, "height": 720}) == "1280x720"
    assert normalise_resolution([704, 576]) == "704x576"
    assert normalise_resolution(None) is None
    assert parse_bool("live") is True and parse_bool("offline") is False and parse_bool(None) is None
    assert parse_bool(1) is True and parse_bool(True) is True


def test_error_report_csv_shape() -> None:
    errors = [{"row": 4, "external_id": "CSV-004", "field": "lat", "message": "must be between -90 and 90 (got 95.0)"}]
    csv_text = error_report_csv(errors, {4: "CSV-004,Jamnagar,..."})
    lines = csv_text.splitlines()
    assert lines[0] == "row,external_id,field,message,original_line"
    assert lines[1].startswith("4,CSV-004,lat,")
    assert lines[1].endswith('"CSV-004,Jamnagar,..."')
