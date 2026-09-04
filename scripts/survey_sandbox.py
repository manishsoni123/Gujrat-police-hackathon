#!/usr/bin/env python3
"""Survey every catalogue camera: one frame each, a contact sheet and a CSV/JSON summary.

Used in hour one of Day 1 to choose the 8-12 ``live`` (ANPR) cameras (plan 5.5 step 12) and
usable against the mock sandbox. Runs inside the ``anpr`` image (ffmpeg + OpenCV available):

    python scripts/survey_sandbox.py --api http://api:8000 --key <internal key> --out /data/survey
    python scripts/survey_sandbox.py --catalogue http://api:8000/mock-sandbox/api/ingest --out /tmp/survey

Camera sources:
  --api        GET /api/internal/anpr-config?mode=preindex (every non-retired camera with an RTSP URL)
  --catalogue  the raw organiser/mock catalogue (bare list or wrapper object; id/name/rtsp_url fields)
  --rtsp       one or more explicit RTSP URLs (id = position)

Outputs in --out:
  <id>.jpg               first decodable frame (10 s timeout) per camera
  survey.csv             camera_id, external_id, name, ok, width, height, codec, seconds_to_first_frame, error
  survey.json            the same rows plus fps and mean brightness (0-255) of the frame
  contact_sheet.jpg      thumbnails with id/name/resolution captions
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import requests

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"
WRAPPER_KEYS = ("cameras", "data", "items", "results", "streams")


def load_from_api(api: str, key: str, timeout: float) -> list[dict[str, Any]]:
    resp = requests.get(f"{api.rstrip('/')}/api/internal/anpr-config", params={"mode": "preindex"},
                        headers={"X-API-Key": key}, timeout=timeout)
    resp.raise_for_status()
    return [{"id": c["id"], "external_id": c.get("external_id"), "name": c.get("name"), "rtsp_url": c.get("rtsp_url")}
            for c in resp.json().get("cameras", [])]


def load_from_catalogue(url: str, timeout: float, auth: tuple[str, str] | None) -> list[dict[str, Any]]:
    resp = requests.get(url, timeout=timeout, auth=auth)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        for k in WRAPPER_KEYS:
            if isinstance(data.get(k), list):
                data = data[k]
                break
    if not isinstance(data, list):
        raise SystemExit("catalogue is neither a list nor a wrapper object with a list")
    rows = []
    for i, item in enumerate(data, 1):
        rtsp = item.get("rtsp_url") or item.get("rtsp") or (item.get("urls") or {}).get("rtsp") or (item.get("streams") or {}).get("rtsp")
        rows.append({"id": i, "external_id": str(item.get("id", i)), "name": item.get("name") or f"camera {i}", "rtsp_url": rtsp})
    return rows


def probe(url: str, timeout: float) -> dict[str, Any]:
    cmd = [FFPROBE, "-v", "error", "-rtsp_transport", "tcp", "-timeout", str(int(timeout * 1_000_000)),
           "-select_streams", "v:0", "-show_entries", "stream=width,height,codec_name,avg_frame_rate", "-of", "json", url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5, check=False)
        streams = json.loads(out.stdout or "{}").get("streams") or []
        if streams:
            s = streams[0]
            num, _, den = (s.get("avg_frame_rate") or "0/1").partition("/")
            fps = float(num) / float(den or 1) if float(den or 1) else 0.0
            codec = {"h264": "H264", "hevc": "H265", "mjpeg": "MJPEG"}.get(str(s.get("codec_name", "")).lower(), str(s.get("codec_name", "")).upper())
            return {"width": s.get("width"), "height": s.get("height"), "codec": codec, "fps": round(fps, 2)}
        return {"error": (out.stderr or "no video stream").strip()[-200:]}
    except (subprocess.TimeoutExpired, ValueError) as exc:
        return {"error": f"ffprobe: {exc.__class__.__name__}"}


def grab_frame(url: str, dest: Path, timeout: float) -> tuple[bool, float, str]:
    cmd = [FFMPEG, "-y", "-nostdin", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
           "-timeout", str(int(timeout * 1_000_000)), "-i", url, "-frames:v", "1", "-q:v", "3", str(dest)]
    t0 = time.monotonic()
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5, check=False)
    except subprocess.TimeoutExpired:
        return False, timeout, "timeout"
    took = time.monotonic() - t0
    if out.returncode != 0 or not dest.exists():
        return False, took, (out.stderr or f"ffmpeg exit {out.returncode}").strip()[-200:]
    return True, took, ""


def survey_one(row: dict[str, Any], out_dir: Path, timeout: float) -> dict[str, Any]:
    result = {"camera_id": row["id"], "external_id": row.get("external_id"), "name": row.get("name"), "rtsp_url": row.get("rtsp_url"),
              "ok": False, "width": None, "height": None, "codec": None, "fps": None, "seconds_to_first_frame": None,
              "brightness": None, "error": ""}
    if not row.get("rtsp_url"):
        result["error"] = "no rtsp_url"
        return result
    dest = out_dir / f"{row['id']}.jpg"
    ok, took, err = grab_frame(row["rtsp_url"], dest, timeout)
    result["seconds_to_first_frame"] = round(took, 2)
    if not ok:
        result["error"] = err
        return result
    img = cv2.imread(str(dest))
    if img is None:
        result["error"] = "frame not decodable"
        return result
    result.update({"ok": True, "height": int(img.shape[0]), "width": int(img.shape[1]),
                   "brightness": round(float(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).mean()), 1)})
    info = probe(row["rtsp_url"], timeout)
    result["codec"] = info.get("codec")
    result["fps"] = info.get("fps")
    if info.get("width"):
        result["width"], result["height"] = info["width"], info["height"]
    return result


def contact_sheet(rows: list[dict[str, Any]], out_dir: Path, cols: int = 5, tile: tuple[int, int] = (320, 180)) -> None:
    tiles = []
    for r in rows:
        img = cv2.imread(str(out_dir / f"{r['camera_id']}.jpg")) if r["ok"] else None
        if img is None:
            img = np.full((tile[1], tile[0], 3), 40, dtype=np.uint8)
            cv2.putText(img, "no frame", (100, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 220), 2)
        else:
            img = cv2.resize(img, tile, interpolation=cv2.INTER_AREA)
        caption = f"{r['camera_id']} {str(r.get('name') or '')[:26]} {r.get('width') or ''}x{r.get('height') or ''} {r.get('codec') or ''}"
        cv2.rectangle(img, (0, tile[1] - 22), (tile[0], tile[1]), (0, 0, 0), -1)
        cv2.putText(img, caption, (4, tile[1] - 7), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        tiles.append(img)
    if not tiles:
        return
    while len(tiles) % cols:
        tiles.append(np.zeros((tile[1], tile[0], 3), dtype=np.uint8))
    grid = np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)])
    cv2.imwrite(str(out_dir / "contact_sheet.jpg"), grid, [int(cv2.IMWRITE_JPEG_QUALITY), 80])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--api", help="Sentinel API base URL, e.g. http://api:8000 (needs --key)")
    src.add_argument("--catalogue", help="catalogue URL returning the camera list, e.g. http://<host>/api/ingest")
    src.add_argument("--rtsp", nargs="+", help="explicit RTSP URLs")
    parser.add_argument("--key", default=os.environ.get("INTERNAL_API_KEY", ""), help="internal API key for --api")
    parser.add_argument("--user", default=os.environ.get("SANDBOX_USERNAME", ""), help="basic-auth user for --catalogue")
    parser.add_argument("--password", default=os.environ.get("SANDBOX_PASSWORD", ""), help="basic-auth password for --catalogue")
    parser.add_argument("--out", default="/data/survey")
    parser.add_argument("--timeout", type=float, default=10.0, help="seconds per camera for the first frame")
    parser.add_argument("--parallel", type=int, default=4)
    args = parser.parse_args(argv)

    if args.api:
        if not args.key:
            parser.error("--api needs --key (or INTERNAL_API_KEY)")
        rows = load_from_api(args.api, args.key, 30)
    elif args.catalogue:
        auth = (args.user, args.password) if args.user else None
        rows = load_from_catalogue(args.catalogue, 30, auth)
    else:
        rows = [{"id": i, "external_id": str(i), "name": f"rtsp {i}", "rtsp_url": u} for i, u in enumerate(args.rtsp, 1)]
    if not rows:
        print("no cameras found", file=sys.stderr)
        return 1
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"surveying {len(rows)} cameras with {args.parallel} workers, {args.timeout:.0f} s timeout each")
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        results = list(pool.map(lambda r: survey_one(r, out_dir, args.timeout), rows))
    results.sort(key=lambda r: r["camera_id"])

    with (out_dir / "survey.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["camera_id", "external_id", "name", "ok", "width", "height", "codec", "seconds_to_first_frame", "error"])
        for r in results:
            writer.writerow([r["camera_id"], r["external_id"], r["name"], str(r["ok"]).lower(), r["width"], r["height"],
                             r["codec"], r["seconds_to_first_frame"], r["error"]])
    (out_dir / "survey.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    contact_sheet(results, out_dir)

    ok = [r for r in results if r["ok"]]
    for r in results:
        flag = "ok " if r["ok"] else "-- "
        print(f"{flag}{r['camera_id']:>4} {str(r['name'])[:40]:<40} {r.get('width') or '':>5}x{r.get('height') or '':<5} "
              f"{r.get('codec') or '':<6} {r.get('seconds_to_first_frame') or '':>5}s bright={r.get('brightness') or ''} {r['error'][:60]}")
    print(f"\n{len(ok)}/{len(results)} cameras delivered a frame in {time.monotonic() - t0:.0f} s -> {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
