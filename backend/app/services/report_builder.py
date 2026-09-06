"""CSV (pandas) and PDF (reportlab) report generation with hash ledger (CONTRACT §5.10, §5.15, §5.8)."""

from __future__ import annotations

import hashlib
import io
import json
import logging
from datetime import datetime
from typing import Any

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.hashing import abs_path, write_bytes_hashed
from app.core.tz import fmt_ist, ist_stamp, utc_date_dir, utcnow
from app.db.models import ReportFile
from app.services.csv_importer import neutralise_cell
from app.services.plates import format_plate

log = logging.getLogger("sentinel.reports")

PRODUCT = settings.PRODUCT_NAME
VERSION = settings.APP_VERSION
WATERMARK_SUFFIX = "purpose-limited to law-enforcement use"

_styles = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=_styles["Heading1"], fontSize=16, spaceAfter=6)
H2 = ParagraphStyle("h2", parent=_styles["Heading2"], fontSize=12, spaceBefore=8, spaceAfter=4)
BODY = ParagraphStyle("body", parent=_styles["BodyText"], fontSize=8.5, leading=11)
SMALL = ParagraphStyle("small", parent=_styles["BodyText"], fontSize=7, leading=9, textColor=colors.HexColor("#4B5563"))
CELL = ParagraphStyle("cell", parent=_styles["BodyText"], fontSize=7, leading=8.5)


def csv_cell(v: Any) -> Any:
    """Uniform CSV spelling across every export: booleans are `true`/`false` (the camera CSV
    template spelling, so exports round-trip through the importer); strings are formula-neutralised
    (`neutralise_cell`, CONTRACT §10.3 Amendments) — `rows_hash` reuses this so the `#` trailer hash is
    computed on the escaped spelling that is actually written."""
    if isinstance(v, bool):
        return "true" if v else "false"
    return neutralise_cell(v)


def rows_hash(rows: list[list[Any]]) -> str:
    """SHA-256 over the rows exactly as `build_csv` writes them (same cell spelling)."""
    h = hashlib.sha256()
    for r in rows:
        h.update(("\x1f".join("" if v is None else str(csv_cell(v)) for v in r) + "\n").encode("utf-8"))
    return h.hexdigest()


def build_csv(header: list[str], rows: list[list[Any]], trailer: str) -> bytes:
    df = pd.DataFrame([[csv_cell(v) for v in r] for r in rows], columns=header)
    buf = io.StringIO()
    df.to_csv(buf, index=False, lineterminator="\n")
    buf.write(trailer.rstrip("\n") + "\n")
    return buf.getvalue().encode("utf-8")


async def store_report(
    db: AsyncSession, report_type: str, data: bytes, ext: str, username: str, user_id: int | None,
    params: dict[str, Any], row_count: int | None, subdir: str = "reports", basename: str | None = None,
) -> ReportFile:
    stamp = ist_stamp()
    name = basename or f"{report_type}_{stamp}IST_{username}.{ext}"
    rel = f"{subdir}/{utc_date_dir()}/{name}"
    sha, size = write_bytes_hashed(rel, data)
    rf = ReportFile(type=report_type, path=rel, sha256=sha, size_bytes=size, params=params, row_count=row_count, created_by=user_id, created_at=utcnow())
    db.add(rf)
    await db.commit()
    await db.refresh(rf)
    return rf


# ---- PDF helpers ---------------------------------------------------------------------------


class _Footer:
    def __init__(self, username: str, body_hash: str) -> None:
        self.username = username
        self.body_hash = body_hash
        self.generated = fmt_ist(utcnow())

    def __call__(self, canvas, doc) -> None:
        canvas.saveState()
        w, _h = doc.pagesize
        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(colors.HexColor("#4B5563"))
        canvas.drawString(12 * mm, 8 * mm, f"{PRODUCT} · {self.username} · {self.generated} · {WATERMARK_SUFFIX}")
        canvas.drawRightString(w - 12 * mm, 8 * mm, f"Evidence hash: {self.body_hash}  ·  page {doc.page}")
        canvas.restoreState()


