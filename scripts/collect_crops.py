#!/usr/bin/env python3
"""Collect a bank of native-resolution plate crops from the live relay cameras (accuracy pass).

Runs inside the ``anpr`` image on the compose network (the worker's own decoder, tiled detector,
OSD/static filters and tracker; **no OCR** - this is about collecting what the cameras offer)::

    docker run --rm --network sentinel_default --cpus 4 --memory 1500m \\
        -e INTERNAL_API_KEY -v $PWD/anpr:/app/anpr:ro -v $PWD/scripts:/app/scripts:ro \\
        -v $PWD/media:/app/media --entrypoint python sentinel-anpr:cpu \\
        /app/scripts/collect_crops.py --api http://api:8000 --duration-s 600

Per camera: ``ffmpeg`` decodes the relay path at the **source resolution** (``anpr.decode.Decoder``
with ``out_width=0``; one dial at a time through the ``DialGate``), ``--fps`` frames per second of
stream time. The newest frame per camera is detected on a copy resized to ``--detect-width``
(``TiledDetector``, the production geometry), the boxes are mapped back to native pixels, the
OSD band / static-box filters of the worker are applied, and ``anpr.tracks.PlateTracker`` follows
each plate across frames. When a track ends its best crop (largest, sharpness-weighted) is written
to ``<out>/<external_id>/<utc ts>_t<track>_w<px>.jpg`` (JPEG 95, no resize) and a manifest row is
appended (camera, UTC timestamp, stream pts, bbox, width, height, detector confidence, brightness,
sharpness, frames in track, frame size). Outputs: ``<out>/manifest.csv``, ``<out>/manifest.jsonl``,
``<out>/summary.json`` (per-camera counts, fps, decoder restarts, loop clock note) and
``<out>/sheets/<external_id>.jpg`` contact sheets of the widest crops.

The burnt-in OSD clock of each camera is *not* OCR'd here; note the wall time and read the loop
phase from ``media/loopcheck`` (docs/sandbox-progress.md, loop model) when reporting.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from survey_sandbox import load_from_api  # noqa: E402

from anpr.decode import Decoder, DialGate, Frame  # noqa: E402
from anpr.detector import Box, OsdMask, StaticBoxFilter, TiledDetector, build_detector  # noqa: E402
from anpr.tracks import PlateTracker, Track  # noqa: E402

log = logging.getLogger("collect_crops")
_CRED_RE = re.compile(r"://([^/@:]+):[^/@]*@")
EDGE_MARGIN_PX = 2


def mask_url(url: str | None) -> str | None:
    return _CRED_RE.sub(r"://\1:***@", url) if url else url


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class CamState:
    cfg: dict[str, Any]
    decoder: Decoder
    tracker: PlateTracker
    osd: OsdMask
    static: StaticBoxFilter
    lock: threading.Lock = field(default_factory=threading.Lock)
    slot: Frame | None = None
    dropped: int = 0
    processed: int = 0
    boxes: int = 0
    crops: int = 0
    discontinuities: int = 0
    src_size: tuple[int, int] = (0, 0)

    def on_frame(self, frame: Frame) -> None:
        with self.lock:
            if self.slot is not None:
                self.dropped += 1
            self.slot = frame

    def on_discontinuity(self, camera_id: int, reason: str, before: float | None, after: float | None) -> None:
        with self.lock:
            self.discontinuities += 1

    def take(self) -> Frame | None:
        with self.lock:
            f, self.slot = self.slot, None
        return f


def scale_boxes(boxes: list[Box], sx: float, sy: float) -> list[Box]:
    return [Box(int(round(b.x * sx)), int(round(b.y * sy)), int(round(b.w * sx)), int(round(b.h * sy)), b.confidence, b.source) for b in boxes]


def save_track(cam: CamState, t: Track, out_dir: Path, writer: csv.DictWriter, jsonl, min_w: int) -> dict[str, Any] | None:
    """Write the best crop of an ended track and its manifest row; returns the row (None when skipped)."""
    best = t.best
    if best is None or best.box.w < min_w:
        return None
    ext = str(cam.cfg.get("external_id") or cam.cfg["id"])
    cam_dir = out_dir / ext
    cam_dir.mkdir(parents=True, exist_ok=True)
    ts = best.captured_at.astimezone(timezone.utc)
    name = f"{ts.strftime('%Y%m%dT%H%M%S')}_{ts.microsecond // 1000:03d}_t{t.id}_w{best.box.w}.jpg"
    path = cam_dir / name
    ok = cv2.imwrite(str(path), best.crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    if not ok:
        log.warning("camera %s: could not write %s", cam.cfg["id"], path)
        return None
    gray = cv2.cvtColor(best.crop, cv2.COLOR_BGR2GRAY)
    row = {
        "camera_id": cam.cfg["id"], "external_id": ext, "file": f"{ext}/{name}",
        "ts_utc": iso(best.captured_at), "stream_pts": round(best.stream_pts, 3), "track": t.id,
        "x": best.box.x, "y": best.box.y, "w": best.box.w, "h": best.box.h,
        "width_px": best.box.w, "height_px": best.box.h, "det_conf": round(best.box.confidence, 3),
        "brightness": round(float(gray.mean()), 1), "contrast": round(float(gray.std()), 1),
        "sharpness": round(best.sharp, 1), "track_frames": t.frames, "track_max_w": t.max_width,
        "first_seen": iso(t.first_seen), "last_seen": iso(t.last_seen),
        "frame_w": cam.src_size[0], "frame_h": cam.src_size[1],
    }
    writer.writerow(row)
    jsonl.write(json.dumps(row) + "\n")
    jsonl.flush()
    cam.crops += 1
    return row


def contact_sheet(rows: list[dict[str, Any]], out_dir: Path, dest: Path, title: str, limit: int = 24, cols: int = 4) -> None:
    rows = sorted(rows, key=lambda r: -int(r["width_px"]))[:limit]
    if not rows:
        return
    cell_w, cell_h = 320, 110
    tiles = []
    for r in rows:
        img = cv2.imread(str(out_dir / r["file"]))
        tile = np.full((cell_h, cell_w, 3), 25, np.uint8)
        if img is not None:
            h, w = img.shape[:2]
            s = min((cell_w - 8) / w, (cell_h - 30) / h)
            img = cv2.resize(img, (max(2, int(w * s)), max(2, int(h * s))), interpolation=cv2.INTER_CUBIC)
            tile[4:4 + img.shape[0], 4:4 + img.shape[1]] = img
        label = f"{r['width_px']}px {r['ts_utc'][11:19]}Z sh{int(float(r['sharpness']))} b{int(float(r['brightness']))}"
        cv2.putText(tile, label, (4, cell_h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(tile, label, (4, cell_h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(tile)
    while len(tiles) % cols:
        tiles.append(np.zeros((cell_h, cell_w, 3), np.uint8))
    grid = np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)])
    header = np.full((28, grid.shape[1], 3), 40, np.uint8)
    cv2.putText(header, title, (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dest), np.vstack([header, grid]), [int(cv2.IMWRITE_JPEG_QUALITY), 88])


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
    parser.add_argument("--api", default="http://api:8000")
    parser.add_argument("--key", default=os.environ.get("INTERNAL_API_KEY", ""))
    parser.add_argument("--mode", default="live", choices=["live", "preindex"], help="which anpr-config list to take the cameras from")
    parser.add_argument("--cameras", default="", help="camera ids, e.g. 61,62,64 (default: every camera of --mode)")
    parser.add_argument("--rtsp-base", default=os.environ.get("RTSP_BASE", "rtsp://mediamtx:8554"))
    parser.add_argument("--out", default="/app/media/anpr_crops")
    parser.add_argument("--duration-s", type=float, default=600.0)
    parser.add_argument("--fps", type=float, default=2.0, help="frames per second of stream time per camera")
    parser.add_argument("--detect-width", type=int, default=int(os.environ.get("FRAME_WIDTH", "1280")))
    parser.add_argument("--tile", type=int, default=int(os.environ.get("ANPR_DET_TILE", "768")))
    parser.add_argument("--det-conf", type=float, default=float(os.environ.get("ANPR_DET_CONF", "0.4")))
    parser.add_argument("--min-w", type=int, default=24, help="detector minimum box width at --detect-width")
    parser.add_argument("--save-min-w", type=int, default=24, help="save a track only when its best crop is at least this wide (native px)")
    parser.add_argument("--gap-s", type=float, default=2.0, help="a track ends after this long without a matching box")
    parser.add_argument("--keep", type=int, default=3)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--decode-threads", type=int, default=2)
    parser.add_argument("--start-timeout", type=float, default=90.0)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not args.key:
        parser.error("--key or INTERNAL_API_KEY is required")
    import requests

    resp = requests.get(f"{args.api.rstrip('/')}/api/internal/anpr-config", params={"mode": args.mode},
                        headers={"X-API-Key": args.key}, timeout=30)
    resp.raise_for_status()
    cameras = [c for c in resp.json().get("cameras", []) if c.get("rtsp_url") or c.get("relay_path")]
    if args.cameras:
        wanted = parse_ids(args.cameras)
        cameras = [c for c in cameras if int(c["id"]) in wanted]
    cameras.sort(key=lambda c: int(c["id"]))
    if not cameras:
        print("no cameras", file=sys.stderr)
        return 1
    _ = load_from_api  # imported for parity with survey_anpr (same API helper module)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    detector = build_detector("onnx", os.environ.get("ANPR_DET_MODEL", "/app/weights/yolo-v9-t-384-license-plates-end2end.onnx"),
                              conf=args.det_conf, min_w=args.min_w, cpu=True, threads=args.threads)
    tiled = TiledDetector(detector, args.tile, 0.15) if args.tile > 0 else detector
    gate = DialGate(spacing_s=3.0)
    states: dict[int, CamState] = {}
    for c in cameras:
        cid = int(c["id"])
        src = c.get("rtsp_url") or f"{args.rtsp_base.rstrip('/')}/{c.get('relay_path') or 'cam_' + str(cid)}"
        st = CamState(cfg=c, decoder=None, tracker=PlateTracker(cid, gap_s=args.gap_s, keep=args.keep),  # type: ignore[arg-type]
                      osd=OsdMask(band=0.08, warmup_s=30.0), static=StaticBoxFilter(window=20))
        st.decoder = Decoder(cid, src, mode="live", target_fps=args.fps, out_width=0, cpu=True,
                             on_frame=st.on_frame, on_discontinuity=st.on_discontinuity,
                             start_timeout_s=args.start_timeout, decode_threads=args.decode_threads, dial_gate=gate)
        states[cid] = st
    log.info("collecting from %d cameras for %.0f s: %s", len(states), args.duration_s,
             ", ".join(f"{c['id']}={mask_url(c.get('rtsp_url'))}" for c in cameras))
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    for st in states.values():
        st.decoder.start()

    fields = ["camera_id", "external_id", "file", "ts_utc", "stream_pts", "track", "x", "y", "w", "h", "width_px", "height_px",
              "det_conf", "brightness", "contrast", "sharpness", "track_frames", "track_max_w", "first_seen", "last_seen", "frame_w", "frame_h"]
    csv_path, jsonl_path = out_dir / "manifest.csv", out_dir / "manifest.jsonl"
    new_file = not csv_path.exists()
    rows: list[dict[str, Any]] = []
    detect_ms = 0.0
    frames_total = 0
    last_log = time.monotonic()
    with csv_path.open("a", newline="", encoding="utf-8") as fh, jsonl_path.open("a", encoding="utf-8") as jl:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if new_file:
            writer.writeheader()

        def finish(cam: CamState, t: Track) -> None:
            row = save_track(cam, t, out_dir, writer, jl, args.save_min_w)
            if row is not None:
                rows.append(row)

        try:
            while time.monotonic() - t0 < args.duration_s:
                busy = False
                for st in states.values():
                    frame = st.take()
                    if frame is None:
                        continue
                    busy = True
                    img = frame.image
                    h, w = img.shape[:2]
                    st.src_size = (w, h)
                    if args.detect_width > 0 and w > args.detect_width:
                        dh = max(2, int(round(h * args.detect_width / w)) // 2 * 2)
                        small = cv2.resize(img, (args.detect_width, dh), interpolation=cv2.INTER_AREA)
                    else:
                        small = img
                    t1 = time.perf_counter()
                    boxes = tiled.detect(small)
                    detect_ms += (time.perf_counter() - t1) * 1000
                    sh, sw = small.shape[:2]
                    boxes = [b for b in boxes if b.x > EDGE_MARGIN_PX and b.x + b.w < sw - EDGE_MARGIN_PX]
                    st.osd.observe(small, time.monotonic())
                    boxes = st.osd.filter(boxes, sh)
                    boxes = st.static.filter(boxes)
                    boxes = scale_boxes(boxes, w / sw, h / sh)
                    st.boxes += len(boxes)
                    touched, ended = st.tracker.update(boxes, img, frame.captured_at, frame.stream_pts, frame.frame_index)
                    for t in ended:
                        finish(st, t)
                    st.processed += 1
                    frames_total += 1
                if not busy:
                    time.sleep(0.01)
                if time.monotonic() - last_log >= 30:
                    last_log = time.monotonic()
                    log.info("t=%.0fs frames=%d detect=%.0f ms/frame crops=%d cams=%s", time.monotonic() - t0, frames_total,
                             detect_ms / max(1, frames_total), sum(s.crops for s in states.values()),
                             json.dumps({cid: {"state": s.decoder.state, "fps": round(s.decoder.stats.fps_actual, 2), "proc": s.processed,
                                               "boxes": s.boxes, "open": s.tracker.open, "crops": s.crops, "restarts": s.decoder.stats.restarts}
                                         for cid, s in states.items()}))
        finally:
            for st in states.values():
                st.decoder.request_stop()
            for st in states.values():
                for t in st.tracker.flush():
                    finish(st, t)
            for st in states.values():
                st.decoder.stop()

    all_rows: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8") as fh:
        all_rows = list(csv.DictReader(fh))
    run_rows = [r for r in all_rows if r["ts_utc"] >= iso(started)]
    summary = {
        "generated_at": iso(datetime.now(timezone.utc)), "started_at": iso(started), "duration_s": round(time.monotonic() - t0, 1),
        "params": {"fps": args.fps, "detect_width": args.detect_width, "tile": args.tile, "det_conf": args.det_conf, "min_w": args.min_w,
                   "save_min_w": args.save_min_w, "gap_s": args.gap_s, "keep": args.keep, "threads": args.threads},
        "frames": frames_total, "detect_ms_per_frame": round(detect_ms / max(1, frames_total), 1),
        "crops_this_run": len(run_rows), "crops_total": len(all_rows),
        "cameras": {},
    }
    for cid, st in states.items():
        mine = [r for r in run_rows if int(r["camera_id"]) == cid]
        widths = sorted(int(r["width_px"]) for r in mine)
        summary["cameras"][str(cid)] = {
            "external_id": st.cfg.get("external_id"), "name": st.cfg.get("name"), "state": st.decoder.state,
            "fps": round(st.decoder.stats.fps_actual, 2), "frames_decoded": st.decoder.stats.frames, "processed": st.processed,
            "dropped": st.dropped, "boxes": st.boxes, "tracks_opened": st.tracker.opened, "crops": len(mine),
            "restarts": st.decoder.stats.restarts, "discontinuities": st.discontinuities, "src_size": list(st.src_size),
            "width_px": {"min": widths[0] if widths else None, "median": widths[len(widths) // 2] if widths else None,
                         "max": widths[-1] if widths else None, "ge60": sum(1 for x in widths if x >= 60), "ge100": sum(1 for x in widths if x >= 100)},
            "brightness_mean": round(sum(float(r["brightness"]) for r in mine) / len(mine), 1) if mine else None,
            "suppressed": {"osd_band": st.osd.suppressed_band, "osd_region": st.osd.suppressed_region, "static": st.static.suppressed},
        }
        ext = str(st.cfg.get("external_id") or cid)
        contact_sheet(mine, out_dir, out_dir / "sheets" / f"{ext}.jpg", f"{ext} {str(st.cfg.get('name'))[:40]}  crops {len(mine)}  widest first")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "cameras"}))
    for cid, c in summary["cameras"].items():
        print(f"  {cid} {c['external_id']:<6} {c['state']:<12} fps {c['fps']:>4} proc {c['processed']:>4} boxes {c['boxes']:>4} tracks {c['tracks_opened']:>4} "
              f"crops {c['crops']:>4} w {c['width_px']} bright {c['brightness_mean']} restarts {c['restarts']}")
    return 0 if len(run_rows) else 1


if __name__ == "__main__":
    sys.exit(main())
