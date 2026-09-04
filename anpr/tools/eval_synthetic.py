#!/usr/bin/env python3
"""Accuracy and throughput check of the worker against the synthetic videos (CONTRACT.md 12.4).

Runs ``python -m anpr.pipeline`` in dry-run mode on one or more ``cam_<n>.mp4`` files, collects the
voted reads it would have POSTed, and scores them against ``plates.json``:

* an appearance is *matched* when a read with exactly its ``plate_norm`` has a ``stream_pts``
  inside ``[start_s - 1, end_s + vote_window + 1]`` on that camera;
* *false reads* are valid-format reads that match no appearance (wrong plate or wrong time);
* throughput is the worker's own ``stats:`` line (frames processed per second of wall clock).

    python -m anpr.tools.eval_synthetic --media /media/synthetic --cameras 1 --detector contour
    python -m anpr.tools.eval_synthetic --media /media/synthetic --cameras 1 2 3 --no-realtime   # CPU capacity

Exit code 0 when every camera reaches ``--min-accuracy`` (default 0.9), 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

STATS_RE = re.compile(r"frames=(\d+) \(([\d.]+)/s\) detect=([\d.]+) ms/frame ocr=([\d.]+) ms/call \((\d+)\) objects=([\d.]+) ms/call \((\d+)\)")


def run_worker(sources: dict[int, Path], detector: str, realtime: bool, objects: bool, extra_env: dict[str, str], timeout_s: float) -> tuple[list[dict], list[str], float]:
    cmd = [sys.executable, "-m", "anpr.pipeline", "--dry-run", "--detector", detector, "--log-level", "INFO"]
    for cid, path in sources.items():
        cmd += ["--source", f"{cid}={path}"]
    if not realtime:
        cmd.append("--no-realtime")
    if not objects:
        cmd.append("--no-objects")
    env = {**os.environ, "ANPR_MAX_CAMERAS": str(max(len(sources), 1)), **extra_env}
    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, env=env, check=False)
    elapsed = time.monotonic() - t0
    payloads: list[dict] = []
    logs: list[str] = []
    for line in proc.stdout.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("dry_run") and obj.get("endpoint") == "/internal/detections":
            payloads.append(obj["payload"])
        elif "msg" in obj:
            logs.append(obj["msg"])
    if proc.returncode != 0:
        print(f"worker exited with {proc.returncode}\n{proc.stderr[-2000:]}", file=sys.stderr)
    return payloads, logs, elapsed


def score(camera_id: int, payloads: list[dict], plates: dict, vote_window: float) -> dict:
    apps = [a for a in plates["appearances"] if a["camera_id"] == camera_id]
    reads = [r for p in payloads if p["camera_id"] == camera_id for r in p["reads"]]
    matched, missed, details = 0, [], []
    used: set[int] = set()
    for a in apps:
        lo, hi = a["start_s"] - 1.0, a["end_s"] + vote_window + 1.0
        hit = None
        for i, r in enumerate(reads):
            if i in used:
                continue
            if r["plate_norm"] == a["plate"] and lo <= r["stream_pts"] <= hi:
                hit = i
                break
        if hit is None:
            near = [r["plate_norm"] for r in reads if lo <= r["stream_pts"] <= hi]
            missed.append({"plate": a["plate"], "start_s": a["start_s"], "two_line": a["two_line"], "reads_in_window": near})
        else:
            used.add(hit)
            matched += 1
            details.append({"plate": a["plate"], "start_s": a["start_s"], "pts": reads[hit]["stream_pts"], "conf": reads[hit]["confidence"]})
    false_reads = [
        {"plate_norm": r["plate_norm"], "pts": r["stream_pts"], "conf": r["confidence"], "valid": r["is_valid_format"]}
        for i, r in enumerate(reads) if i not in used and not any(
            r["plate_norm"] == a["plate"] and a["start_s"] - 1.0 <= r["stream_pts"] <= a["end_s"] + vote_window + 1.0 for a in apps)
    ]
    return {
        "camera_id": camera_id, "appearances": len(apps), "matched": matched,
        "accuracy": matched / len(apps) if apps else 0.0, "reads": len(reads), "false_reads": false_reads,
        "missed": missed, "matched_details": details,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--media", default=os.environ.get("SYNTH_OUT", "/media/synthetic"))
    parser.add_argument("--cameras", type=int, nargs="+", default=[1])
    parser.add_argument("--detector", default="contour", choices=["auto", "onnx", "contour"])
    parser.add_argument("--no-realtime", action="store_true", help="decode as fast as possible (throughput measurement)")
    parser.add_argument("--objects", action="store_true", help="keep YOLOX counting on (default off for a clean OCR number)")
    parser.add_argument("--min-accuracy", type=float, default=0.9)
    parser.add_argument("--vote-window", type=float, default=float(os.environ.get("ANPR_VOTE_WINDOW_S", "3")))
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--json", help="write the full result to this file")
    args = parser.parse_args(argv)

    media = Path(args.media)
    plates = json.loads((media / "plates.json").read_text(encoding="utf-8"))
    sources = {cid: media / f"cam_{cid}.mp4" for cid in args.cameras}
    for path in sources.values():
        if not path.exists():
            print(f"missing {path}", file=sys.stderr)
            return 2
    print(f"running worker: detector={args.detector} realtime={not args.no_realtime} cameras={args.cameras}")
    payloads, logs, elapsed = run_worker(sources, args.detector, not args.no_realtime, args.objects, {}, args.timeout)
    stats = [m for m in (STATS_RE.search(l) for l in logs) if m]
    last = stats[-1] if stats else None
    results = [score(cid, payloads, plates, args.vote_window) for cid in args.cameras]
    ok = True
    for r in results:
        flag = "PASS" if r["accuracy"] >= args.min_accuracy else "FAIL"
        ok &= r["accuracy"] >= args.min_accuracy
        print(f"cam {r['camera_id']}: {r['matched']}/{r['appearances']} appearances read exactly "
              f"({r['accuracy'] * 100:.1f} %), {r['reads']} voted reads, {len(r['false_reads'])} false -> {flag}")
        for m in r["missed"]:
            print(f"    missed {m['plate']} @ {m['start_s']}s two_line={m['two_line']} reads_in_window={m['reads_in_window']}")
        for f in r["false_reads"][:10]:
            print(f"    false  {f['plate_norm']} @ {f['pts']}s conf={f['conf']} valid={f['valid']}")
    if last:
        print(f"throughput: {last.group(1)} frames processed at {last.group(2)} frames/s wall clock; "
              f"detect {last.group(3)} ms/frame, OCR {last.group(4)} ms/call x{last.group(5)}, "
              f"objects {last.group(6)} ms/call x{last.group(7)}; run took {elapsed:.0f} s")
    else:
        print(f"no stats line captured; run took {elapsed:.0f} s")
    if args.json:
        Path(args.json).write_text(json.dumps({"results": results, "stats": last.groupdict() if last else None,
                                               "stats_line": last.group(0) if last else None, "elapsed_s": elapsed,
                                               "detector": args.detector, "realtime": not args.no_realtime}, indent=2), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