def _thumb(rel_path: str | None, size_mm: float = 22) -> Any:
    if not rel_path:
        return Paragraph("—", CELL)
    p = abs_path(rel_path)
    if not p.exists():
        return Paragraph("(missing)", CELL)
    try:
        img = Image(str(p))
        ratio = img.imageWidth / float(img.imageHeight or 1)
        img.drawWidth = size_mm * mm
        img.drawHeight = size_mm * mm / max(ratio, 0.1)
        return img
    except Exception:  # noqa: BLE001
        return Paragraph("(unreadable)", CELL)


def _table(header: list[str], rows: list[list[Any]], col_widths: list[float] | None = None, font_size: float = 7) -> Table:
    data = [[Paragraph(str(h), CELL) for h in header]] + [[c if not isinstance(c, str) else Paragraph(c, CELL) for c in r] for r in rows]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E5E7EB")),
                ("FONTSIZE", (0, 0), (-1, -1), font_size),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
            ]
        )
    )
    return t


def _header_block(title: str, subtitle_lines: list[str]) -> list[Any]:
    out = [Paragraph(f"{PRODUCT} — {title}", H1)]
    for line in subtitle_lines:
        out.append(Paragraph(line, BODY))
    out.append(Paragraph(f"Version {VERSION} · Dynatech Consultancy · generated {fmt_ist(utcnow())}", SMALL))
    out.append(Spacer(1, 4 * mm))
    return out


def render_pdf(story: list[Any], username: str, pagesize=A4) -> bytes:
    """Two-pass render: the footer carries the sha256 of the body-only render (evidence hash)."""
    body = _render(story, username, pagesize, body_hash="pending")
    body_hash = hashlib.sha256(body).hexdigest()[:32]
    return _render(story, username, pagesize, body_hash=body_hash)


def _render(story: list[Any], username: str, pagesize, body_hash: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=14 * mm, title=PRODUCT, author=f"{PRODUCT} {VERSION}")
    footer = _Footer(username, body_hash)
    import copy

    doc.build(copy.deepcopy(story), onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# ---- static map drawing --------------------------------------------------------------------


def draw_map(points: list[tuple[float, float, str]], outline: list[list[tuple[float, float]]] | None = None, polyline: bool = True, width_mm: float = 170, height_mm: float = 95, cells: list[list[tuple[float, float]]] | None = None) -> Image:
    """Plot lat/lon points (numbered), optional district outline and red cells on a white canvas → PNG image flowable."""
    from PIL import Image as PILImage, ImageDraw, ImageFont

    W, H = int(width_mm * 8), int(height_mm * 8)
    img = PILImage.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    lats = [p[0] for p in points] + [c[0] for ring in (outline or []) for c in ring]
    lons = [p[1] for p in points] + [c[1] for ring in (outline or []) for c in ring]
    if cells:
        lats += [c[0] for cell in cells for c in cell]
        lons += [c[1] for cell in cells for c in cell]
    if not lats:
        draw.text((10, 10), "No coordinates available", fill="black")
        return _pil_to_flowable(img, width_mm)
    lat_min, lat_max, lon_min, lon_max = min(lats), max(lats), min(lons), max(lons)
    pad_lat = max((lat_max - lat_min) * 0.15, 0.002)
    pad_lon = max((lon_max - lon_min) * 0.15, 0.002)
    lat_min, lat_max, lon_min, lon_max = lat_min - pad_lat, lat_max + pad_lat, lon_min - pad_lon, lon_max + pad_lon
    # keep aspect roughly right for the latitude
    import math

    span_lat = lat_max - lat_min
    span_lon = (lon_max - lon_min) * math.cos(math.radians((lat_max + lat_min) / 2))
    scale = min((W - 40) / span_lon, (H - 40) / span_lat)

    def xy(lat: float, lon: float) -> tuple[float, float]:
        x = 20 + (lon - lon_min) * math.cos(math.radians((lat_max + lat_min) / 2)) * scale
        y = H - 20 - (lat - lat_min) * scale
        return x, y

    for ring in outline or []:
        pts = [xy(a, b) for a, b in ring]
        if len(pts) > 2:
            draw.polygon(pts, outline=(11, 31, 58), fill=(245, 247, 250))
    for cell in cells or []:
        pts = [xy(a, b) for a, b in cell]
        if len(pts) > 2:
            draw.polygon(pts, fill=(252, 165, 165), outline=(220, 38, 38))
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    if polyline and len(points) > 1:
        draw.line([xy(p[0], p[1]) for p in points], fill=(30, 77, 183), width=4)
    for lat, lon, label in points:
        x, y = xy(lat, lon)
        r = 16
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(220, 38, 38) if polyline else (30, 77, 183), outline="white", width=3)
        if label:
            tw = draw.textlength(label, font=font)
            draw.text((x - tw / 2, y - 13), label, fill="white", font=font)
    draw.rectangle((0, 0, W - 1, H - 1), outline=(229, 231, 235))
    return _pil_to_flowable(img, width_mm)


