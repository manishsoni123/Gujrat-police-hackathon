"""CSV parsing for camera and watchlist imports (CONTRACT §5.2, §5.11, §12.1, §12.2).

`parse_camera_csv` is pure (no DB) so `tests/test_csv_import.py` can run without Postgres.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any

CAMERA_TEMPLATE_HEADER = [
    "external_id", "name", "department_code", "type", "ownership", "lat", "lon", "address", "district",
    "police_station", "ward", "rtsp_url", "codec", "resolution", "fps", "storage_location", "retention_days",
    "install_date", "vendor", "model", "heading_deg", "fov_deg", "connectivity_type", "bandwidth_kbps",
    "vms_platform", "nvr_id", "maintenance_status", "amc_vendor", "amc_expiry", "anpr_enabled", "record_enabled",
]

CAMERA_TEMPLATE_EXAMPLES = [
    ["GSRTC-101", "Mehsana Depot Gate", "GSRTC", "bullet", "govt_dept", "23.5880", "72.3690", "ST Depot, Mehsana",
     "Mehsana", "Mehsana City", "", "rtsp://192.168.10.21:554/stream1", "H264", "1920x1080", "25", "NVR at depot",
     "30", "2022-04-01", "Hikvision", "DS-2CD2043G2", "90", "90", "lan", "4096", "Hikvision NVR", "NVR-MSN-01",
     "ok", "Depot AMC", "2027-03-31", "false", "false"],
    ["PS-SEC7-02", "Sector 7 PS Entrance", "POLICE", "dome", "govt_dept", "23.2260", "72.6450", "Sector 7 Police Station",
     "Gandhinagar", "Sector 7", "", "", "H265", "1280x720", "15", "DVR at PS", "15", "2019-08-15", "CP Plus",
     "CP-UNC-DA21L3", "", "", "4g", "2000", "none", "DVR-GNR-07", "ok", "", "", "false", "false"],
]

WATCHLIST_TEMPLATE_HEADER = ["plate", "entity_type", "name", "reason", "priority", "source", "notes", "expires_at", "is_active"]
WATCHLIST_TEMPLATE_EXAMPLES = [
    ["GJ 01 AB 1234", "vehicle", "White Maruti Swift – FIR 123/2026", "stolen", "critical", "own", "Reported stolen 2026-08-30", "", "true"],
    ["", "person", "Missing minor – Surat", "missing", "high", "own", "FRS roadmap – no plate", "", "true"],
]

MAX_ROWS = 20_000


@dataclass
class ParsedCsv:
    header: list[str]
    rows: list[dict[str, Any]] = field(default_factory=list)  # keys = header columns (blank → "")
    row_numbers: list[int] = field(default_factory=list)  # 1-based data row numbers
    original_lines: list[str] = field(default_factory=list)
    present_fields: list[set[str]] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)
    header_error: str | None = None


def decode_csv_bytes(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def parse_csv(text: str, known_columns: list[str], required: tuple[str, ...]) -> ParsedCsv:
    """Generic tolerant parser: any column order, unknown columns reported, blank cells = ''."""
    reader = csv.reader(io.StringIO(text))
    try:
        header_raw = next(reader)
    except StopIteration:
        return ParsedCsv(header=[], header_error="empty file")
    header = [h.strip().lstrip("﻿").lower() for h in header_raw]
    missing = [c for c in required if c not in header]
    parsed = ParsedCsv(header=header)
    if missing:
        parsed.header_error = f"missing required column(s): {', '.join(missing)}"
        return parsed
    parsed.unknown_columns = [h for h in header if h and h not in known_columns]
    lines = text.splitlines()
    for line_idx, rec in enumerate(reader, start=1):
        if not any(cell.strip() for cell in rec):
            continue
        if len(parsed.rows) >= MAX_ROWS:
            parsed.header_error = f"file has more than {MAX_ROWS} rows"
            break
        row: dict[str, Any] = {}
        present: set[str] = set()
        for i, col in enumerate(header):
            if not col or col not in known_columns:
                continue
            val = rec[i].strip() if i < len(rec) else ""
            row[col] = val
            if val != "":
                present.add(col)
        parsed.rows.append(row)
        parsed.row_numbers.append(len(parsed.rows))
        parsed.original_lines.append(lines[line_idx] if line_idx < len(lines) else "")
        parsed.present_fields.append(present)
    return parsed


def parse_camera_csv(text: str) -> ParsedCsv:
    return parse_csv(text, CAMERA_TEMPLATE_HEADER, ("external_id", "name"))


def parse_watchlist_csv(text: str) -> ParsedCsv:
    return parse_csv(text, WATCHLIST_TEMPLATE_HEADER, ("entity_type", "reason"))


def render_csv(header: list[str], rows: list[list[Any]], trailer: str | None = None) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    if trailer:
        buf.write(trailer.rstrip("\n") + "\n")
    return buf.getvalue()


def camera_template_csv() -> str:
    return render_csv(CAMERA_TEMPLATE_HEADER, CAMERA_TEMPLATE_EXAMPLES)


def watchlist_template_csv() -> str:
    return render_csv(WATCHLIST_TEMPLATE_HEADER, WATCHLIST_TEMPLATE_EXAMPLES)


def error_report_csv(errors: list[dict[str, Any]], original_lines: dict[int, str]) -> str:
    rows = [[e.get("row"), e.get("external_id"), e.get("field"), e.get("message"), original_lines.get(e.get("row"), "")] for e in errors]
    return render_csv(["row", "external_id", "field", "message", "original_line"], rows)


def preview_validate_cameras(text: str) -> dict[str, Any]:
    """DB-free validation summary used by tests: pydantic errors + in-file duplicates."""
    from app.services.camera_importer import RowIssue, validate_row

    parsed = parse_camera_csv(text)
    errors: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    valid = 0
    for row_no, raw in zip(parsed.row_numbers, parsed.rows):
        ext = raw.get("external_id", "")
        _, issues = validate_row(raw, row_no, None)
        if ext:
            if ext in seen:
                issues.insert(0, RowIssue(row_no, ext, "external_id", f"duplicate external_id in file (first seen at row {seen[ext]})"))
            else:
                seen[ext] = row_no
        if issues:
            errors.extend(i.as_dict() for i in issues)
        else:
            valid += 1
    return {
        "rows_total": len(parsed.rows),
        "valid": valid,
        "errors": errors,
        "header_error": parsed.header_error,
        "unknown_columns": parsed.unknown_columns,
    }
