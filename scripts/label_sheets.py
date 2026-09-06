#!/usr/bin/env python3
"""Contact sheets for hand-labelling the crop bank: the widest crops, upscaled, numbered.

    docker run --rm -v $PWD/media:/app/media -v $PWD/scripts:/app/scripts:ro --entrypoint python \\
        sentinel-anpr:cpu /app/scripts/label_sheets.py --bank /app/media/anpr_crops --top 60 --per-sheet 10

Writes ``<bank>/label_sheets/sheet_<n>.jpg`` (each crop upscaled x4 with cubic interpolation, a
CLAHE-enhanced twin next to it, and an index) plus ``<bank>/label_sheets/index.csv`` mapping
index -> file, so a labeller can open the sheets, read what is legible and fill in
``<bank>/labels.csv`` (``file,plate,note``). Only label what you can actually read.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, "/app")
from anpr.ocr import enhance_crop  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bank", default="/app/media/anpr_crops")
    parser.add_argument("--top", type=int, default=60)
    parser.add_argument("--per-sheet", type=int, default=10)
    parser.add_argument("--min-w", type=int, default=40)
    parser.add_argument("--scale", type=float, default=4.0)
    parser.add_argument("--max-w", type=int, default=560)
    args = parser.parse_args(argv)
    bank = Path(args.bank)
    with (bank / "manifest.csv").open("r", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if int(r["width_px"]) >= args.min_w]
    rows.sort(key=lambda r: -int(r["width_px"]))
    rows = rows[: args.top]
    out = bank / "label_sheets"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "index.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["index", "file", "width_px", "camera", "ts_utc", "sheet"])
        for i in range(0, len(rows), args.per_sheet):
            chunk = rows[i:i + args.per_sheet]
            tiles = []
            for j, r in enumerate(chunk):
                idx = i + j + 1
                img = cv2.imread(str(bank / r["file"]))
                if img is None:
                    continue
                scale = min(args.scale, args.max_w / max(1, img.shape[1]))
                big = cv2.resize(img, (int(img.shape[1] * scale), int(img.shape[0] * scale)), interpolation=cv2.INTER_CUBIC)
                enh, _ = enhance_crop(img, upscale=scale, max_width=args.max_w, pad=0)
                enh = cv2.resize(enh, (big.shape[1], big.shape[0]))
                pair = np.hstack([big, np.full((big.shape[0], 8, 3), 60, np.uint8), enh])
                strip = np.full((26, pair.shape[1], 3), 20, np.uint8)
                cv2.putText(strip, f"#{idx}  {r['external_id']}  {r['width_px']}px  {r['ts_utc'][11:19]}Z  b{int(float(r['brightness']))}", (4, 18),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
                tiles.append(np.vstack([strip, pair]))
                w.writerow([idx, r["file"], r["width_px"], r["external_id"], r["ts_utc"], f"sheet_{i // args.per_sheet + 1}.jpg"])
            if not tiles:
                continue
            width = max(t.shape[1] for t in tiles)
            tiles = [cv2.copyMakeBorder(t, 0, 6, 0, width - t.shape[1], cv2.BORDER_CONSTANT, value=(20, 20, 20)) for t in tiles]
            cv2.imwrite(str(out / f"sheet_{i // args.per_sheet + 1}.jpg"), np.vstack(tiles), [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    print(f"{len(rows)} crops -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