def _pil_to_flowable(img, width_mm: float) -> Image:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    flow = Image(buf)  # reportlab accepts a file-like object; ImageReader is not accepted by the flowable
    ratio = img.height / float(img.width)
    flow.drawWidth = width_mm * mm
    flow.drawHeight = width_mm * mm * ratio
    return flow


# ---- Report renderers ------------------------------------------------------------------------

DETECTIONS_CSV_COLUMNS = [
    "captured_at_ist", "captured_at_utc", "camera_id", "camera_external_id", "camera_name", "department", "district",
    "lat", "lon", "plate", "plate_raw", "is_valid_format", "confidence", "sighting_id", "read_id", "crop_path", "crop_sha256", "mode",
]


def detections_pdf(username: str, window: dict[str, Any], summary: dict[str, Any], rows: list[dict[str, Any]], quality: dict[str, Any], capped: bool) -> bytes:
    story: list[Any] = _header_block(
        "Output report: detected plates",
        [f"Window (IST): {window['from_ist']} → {window['to_ist']}", f"Filters: {window.get('filters_text', 'none')}", f"Generated by {username}"],
    )
    story.append(Paragraph("Summary", H2))
    kv = [
        ["Cameras", summary["cameras"]], ["Departments", summary["departments"]], ["Reads", summary["reads"]],
        ["Valid-format %", f"{summary['valid_pct']:.1f}"], ["Sightings", summary["sightings"]], ["Unique plates", summary["unique_plates"]],
        ["Alerts in window", summary["alerts"]], ["Object counts", ", ".join(f"{k}: {v}" for k, v in summary["object_counts"].items()) or "—"],
    ]
    if capped:
        kv.append(["Note", f"table capped at {len(rows)} rows; the CSV export carries the full set"])
    story.append(_table(["Metric", "Value"], [[str(a), str(b)] for a, b in kv], [50 * mm, 120 * mm]))
    story.append(Paragraph("Detections", H2))
    header = ["#", "Captured (IST)", "Camera", "Department", "Plate", "Conf", "Sighting", "SHA-256 (crop)"]
    table_rows = []
    for i, r in enumerate(rows, 1):
        table_rows.append([str(i), r["captured_at_ist"], f"{r['camera_id']} · {r['camera_name']}", r["department"] or "", format_plate(r["plate"]) if r["is_valid_format"] else r["plate"], f"{r['confidence']:.2f}", str(r["sighting_id"] or ""), (r["crop_sha256"] or "")[:16] + "…"])
    for i in range(0, len(table_rows), 40):
        story.append(_table(header, table_rows[i : i + 40], [8 * mm, 34 * mm, 48 * mm, 28 * mm, 26 * mm, 10 * mm, 14 * mm, 28 * mm]))
        if i + 40 < len(table_rows):
            story.append(PageBreak())
    thumbs = [r for r in rows[:50] if r.get("crop_path")]
    if thumbs:
        story.append(PageBreak())
        story.append(Paragraph("Crop thumbnails (first 50) with SHA-256", H2))
        grid = []
        row: list[Any] = []
        for r in thumbs:
            cell = [_thumb(r["crop_path"], 26), Paragraph(f"{format_plate(r['plate']) if r['is_valid_format'] else r['plate']}<br/>{r['captured_at_ist']}<br/>{(r['crop_sha256'] or '')[:16]}…", CELL)]
            row.append(cell)
            if len(row) == 5:
                grid.append(row)
                row = []
        if row:
            grid.append(row)
        t = Table(grid, colWidths=[36 * mm] * 5)
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E5E7EB"))]))
        story.append(t)
    story.append(PageBreak())
    story.append(Paragraph("Analytics quality", H2))
    q_rows = [
        ["Reads", quality.get("reads_total")], ["Valid-format %", quality.get("valid_format_pct")], ["Sightings", quality.get("sightings_total")],
        ["Unique plates", quality.get("unique_plates")], ["Mean confidence", quality.get("mean_confidence")], ["Labelled crops", quality.get("labelled")],
        ["Exact-match accuracy %", quality.get("exact_match_pct")], ["Character accuracy %", quality.get("char_accuracy_pct")],
    ]
    story.append(_table(["Metric", "Value"], [[str(a), "—" if b is None else str(b)] for a, b in q_rows], [60 * mm, 60 * mm]))
    if quality.get("reads_per_camera"):
        story.append(Paragraph("Reads per camera", H2))
        story.append(_table(["Camera", "Reads", "Valid %", "Mean conf"], [[f"{c['camera_id']} · {c['camera_name']}", str(c["reads"]), str(c["valid_pct"]), str(c["mean_conf"])] for c in quality["reads_per_camera"]], [90 * mm, 25 * mm, 25 * mm, 25 * mm]))
    return render_pdf(story, username)


