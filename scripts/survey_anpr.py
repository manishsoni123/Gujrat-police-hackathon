#!/usr/bin/env python3
"""Plate-readability survey of the relay cameras: which ones are worth the live ANPR budget?

Extends ``scripts/survey_sandbox.py`` (one frame + codec per camera) with the worker's own
detector and OCR so the choice of ``anpr_enabled`` cameras is measured, not guessed. Runs inside
the ``anpr`` image (ffmpeg, OpenCV, onnxruntime, PaddleOCR, ``anpr`` package on ``PYTHONPATH``)::

    docker run --rm --network sentinel_default --cpus 6 \\
        -e INTERNAL_API_KEY -e ANPR_DETECTOR=onnx \\
        -v $PWD/scripts:/app/scripts:ro -v $PWD/media:/app/media \\
        --entrypoint python sentinel-anpr:cpu /app/scripts/survey_anpr.py \\
        --api http://api:8000 --out /app/media/anpr_survey --json /app/media/anpr_survey.json

Per camera (``--group`` cameras decoded at once, the next group starting while the current one
is analysed, to pace the relay and the sandbox):

1. ``ffmpeg -rtsp_transport tcp`` on the relay path, every ``--every``-th frame (about 1 fps on the
   sandbox feeds; ``--keyframes`` takes I-frames only instead) at the source resolution (or
   ``--frame-width``), until ``--frames`` frames arrived or ``--capture-s`` passed after the first
   one. The frame size comes from ``showinfo`` so no separate ffprobe is needed; decoder warnings
   on stderr are counted.
2. The configured plate detector (``ANPR_DETECTOR``, default ``onnx``; weights hash verified) in
   two ways: **production** - the frame resized to ``FRAME_WIDTH`` (960) exactly as the worker
   sees it - and **tiled** - overlapping ``--tile`` px tiles of the full-resolution frame, merged
   by IoU, which is what a plate 30-60 px wide in a 1080p wide-angle scene needs. A low
   ``--survey-min-w`` keeps small plates so their size is *measured* rather than dropped.
3. PaddleOCR on the best ``--max-boxes`` tiled crops per frame, ``anpr.normalise`` on the text.
4. A score 0-100 (see ``score_camera``), a contact sheet ``<external_id>.jpg`` with the boxes
   and reads drawn, and an ``overview.jpg`` of every camera's best frame ordered by score.

Outputs: ``--json`` (default ``<out>/anpr_survey.json``) with one row per camera and the
parameters used; ``<out>/<external_id>.jpg`` per camera; ``<out>/overview.jpg``. Relay URLs never
carry credentials, but every URL written is masked anyway (``user:***@``).

Exit code 0 when at least one camera delivered frames, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from survey_sandbox import FFMPEG, load_from_api  # noqa: E402

from anpr.detector import Box, TiledDetector, build_detector, iou  # noqa: E402
from anpr.normalise import normalise  # noqa: E402

log = logging.getLogger("survey_anpr")

_CRED_RE = re.compile(r"://([^/@:]+):[^/@]*@")
#: ffmpeg stderr lines that mean the decoder had to work around missing/corrupt data.
_WARN_RE = re.compile(r"error|corrupt|concealing|missing|invalid|skipping|lost|non-existing|no frame", re.IGNORECASE)
_SHOWINFO_RE = re.compile(r"\bn:\s*(\d+)\b.*?\bpts_time:\s*([-\d.]+).*?\bs:(\d+)x(\d+)")
EDGE_MARGIN_PX = 8              # same rule as anpr.pipeline: boxes touching the frame edge are cut off
THUMB = (320, 180)
COLS = 6


def mask_url(url: str | None) -> str | None:
    return _CRED_RE.sub(r"://\1:***@", url) if url else url


@dataclass
class Detection:
    box: Box                     # full-resolution frame coordinates
    raw: str = ""
    plate_norm: str = ""
    valid: bool = False
    ocr_conf: float = 0.0
    crop: np.ndarray | None = None
    osd: bool = False            # static overlay / sign text (camera name, "RLVD"), not a vehicle plate

    def as_json(self) -> dict[str, Any]:
        return {"bbox": self.box.as_list(), "det_conf": round(self.box.confidence, 3), "raw": self.raw,
                "plate_norm": self.plate_norm, "valid": self.valid, "ocr_conf": round(self.ocr_conf, 3), "osd": self.osd}


@dataclass
class FrameResult:
    index: int
    pts: float | None
    brightness: float
    saturation: float
    detections: list[Detection] = field(default_factory=list)      # tiled, full resolution
    production_boxes: int = 0                                        # what the 960 px worker path finds
    production_max_w: int = 0
    image: np.ndarray | None = None


@dataclass
class Grab:
    frames: list[tuple[np.ndarray, float | None]] = field(default_factory=list)
    first_frame_s: float | None = None
    capture_s: float = 0.0
    warnings: int = 0
    error: str = ""
    src_width: int = 0
    src_height: int = 0


# ----------------------------------------------------------------------------- decode
def grab_frames(url: str, *, frames: int, start_timeout: float, capture_s: float, width: int, every: int, interval_s: float, keyframes: bool) -> Grab:
    """Pull up to ``frames`` frames from ``url`` (every ``every``-th, or key frames only)."""
    g = Grab()
    cmd = [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "info", "-nostats",
           "-rtsp_transport", "tcp", "-timeout", str(int(start_timeout * 1_000_000))]
    if keyframes:
        cmd += ["-skip_frame", "nokey"]
    filters = []
    if not keyframes and interval_s > 0:
        # one frame per ``interval_s`` of stream time: lossy feeds decode far fewer frames than the
        # nominal rate, so a fixed frame-count decimation would starve them
        filters.append(f"select=isnan(prev_selected_t)+gte(t-prev_selected_t\\,{interval_s})")
    elif not keyframes and every > 1:
        filters.append(f"select=not(mod(n\\,{every}))")
    if width > 0:
        filters.append(f"scale={width}:-2")
    filters.append("showinfo")
    cmd += ["-i", url, "-an", "-sn", "-dn", "-vf", ",".join(filters), "-fps_mode", "passthrough",
            "-frames:v", str(frames), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    assert proc.stdout is not None and proc.stderr is not None
    pts_by_index: dict[int, float] = {}
    size: list[tuple[int, int]] = []
    size_known = threading.Event()
    warn_count = [0]
    tail: list[str] = []

    def _drain() -> None:
        for raw in proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if "showinfo" in line:
                m = _SHOWINFO_RE.search(line)
                if m:
                    pts_by_index[int(m.group(1))] = float(m.group(2))
                    if not size:
                        size.append((int(m.group(3)), int(m.group(4))))
                        size_known.set()
            elif _WARN_RE.search(line):
                warn_count[0] += 1
                tail.append(line[-160:])
                del tail[:-3]

    drain = threading.Thread(target=_drain, daemon=True)
    drain.start()
    t_start = time.monotonic()
    deadline = [t_start + start_timeout + capture_s]
    done = threading.Event()

    def _watchdog() -> None:
        # A feed that keeps the socket alive but never yields a decodable frame (the lossiest
        # sandbox cameras) blocks ``stdout.read`` forever; kill ffmpeg at the deadline instead.
        while not done.wait(0.5):
            if time.monotonic() >= deadline[0] and proc.poll() is None:
                proc.kill()
                break

    threading.Thread(target=_watchdog, daemon=True).start()
    buf = bytearray()
    index = 0
    try:
        if not size_known.wait(timeout=start_timeout + 20) or proc.poll() is not None and not size:
            g.error = "no frame within the start budget" + (f": {tail[-1]}" if tail else "")
            return g
        w, h = size[0]
        g.src_width, g.src_height = w, h
        frame_bytes = w * h * 3
        while len(g.frames) < frames and time.monotonic() < deadline[0]:
            chunk = proc.stdout.read(frame_bytes - len(buf))
            if not chunk:
                break
            buf += chunk
            if len(buf) < frame_bytes:
                continue
            img = np.frombuffer(bytes(buf), dtype=np.uint8).reshape((h, w, 3))
            buf.clear()
            now = time.monotonic()
            if g.first_frame_s is None:
                g.first_frame_s = round(now - t_start, 1)
                deadline[0] = min(deadline[0], now + capture_s)
            g.frames.append((img.copy(), pts_by_index.get(index)))
            index += 1
    finally:
        done.set()
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        drain.join(timeout=2)
        g.capture_s = round(time.monotonic() - t_start, 1)
        g.warnings = warn_count[0]
        if not g.frames and not g.error:
            g.error = "no frame within the budget" + (f": {tail[-1]}" if tail else "")
    return g


# ----------------------------------------------------------------------------- analyse
def crop_for(image: np.ndarray, box: Box) -> np.ndarray:
    """Same context padding as ``anpr.pipeline.Pipeline._crop``."""
    h, w = image.shape[:2]
    mx, my = int(box.w * 0.08), int(box.h * 0.15)
    return image[max(0, box.y - my):min(h, box.y + box.h + my), max(0, box.x - mx):min(w, box.x + box.w + mx)]


def analyse_frames(grab: Grab, detector: Any, tiled: Any, ocr: Any, *, production_width: int, max_boxes: int, ocr_min_w: int) -> list[FrameResult]:
    out: list[FrameResult] = []
    for i, (img, pts) in enumerate(grab.frames):
        small = cv2.resize(img, (production_width, max(2, int(round(img.shape[0] * production_width / img.shape[1])) // 2 * 2)),
                           interpolation=cv2.INTER_AREA) if img.shape[1] > production_width else img
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        fr = FrameResult(index=i, pts=pts, brightness=float(hsv[:, :, 2].mean()), saturation=float(hsv[:, :, 1].mean()), image=img)
        prod = [b for b in detector.detect(small) if b.x > EDGE_MARGIN_PX and b.x + b.w < small.shape[1] - EDGE_MARGIN_PX]
        fr.production_boxes = len(prod)
        fr.production_max_w = max((b.w for b in prod), default=0)
        width = img.shape[1]
        boxes = [b for b in tiled.detect(img) if b.x > EDGE_MARGIN_PX and b.x + b.w < width - EDGE_MARGIN_PX]
        boxes = sorted(boxes, key=lambda b: (-b.confidence, -b.area))
        for j, box in enumerate(boxes):
            det = Detection(box=box)
            if j < max_boxes and box.w >= ocr_min_w and ocr is not None:
                crop = crop_for(img, box)
                if crop.size:
                    det.crop = crop
                    result = ocr.read(crop)
                    if result.raw:
                        norm = normalise(result.raw)
                        det.raw = result.raw.replace("\n", " ")
                        det.plate_norm, det.valid, det.ocr_conf = norm.plate_norm, norm.is_valid_format, float(result.confidence)
            fr.detections.append(det)
        out.append(fr)
    mark_osd(out)
    return out


def mark_osd(frames: list[FrameResult]) -> None:
    """Flag detections that are camera overlays or signboards rather than vehicles.

    The detector is happy to box any high-contrast text ("DELIGHT P1 RLVD", the camera name the
    encoder burns in). Two rules: a box that sits at the same place (IoU >= 0.6) in at least 60 %
    of the frames (min 3) is static; OCR text without a single digit is never a registration.
    """
    n = len(frames)
    if not n:
        return
    all_dets = [d for f in frames for d in f.detections]
    for d in all_dets:
        if d.raw and not any(ch.isdigit() for ch in d.raw):
            d.osd = True
    if n < 3:
        return
    need = max(3, int(round(0.6 * n)))
    for d in all_dets:
        hits = sum(1 for f in frames if any(iou(d.box, o.box) >= 0.6 for o in f.detections))
        if hits >= need:
            d.osd = True


def score_camera(row: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """0-100: plates per frame 30, plate width 20, valid reads per frame 30, OCR confidence 10, daylight 5, decode health 5.

    Plate counts and widths are the tiled full-resolution ones (what the camera *offers*); a plate
    100 px wide at the source is comfortably readable, 0.5 plates per frame means a vehicle with a
    visible plate every other second, 0.25 valid reads per frame means one confirmed read every 4 s.
    """
    parts = {
        "plates_per_frame": 30.0 * min(1.0, row["plates_per_frame"] / 0.5),
        "plate_width": 20.0 * min(1.0, (row["mean_plate_w_px"] or 0.0) / 100.0),
        "valid_reads": 30.0 * min(1.0, row["valid_reads_per_frame"] / 0.25),
        "confidence": 10.0 * (row["mean_valid_conf"] or 0.0),
        "daylight": 5.0 if row["daylight"] else 0.0,
        "decode": 5.0 * min(1.0, row["frames"] / max(1, row["frames_target"])) * (0.5 if row["decode_warnings"] > 50 else 1.0),
    }
    return round(sum(parts.values()), 1), {k: round(v, 1) for k, v in parts.items()}


def summarise(cam: dict[str, Any], grab: Grab, frames: list[FrameResult], *, frames_target: int, min_plate_w: int, production_width: int) -> dict[str, Any]:
    osd = [d for f in frames for d in f.detections if d.osd]
    dets = [d for f in frames for d in f.detections if not d.osd]
    reads = [d for d in dets if d.raw]
    valid = [d for d in reads if d.valid]
    n = len(frames)
    scale = (production_width / grab.src_width) if grab.src_width and grab.src_width > production_width else 1.0
    brightness = mean(f.brightness for f in frames) if frames else 0.0
    saturation = mean(f.saturation for f in frames) if frames else 0.0
    row: dict[str, Any] = {
        "camera_id": cam["id"], "external_id": cam.get("external_id"), "name": cam.get("name"),
        "rtsp_url": mask_url(cam.get("rtsp_url")), "codec": cam.get("codec"),
        "resolution": f"{grab.src_width}x{grab.src_height}" if grab.src_width else None,
        "first_frame_s": grab.first_frame_s, "capture_s": grab.capture_s,
        "frames": n, "frames_target": frames_target, "decode_warnings": grab.warnings, "error": grab.error,
        "pts_span_s": None,
        "plates": len(dets), "plates_per_frame": round(len(dets) / n, 3) if n else 0.0,
        "osd_boxes": len(osd), "osd_texts": sorted({d.raw for d in osd if d.raw})[:6],
        "plates_ge_min_w_at_960": sum(1 for d in dets if d.box.w * scale >= min_plate_w),
        "mean_plate_w_px": round(mean(d.box.w for d in dets), 1) if dets else None,
        "max_plate_w_px": max((d.box.w for d in dets), default=None),
        "mean_plate_w_at_960_px": round(mean(d.box.w for d in dets) * scale, 1) if dets else None,
        "mean_det_conf": round(mean(d.box.confidence for d in dets), 3) if dets else None,
        "production_plates": sum(f.production_boxes for f in frames),
        "production_plates_per_frame": round(sum(f.production_boxes for f in frames) / n, 3) if n else 0.0,
        "production_max_plate_w_px": max((f.production_max_w for f in frames), default=0),
        "reads": len(reads), "valid_reads": len(valid),
        "valid_reads_per_frame": round(len(valid) / n, 3) if n else 0.0,
        "valid_rate": round(len(valid) / len(reads), 3) if reads else 0.0,
        "mean_valid_conf": round(mean(d.ocr_conf for d in valid), 3) if valid else None,
        "distinct_plates": sorted({d.plate_norm for d in valid}),
        "sample_reads": [{"raw": d.raw, "plate_norm": d.plate_norm, "valid": d.valid, "conf": round(d.ocr_conf, 2), "w_px": d.box.w}
                         for d in sorted(reads, key=lambda d: -d.ocr_conf)[:8]],
        "brightness": round(brightness, 1), "saturation": round(saturation, 1),
        "daylight": bool(brightness >= 60 and saturation >= 12),   # IR night mode is bright but grey
        "contact_sheet": None,
    }
    pts = [f.pts for f in frames if f.pts is not None]
    if len(pts) >= 2:
        row["pts_span_s"] = round(max(pts) - min(pts), 1)
    row["score"], row["score_parts"] = score_camera(row)
    return row


# ----------------------------------------------------------------------------- drawing
def _put(img: np.ndarray, text: str, org: tuple[int, int], scale: float = 0.45, color=(255, 255, 255), thick: int = 1) -> None:
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def draw_frame(fr: FrameResult, size: tuple[int, int]) -> np.ndarray:
    assert fr.image is not None
    h, w = fr.image.shape[:2]
    sx, sy = size[0] / w, size[1] / h
    tile = cv2.resize(fr.image, size, interpolation=cv2.INTER_AREA)
    for d in fr.detections:
        b = d.box
        colour = (140, 140, 140) if d.osd else (0, 200, 0) if d.valid else (0, 180, 255) if d.raw else (0, 0, 230)
        p1, p2 = (int(b.x * sx), int(b.y * sy)), (int((b.x + b.w) * sx), int((b.y + b.h) * sy))
        cv2.rectangle(tile, p1, p2, colour, 1)
        label = ("osd " + (d.raw or "")[:8]) if d.osd else (d.plate_norm or d.raw or f"{b.w}px")[:12]
        _put(tile, label, (p1[0], max(10, p1[1] - 3)), 0.38, colour)
    _put(tile, f"#{fr.index} pts {fr.pts:.1f}" if fr.pts is not None else f"#{fr.index}", (4, size[1] - 6), 0.38)
    return tile


def contact_sheet(row: dict[str, Any], frames: list[FrameResult], dest: Path) -> None:
    tiles = [draw_frame(f, THUMB) for f in frames if f.image is not None]
    if not tiles:
        tiles = [np.full((THUMB[1], THUMB[0], 3), 40, dtype=np.uint8)]
        _put(tiles[0], "no frame", (110, 95), 0.7, (0, 0, 220), 2)
    while len(tiles) % COLS:
        tiles.append(np.zeros((THUMB[1], THUMB[0], 3), dtype=np.uint8))
    grid = np.vstack([np.hstack(tiles[i:i + COLS]) for i in range(0, len(tiles), COLS)])
    header = np.full((44, grid.shape[1], 3), 30, dtype=np.uint8)
    title = f"{row['external_id']}  {str(row['name'])[:40]}  score {row['score']}  {row['codec'] or ''} {row['resolution'] or ''}"
    stats = (f"frames {row['frames']}/{row['frames_target']}  plates/frame {row['plates_per_frame']} (960px path {row['production_plates_per_frame']})  "
             f"mean plate {row['mean_plate_w_px'] or 0} px src ({row['mean_plate_w_at_960_px'] or 0} px at 960)  reads {row['reads']}  valid {row['valid_reads']}  "
             f"conf {row['mean_valid_conf'] or 0}  bright {row['brightness']}  {'day' if row['daylight'] else 'night/IR'}  warn {row['decode_warnings']}")
    _put(header, title, (8, 18), 0.55, (255, 255, 255), 1)
    _put(header, stats, (8, 37), 0.42, (200, 220, 255), 1)
    parts = [header, grid]
    crops = sorted((d for f in frames for d in f.detections if d.crop is not None and d.raw and not d.osd), key=lambda d: -d.ocr_conf)[:8]
    if crops:
        strip = np.full((90, grid.shape[1], 3), 20, dtype=np.uint8)
        x = 6
        for d in crops:
            c = d.crop
            assert c is not None
            ch, cw = c.shape[:2]
            scale = 60.0 / max(1, ch)
            c = cv2.resize(c, (max(8, int(cw * scale)), 60), interpolation=cv2.INTER_CUBIC)
            if x + c.shape[1] + 6 > strip.shape[1]:
                break
            strip[6:66, x:x + c.shape[1]] = c
            colour = (0, 200, 0) if d.valid else (0, 180, 255)
            _put(strip, f"{d.raw[:12]} -> {d.plate_norm[:10]} {d.ocr_conf:.2f} {d.box.w}px", (x, 82), 0.36, colour)
            x += max(c.shape[1], 170) + 10
        parts.append(strip)
    cv2.imwrite(str(dest), np.vstack(parts), [int(cv2.IMWRITE_JPEG_QUALITY), 85])


def best_frame(frames: list[FrameResult]) -> FrameResult | None:
    if not frames:
        return None
    return max(frames, key=lambda f: (sum(1 for d in f.detections if d.valid), len(f.detections), f.brightness))


def overview_sheet(entries: list[tuple[dict[str, Any], np.ndarray | None]], dest: Path) -> None:
    """Best frame per camera (already rendered as a thumbnail), ordered by score."""
    tiles = []
    for row, thumb in sorted(entries, key=lambda e: -e[0]["score"]):
        tile = thumb.copy() if thumb is not None else np.full((THUMB[1], THUMB[0], 3), 40, dtype=np.uint8)
        cv2.rectangle(tile, (0, 0), (THUMB[0], 18), (0, 0, 0), -1)
        _put(tile, f"{row['external_id']} {row['score']:>5}  {str(row['name'])[:22]}", (4, 13), 0.42, (255, 255, 255))
        tiles.append(tile)
    if not tiles:
        return
    while len(tiles) % 5:
        tiles.append(np.zeros((THUMB[1], THUMB[0], 3), dtype=np.uint8))
    cv2.imwrite(str(dest), np.vstack([np.hstack(tiles[i:i + 5]) for i in range(0, len(tiles), 5)]), [int(cv2.IMWRITE_JPEG_QUALITY), 82])


# ----------------------------------------------------------------------------- main
def parse_ids(spec: str) -> set[int]:
    ids: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            ids.update(range(int(a), int(b) + 1))
        else:
            ids.add(int(part))
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--api", help="Sentinel API base URL, e.g. http://api:8000 (needs --key / INTERNAL_API_KEY)")
    src.add_argument("--rtsp", nargs="+", help="explicit RTSP URLs (id = position)")
    parser.add_argument("--key", default=os.environ.get("INTERNAL_API_KEY", ""))
    parser.add_argument("--cameras", default="", help="camera ids to include, e.g. 61-90,60 (default: all)")
    parser.add_argument("--out", default="/app/media/anpr_survey")
    parser.add_argument("--json", default="", help="summary path (default <out>/anpr_survey.json)")
    parser.add_argument("--frames", type=int, default=30, help="frames per camera")
    parser.add_argument("--capture-s", type=float, default=45.0, help="seconds after the first frame to keep reading")
    parser.add_argument("--start-timeout", type=float, default=60.0, help="RTSP/on-demand start budget per camera")
    parser.add_argument("--group", type=int, default=5, help="cameras decoded at the same time")
    parser.add_argument("--interval-s", type=float, default=1.0, help="one frame per this many seconds of stream time (0 = use --every)")
    parser.add_argument("--every", type=int, default=25, help="take every N-th decoded frame when --interval-s is 0")
    parser.add_argument("--keyframes", action="store_true", help="I-frames only (-skip_frame nokey) instead of --every")
    parser.add_argument("--frame-width", type=int, default=0, help="decode width; 0 = source resolution")
    parser.add_argument("--production-width", type=int, default=int(os.environ.get("FRAME_WIDTH", "960")),
                        help="the worker's FRAME_WIDTH; the detector also runs on the frame resized to this width")
    parser.add_argument("--tile", type=int, default=768, help="tile size for the full-resolution pass (0 = whole frame)")
    parser.add_argument("--detector", default=os.environ.get("ANPR_DETECTOR", "onnx"), help="onnx | auto | contour")
    parser.add_argument("--det-model", default=os.environ.get("ANPR_DET_MODEL", "/app/weights/yolo-v9-t-384-license-plates-end2end.onnx"))
    parser.add_argument("--det-conf", type=float, default=float(os.environ.get("ANPR_DET_CONF", "0.4")))
    parser.add_argument("--min-plate-w", type=int, default=int(os.environ.get("ANPR_MIN_PLATE_W", "60")),
                        help="production threshold (at --production-width); detections down to --survey-min-w are still measured")
    parser.add_argument("--survey-min-w", type=int, default=24)
    parser.add_argument("--ocr-min-w", type=int, default=40, help="OCR only boxes at least this wide (source px)")
    parser.add_argument("--max-boxes", type=int, default=4)
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.api:
        if not args.key:
            parser.error("--api needs --key (or INTERNAL_API_KEY)")
        cameras = load_from_api(args.api, args.key, 30)
    else:
        cameras = [{"id": i, "external_id": str(i), "name": f"rtsp {i}", "rtsp_url": u} for i, u in enumerate(args.rtsp, 1)]
    if args.cameras:
        wanted = parse_ids(args.cameras)
        cameras = [c for c in cameras if int(c["id"]) in wanted]
    cameras = [c for c in cameras if c.get("rtsp_url")]
    if not cameras:
        print("no cameras to survey", file=sys.stderr)
        return 1
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = Path(args.json) if args.json else out_dir / "anpr_survey.json"

    detector = build_detector(args.detector, args.det_model, conf=args.det_conf, min_w=args.survey_min_w, cpu=True, threads=args.threads)
    log.info("detector: %s", detector.name)
    tiled = TiledDetector(detector, args.tile, 0.15) if args.tile > 0 else detector
    ocr = None
    if not args.no_ocr:
        from anpr.ocr import PlateOCR

        ocr = PlateOCR(lang=os.environ.get("OCR_LANG", "en"), cpu=True, threads=args.threads)

    started = datetime.now(timezone.utc)
    group = max(1, args.group)
    log.info("surveying %d cameras, %d at a time, %d frames (%s) / %.0f s each, tile %d px",
             len(cameras), group, args.frames, "key frames" if args.keyframes else (f"every {args.interval_s} s" if args.interval_s > 0 else f"every {args.every}th"), args.capture_s, args.tile)
    rows: list[dict[str, Any]] = []
    overview: list[tuple[dict[str, Any], np.ndarray | None]] = []
    t0 = time.monotonic()

    def submit(pool: ThreadPoolExecutor, batch: list[dict[str, Any]]) -> list[tuple[dict[str, Any], Future[Grab]]]:
        return [(c, pool.submit(grab_frames, c["rtsp_url"], frames=args.frames, start_timeout=args.start_timeout,
                                capture_s=args.capture_s, width=args.frame_width, every=args.every, interval_s=args.interval_s,
                                keyframes=args.keyframes)) for c in batch]

    batches = [cameras[i:i + group] for i in range(0, len(cameras), group)]
    with ThreadPoolExecutor(max_workers=group) as pool:
        pending = submit(pool, batches[0])
        for bi in range(len(batches)):
            current = pending
            # the next group starts pulling while this one is analysed (at most two groups of frames in memory)
            pending = submit(pool, batches[bi + 1]) if bi + 1 < len(batches) else []
            for cam, fut in current:
                try:
                    grab = fut.result()
                except Exception as exc:  # noqa: BLE001 - one bad camera must not end the survey
                    grab = Grab(error=f"grab failed: {exc}"[:200])
                t1 = time.monotonic()
                frames = analyse_frames(grab, detector, tiled, ocr, production_width=args.production_width,
                                        max_boxes=args.max_boxes, ocr_min_w=args.ocr_min_w)
                row = summarise(cam, grab, frames, frames_target=args.frames, min_plate_w=args.min_plate_w, production_width=args.production_width)
                row["analysis_s"] = round(time.monotonic() - t1, 1)
                sheet = out_dir / f"{row['external_id']}.jpg"
                contact_sheet(row, frames, sheet)
                row["contact_sheet"] = sheet.name
                rows.append(row)
                best = best_frame(frames)
                overview.append((row, draw_frame(best, THUMB) if best is not None and best.image is not None else None))
                log.info("%-6s %-26s frames %2d/%d first %5ss warn %3d plates %2d (%.2f/f, %s px; 960px path %d) valid %2d conf %s %s score %s %s",
                         row["external_id"], str(row["name"])[:26], row["frames"], args.frames, row["first_frame_s"], row["decode_warnings"],
                         row["plates"], row["plates_per_frame"], row["mean_plate_w_px"], row["production_plates"], row["valid_reads"],
                         row["mean_valid_conf"], "day" if row["daylight"] else "night", row["score"], row["error"][:60])
                grab.frames.clear()
                for f in frames:          # free the frame buffers before the next camera
                    f.image = None
                    for d in f.detections:
                        d.crop = None

    rows.sort(key=lambda r: (-r["score"], r["camera_id"]))
    overview_sheet(overview, out_dir / "overview.jpg")
    doc = {
        "generated_at": started.isoformat().replace("+00:00", "Z"),
        "duration_s": round(time.monotonic() - t0, 1),
        "params": {"frames": args.frames, "capture_s": args.capture_s, "start_timeout_s": args.start_timeout, "group": group,
                   "interval_s": None if args.keyframes or args.interval_s <= 0 else args.interval_s,
                   "every": None if args.keyframes or args.interval_s > 0 else args.every, "keyframes_only": args.keyframes,
                   "frame_width": args.frame_width or "source", "production_width": args.production_width, "tile": args.tile,
                   "detector": detector.name, "det_model": os.path.basename(args.det_model), "det_conf": args.det_conf,
                   "survey_min_w": args.survey_min_w, "production_min_plate_w": args.min_plate_w, "ocr_min_w": args.ocr_min_w,
                   "max_boxes": args.max_boxes, "ocr": "paddleocr" if ocr is not None else None,
                   "score_weights": {"plates_per_frame": 30, "plate_width": 20, "valid_reads": 30, "confidence": 10, "daylight": 5, "decode": 5}},
        "cameras": rows,
    }
    json_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    ok = [r for r in rows if r["frames"]]
    print(f"\n{'rank':>4} {'cam':<7} {'score':>5} {'frm':>3} {'p/f':>5} {'w px':>5} {'960':>4} {'valid':>5} {'conf':>5} {'day':>5} {'first':>5}  name")
    for i, r in enumerate(rows, 1):
        print(f"{i:>4} {r['external_id']:<7} {r['score']:>5} {r['frames']:>3} {r['plates_per_frame']:>5} {r['mean_plate_w_px'] or 0:>5} "
              f"{r['production_plates']:>4} {r['valid_reads']:>5} {r['mean_valid_conf'] or 0:>5} {'day' if r['daylight'] else 'night':>5} "
              f"{r['first_frame_s'] or '-':>5}  {str(r['name'])[:40]}")
    print(f"\n{len(ok)}/{len(rows)} cameras delivered frames in {doc['duration_s']} s -> {json_path} and {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