def route_pdf(username: str, plate_norm: str, window: dict[str, Any], route: dict[str, Any], outline: list[list[tuple[float, float]]] | None) -> bytes:
    disp = format_plate(plate_norm)
    story: list[Any] = _header_block(
        f"Vehicle route report: {disp}",
        [f"Window (IST): {window['from_ist']} → {window['to_ist']} · include={window['include']}", f"Generated by {username}"],
    )
    s = route
    story.append(Paragraph(
        f"{s['cameras_count']} cameras · {len(s['sightings'])} stops · {s['total_distance_km']} km · {s['total_duration_min']} min · "
        f"{len(s['flags'])} plausibility flag(s) · {s.get('loop_resets_in_window', 0)} loop resets in window", BODY))
    pts = [(x["camera"]["lat"], x["camera"]["lon"], str(x["seq"])) for x in s["sightings"] if x.get("in_polyline")]
    story.append(Spacer(1, 3 * mm))
    story.append(draw_map(pts, outline=outline, polyline=True))
    story.append(Paragraph("Timeline", H2))
    header = ["#", "Camera", "Department", "District", "First seen IST", "Last seen IST", "Reads", "Conf", "Match", "SHA-256 (crop)"]
    rows = []
    for x in s["sightings"]:
        cam = x["camera"]
        rows.append([str(x["seq"]), cam.get("name", ""), cam.get("department_code") or "", cam.get("district") or "", fmt_ist(x["first_seen"], False), fmt_ist(x["last_seen"], False), str(x["read_count"]), f"{x['best_conf']:.2f}", x["match"] + (f" ({x['confirmation']})" if x.get("confirmation") else ""), (x.get("crop_sha256") or "")[:16] + ("…" if x.get("crop_sha256") else "")])
    story.append(_table(header, rows, [7 * mm, 40 * mm, 18 * mm, 20 * mm, 26 * mm, 26 * mm, 10 * mm, 10 * mm, 20 * mm, 24 * mm]))
    if s["flags"]:
        story.append(Paragraph("Plausibility flags", H2))
        for f in s["flags"]:
            story.append(Paragraph(f"• [{f['type']}] {f['message']}", BODY))
        story.append(Paragraph("Note: on looping test feeds every leg is expected to be flagged; flags are advisory and shown as amber badges in the UI.", SMALL))
    crops = [x for x in s["sightings"] if x.get("crop_path")][:24]
    if crops:
        story.append(PageBreak())
        story.append(Paragraph("Crops", H2))
        grid, row = [], []
        for x in crops:
            row.append([_thumb(x["crop_path"], 34), Paragraph(f"#{x['seq']} {x['camera'].get('name', '')}<br/>{fmt_ist(x['first_seen'])}", CELL)])
            if len(row) == 4:
                grid.append(row)
                row = []
        if row:
            grid.append(row)
        t = Table(grid, colWidths=[45 * mm] * 4)
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E5E7EB"))]))
        story.append(t)
    return render_pdf(story, username)


def gap_pdf(username: str, data: dict[str, Any], camera_points: list[tuple[float, float, str]], outline: list[list[tuple[float, float]]] | None, cells: list[list[tuple[float, float]]]) -> bytes:
    sm = data["summary"]
    p = data["params"]
    story: list[Any] = _header_block(
        "Gap-analysis report",
        [f"Coverage radius {p['coverage_radius_m']} m · POI radius {p['poi_radius_m']} m · grid {p['grid_m']} m · ageing threshold {p['ageing_years']} y" + (f" · district {p['district']}" if p.get("district") else ""), f"Generated by {username}"],
    )
    story.append(Paragraph("Summary", H2))
    story.append(_table(["Metric", "Value"], [[k.replace("_", " "), str(v)] for k, v in sm.items()], [70 * mm, 60 * mm]))
    story.append(Paragraph("Zero-coverage cells and cameras", H2))
    story.append(draw_map(camera_points, outline=outline, polyline=False, cells=cells[:1500]))
    story.append(Paragraph(f"{len(cells)} cells drawn (red) · {len(camera_points)} cameras (blue)", SMALL))
    story.append(PageBreak())
    story.append(Paragraph("Coverage by district", H2))
    story.append(_table(["District", "Total", "Online", "Online %", "Zero-coverage cells", "Uncovered POIs", "Ageing", "Metadata gaps"], [[d["district"], str(d["total"]), str(d["online"]), str(d["online_pct"]), str(d["zero_coverage_cells"]), str(d["uncovered_pois"]), str(d["ageing"]), str(d["metadata_gaps"])] for d in data["by_district"]]))
    story.append(Paragraph("Coverage by area", H2))
    story.append(_table(["District", "Police station", "Ward", "Total", "Online %", "By department"], [[a["district"] or "", a["police_station"] or "", a["ward"] or "", str(a["total"]), str(a["online_pct"]), ", ".join(f"{k}: {v}" for k, v in a["by_department"].items())] for a in data["by_area"][:200]]))
    story.append(Paragraph("Uncovered POIs", H2))
    story.append(_table(["POI", "Type", "District", "Nearest camera (m)"], [[u["name"], u["type"], u["district"], str(u["nearest_camera_m"] or "none")] for u in data["uncovered_pois"][:200]]))
    story.append(Paragraph("Department gaps", H2))
    story.append(_table(["Department", "District"], [[g["department_name"], g["district"]] for g in data["department_gaps"][:200]] or [["—", "—"]]))
    story.append(Paragraph("Ageing infrastructure", H2))
    story.append(_table(["Camera", "Dept", "District", "Type", "Age (y)", "AMC", "Offline 24 h %", "Score", "Reasons"], [[f"{a['camera_id']} · {a['name']}", a["department_code"] or "", a["district"] or "", a["type"], str(a["age_years"] or ""), a["amc_status"], str(a["offline_pct_24h"]), str(a["priority_score"]), "; ".join(a["reasons"])] for a in data["ageing"][:200]] or [["—"] * 9]))
    story.append(Paragraph("Offline hotspots", H2))
    story.append(_table(["District", "Police station", "Cameras"], [[h["district"] or "", h["police_station"] or "", ", ".join(str(i) for i in h["camera_ids"])] for h in data["offline_hotspots"]] or [["—", "—", "—"]]))
    story.append(Paragraph("Metadata gaps", H2))
    story.append(_table(["Camera", "Missing"], [[f"{m['camera_id']} · {m['name']}", ", ".join(m["missing"])] for m in data["metadata_gaps"][:300]] or [["—", "—"]]))
    story.append(Paragraph("Recommendations", H2))
    for r in data["recommendations"]:
        story.append(Paragraph(f"<b>{r['district']}</b>: {r['text']}", BODY))
    return render_pdf(story, username, pagesize=landscape(A4))


def quality_pdf(username: str, q: dict[str, Any]) -> bytes:
    scope_bits = [f"camera {q['window']['camera_id']}" if q["window"].get("camera_id") else "", f"camera source '{q['window']['source']}'" if q["window"].get("source") else ""]
    story: list[Any] = _header_block("Analytics quality (ANPR accuracy evidence)", [f"Window (IST): {q['window']['from_ist']} → {q['window']['to_ist']}", "Scope: " + (", ".join(b for b in scope_bits if b) or "every camera in the user's scope"), f"Generated by {username}"])
    rows = [
        ["Reads", q["reads_total"]], ["Valid-format reads", q["reads_valid_format"]], ["Valid-format %", q["valid_format_pct"]], ["Sightings", q["sightings_total"]],
        ["Unique plates", q["unique_plates"]], ["Mean confidence", q["mean_confidence"]], ["Labelled", q["labelled"]], ["Exact-match %", q["exact_match_pct"]], ["Character accuracy %", q["char_accuracy_pct"]],
    ]
    story.append(_table(["Metric", "Value"], [[str(a), "—" if b is None else str(b)] for a, b in rows], [60 * mm, 60 * mm]))
    if q["reads_per_camera"]:
        story.append(Paragraph("Reads per camera", H2))
        story.append(_table(["Camera", "Reads", "Valid %", "Mean conf"], [[f"{c['camera_id']} · {c['camera_name']}", str(c["reads"]), str(c["valid_pct"]), str(c["mean_conf"])] for c in q["reads_per_camera"]]))
    if q["per_camera_accuracy"]:
        story.append(Paragraph("Spot-check accuracy per camera", H2))
        story.append(_table(["Camera", "Labelled", "Exact %", "Char accuracy %"], [[f"{c['camera_id']} · {c['camera_name']}", str(c["labelled"]), str(c["exact_pct"]), str(c["char_accuracy_pct"])] for c in q["per_camera_accuracy"]]))
    if q["confusions"]:
        story.append(Paragraph("Character confusions", H2))
        story.append(_table(["Expected", "Got", "Count"], [[c["expected"], c["got"], str(c["count"])] for c in q["confusions"]], [30 * mm, 30 * mm, 30 * mm]))
    if q["sample"]:
        story.append(Paragraph("Labelled sample", H2))
        story.append(_table(["Read", "Read plate", "True plate", "Match"], [[str(s["read_id"]), s["plate_norm"], s["true_plate"], "yes" if s["is_match"] else "no"] for s in q["sample"][:60]]))
    return render_pdf(story, username)
